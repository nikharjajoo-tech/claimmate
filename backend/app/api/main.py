"""HTTP + WebSocket API.

    uvicorn app.api.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import logging
import os
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any, AsyncContextManager

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.observability import configure_logging
from app.live.relay import LiveRelay, LiveSession
from app.live.tools import TOOL_NAMES, build_live_config
from app.llm.client import StructuredLLM
from app.llm.factory import make_pipeline_llm, make_vision_llm
from app.pipeline.graph import build_graph, run_pipeline
from app.services.evidence import capture_summary, verify_and_record
from app.api.adjuster import adjuster_router
from app.api.security import RateLimiter, SecurityMiddleware
from app.services.packet_zip import build_packet_zip
from app.services.review_runner import ReviewRunner
from app.services.sessions import ClaimService, SessionError
from app.services.store import SessionStore
from app.storage.db import make_engine, make_sessionmaker
from app.storage.migrate import upgrade_to_head
from app.storage.repository import ClaimRepository
from app.services.view import session_view

logger = logging.getLogger(__name__)

OWNER_COOKIE = "claimvoice_owner"
FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
DEFAULT_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"


class EvidenceIn(BaseModel):
    data: str = Field(description="Base64 JPEG", max_length=2_100_000)
    claim: str = Field(default="", max_length=500)


MAX_BATCH_PHOTOS = 10


class EvidenceBatchIn(BaseModel):
    photos: list[EvidenceIn] = Field(min_length=1, max_length=MAX_BATCH_PHOTOS)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    id: str | None = Field(default=None, max_length=64)


def lazy_pipeline_runner(settings: Settings):
    """Builds the LLM and graph on first use, so the server starts even before keys are set."""
    state: dict[str, Any] = {}

    async def runner(turns, **kwargs):
        if "graph" not in state:
            llm = make_pipeline_llm(settings)
            state["llm"], state["graph"] = llm, build_graph(llm, settings.pipeline_mode, settings.prompt_version)
        return await run_pipeline(state["llm"], turns, graph=state["graph"], **kwargs)

    return runner


def gemini_connect_factory(settings: Settings) -> Callable[[], AsyncContextManager[LiveSession]]:
    def connect():
        from google import genai

        client = genai.Client(api_key=settings.google_api_key)
        return client.aio.live.connect(model=settings.live_model, config=build_live_config())

    return connect


def create_app(
    *,
    settings: Settings | None = None,
    store: SessionStore | None = None,
    service: ClaimService | None = None,
    live_connect: Callable[[], AsyncContextManager[LiveSession]] | None = None,
    vision: StructuredLLM | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    engine = None
    if store is None:  # production wiring: SQLite (or any SQLAlchemy URL) plus evidence files on disk
        engine = make_engine(settings.database_url, turso_auth_token=settings.turso_auth_token)
        repo = ClaimRepository(
            make_sessionmaker(engine), settings.evidence_dir, images_in_database=settings.evidence_in_database
        )
        # The wording review uses the pipeline's provider chain, built on first use like the pipeline.
        store = SessionStore(repo, ReviewRunner(repo, lambda: make_pipeline_llm(settings)))
    service = service or ClaimService(lazy_pipeline_runner(settings))
    live_connect = live_connect or gemini_connect_factory(settings)
    allowed_origins = [o.strip() for o in os.getenv("CLAIMVOICE_ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()]
    if os.getenv("RENDER_EXTERNAL_URL"):  # set by Render to the service's public https URL
        allowed_origins.append(os.environ["RENDER_EXTERNAL_URL"].rstrip("/"))

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        if engine is not None:
            await asyncio.to_thread(upgrade_to_head, settings.database_url, settings.turso_auth_token)

        async def sweep_forever():
            while True:
                await asyncio.sleep(60)
                await store.sweep()  # idle intakes are submitted to the adjuster queue

        sweeper = asyncio.create_task(sweep_forever())
        try:
            yield
        finally:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
            if store.reviews is not None:
                await store.reviews.drain()  # let in-flight wording reviews finish writing
            if engine is not None:
                await engine.dispose()

    app = FastAPI(title="ClaimMate API", lifespan=lifespan)
    app.add_middleware(SecurityMiddleware)
    create_limiter = RateLimiter(limit=10, window_s=60)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.state.store, app.state.service = store, service
    vision_holder: dict[str, StructuredLLM | None] = {"llm": vision}

    def get_vision() -> StructuredLLM | None:
        if vision_holder["llm"] is None and (settings.groq_api_key or settings.has_api_key):
            vision_holder["llm"] = make_vision_llm(settings)
        return vision_holder["llm"]

    @app.exception_handler(SessionError)
    async def session_error(_request: Request, exc: SessionError):
        return PlainTextResponse(str(exc), status_code=exc.status)

    def owner_of(request: Request) -> str | None:
        return request.cookies.get(OWNER_COOKIE)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "pipeline_provider": settings.pipeline_provider,
            "groq_configured": bool(settings.groq_api_key),
            "gemini_configured": settings.has_api_key,
            "live_model": settings.live_model,
            "tools": TOOL_NAMES,
            "sessions": len(store),
            # Set by Render; lets CI confirm a deploy actually went live.
            "commit": os.getenv("RENDER_GIT_COMMIT", "")[:7] or None,
        }

    @app.post("/api/claims", status_code=201)
    async def create_claim(request: Request, response: Response) -> dict[str, Any]:
        if not create_limiter.allow(request.client.host if request.client else "unknown"):
            raise HTTPException(429, "Too many new claims from this device. Wait a minute and try again.")
        owner = owner_of(request) or secrets.token_urlsafe(32)
        session = await store.create(owner)
        response.set_cookie(
            OWNER_COOKIE, owner, httponly=True, samesite="strict", secure=request.url.scheme == "https", max_age=24 * 3600
        )
        return {"id": session.id, "state": session_view(session)}

    @app.get("/api/claims/{claim_id}")
    async def get_claim(claim_id: str, request: Request) -> dict[str, Any]:
        return {"id": claim_id, "state": session_view(await store.get(claim_id, owner_of(request), readonly=True))}

    @app.delete("/api/claims/{claim_id}")
    async def finish_claim(claim_id: str, request: Request) -> dict[str, str]:
        """Claimant starts over. A claim with content is submitted for review, never thrown away."""
        session = await store.get(claim_id, owner_of(request))
        if session.live_connected:
            raise HTTPException(409, "End the live call first.")
        await store.finish(session, "claimant started a new claim")
        return {"status": "submitted" if session.status == "submitted" else "discarded"}

    @app.post("/api/claims/{claim_id}/messages")
    async def post_message(claim_id: str, body: MessageIn, request: Request) -> dict[str, Any]:
        """Typed mode: no microphone or Live quota needed. The agent replies with the next question."""
        session = await store.get(claim_id, owner_of(request))
        if session.live_connected:
            raise HTTPException(409, "A live call is active; send text through the call instead.")
        if session.add_turn("claimant", body.text, turn_id=body.id) is None:
            return {"id": claim_id, "state": session_view(session)}  # duplicate delivery
        await store.save(session)  # the message is durable before any model call
        try:
            result = await service.refresh(session)
        except Exception as exc:
            logger.exception("pipeline failed")
            raise HTTPException(503, "The claims team is unavailable right now. Your message was saved.") from exc
        session.add_turn("agent", result.packet.next_question)
        await store.save(session)
        return {"id": claim_id, "state": session_view(session)}

    @app.post("/api/claims/{claim_id}/evidence", status_code=201)
    async def upload_evidence(claim_id: str, body: EvidenceIn, request: Request) -> dict[str, Any]:
        """Photo upload for typed mode (no live camera needed). Verified like a camera capture."""
        session = await store.get(claim_id, owner_of(request))
        try:
            image = base64.b64decode(body.data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(422, "Image must be base64.") from exc
        vision_llm = get_vision()
        if vision_llm is None:
            raise HTTPException(503, "Photo verification is not configured on the server.")
        try:
            result = await verify_and_record(session, vision_llm, image, claimant_claim=body.claim, source="upload")
        except SessionError:
            raise  # limits and validation keep their own status codes via the handler above
        except Exception as exc:
            logger.exception("evidence verification failed")
            raise HTTPException(503, "Photo verification is unavailable right now. Try again shortly.") from exc
        try:
            await service.refresh(session)
        except Exception:
            logger.exception("pipeline failed after evidence upload")
        summary = capture_summary(result.capture)
        session.add_turn("agent", f"Thanks, I've added that photo. I can see: {result.capture.caption}")
        await store.save(session)
        return {"id": claim_id, "capture": summary, "state": session_view(session)}

    @app.post("/api/claims/{claim_id}/evidence/batch", status_code=201)
    async def upload_evidence_batch(claim_id: str, body: EvidenceBatchIn, request: Request) -> dict[str, Any]:
        """Several photos at once. Each is verified on its own; the pipeline runs once for the set.

        One bad photo does not fail the others: every photo gets its own result, in request order.
        """
        session = await store.get(claim_id, owner_of(request))
        vision_llm = get_vision()
        if vision_llm is None:
            raise HTTPException(503, "Photo verification is not configured on the server.")
        results: list[dict[str, Any]] = []
        added = []
        for photo in body.photos:
            try:
                image = base64.b64decode(photo.data, validate=True)
            except (binascii.Error, ValueError):
                results.append({"ok": False, "error": "Image must be base64."})
                continue
            try:
                result = await verify_and_record(session, vision_llm, image, claimant_claim=photo.claim, source="upload")
            except SessionError as exc:
                if session.deleted:
                    raise
                results.append({"ok": False, "error": str(exc)})  # e.g. not a JPEG, or the evidence limit
                continue
            except Exception:
                logger.exception("evidence verification failed")
                results.append({"ok": False, "error": "Photo verification is unavailable right now. Try again shortly."})
                continue
            added.append(result.capture)
            results.append({"ok": True, "capture": capture_summary(result.capture)})
        if added:
            try:
                await service.refresh(session)
            except Exception:
                logger.exception("pipeline failed after evidence upload")
            if len(added) == 1:
                session.add_turn("agent", f"Thanks, I've added that photo. I can see: {added[0].caption}")
            else:
                seen = " ".join(f"({i}) {capture.caption}" for i, capture in enumerate(added, 1))
                session.add_turn("agent", f"Thanks, I've added those {len(added)} photos. I can see: {seen}")
            await store.save(session)
        return {"id": claim_id, "results": results, "state": session_view(session)}

    @app.get("/api/claims/{claim_id}/evidence/{capture_id}")
    async def get_evidence(claim_id: str, capture_id: str, request: Request) -> Response:
        session = await store.get(claim_id, owner_of(request), readonly=True)
        image = session.evidence_images.get(capture_id)
        if image is None:
            raise HTTPException(404, "No such evidence.")
        return Response(image, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/claims/{claim_id}/packet")
    async def download_packet(claim_id: str, request: Request) -> Response:
        """ZIP with the packet, transcript, evidence manifest, and photos."""
        session = await store.get(claim_id, owner_of(request), readonly=True)
        if session.result is None:
            raise HTTPException(409, "No claim packet yet. Describe the loss first.")
        return Response(
            build_packet_zip(session),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="claim-{claim_id[:8]}.zip"'},
        )

    @app.websocket("/ws/claims/{claim_id}/live")
    async def live(websocket: WebSocket, claim_id: str) -> None:
        # Browsers always send Origin on WebSocket upgrades; cookies alone would allow cross-site hijacking.
        if websocket.headers.get("origin") not in allowed_origins:
            await websocket.close(code=1008)
            return
        try:
            session = await store.get(claim_id, websocket.cookies.get(OWNER_COOKIE))
        except SessionError:
            await websocket.close(code=1008)
            return
        if session.live_connected:
            await websocket.close(code=1008, reason="A live call is already open for this claim.")
            return
        await websocket.accept()
        if not settings.has_api_key:
            await websocket.send_json({"type": "error", "message": "GOOGLE_API_KEY is not set on the server."})
            await websocket.close()
            return
        relay = LiveRelay(
            session, service, websocket, live_connect, model_name=settings.live_model, vision=get_vision(), store=store
        )
        try:
            await relay.run()
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("live session failed")
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "error", "message": "The live connection ended. Reconnect to continue."})
        finally:
            with contextlib.suppress(Exception):
                await websocket.close()

    app.include_router(adjuster_router(settings, store))

    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app


configure_logging(os.getenv("CLAIMVOICE_LOG_LEVEL", "INFO"))
app = create_app()
