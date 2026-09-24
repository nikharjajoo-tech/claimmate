"""HTTP + WebSocket API.

    uvicorn app.api.main:app --reload --port 8000
"""

from __future__ import annotations

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
from app.live.relay import LiveRelay, LiveSession
from app.live.tools import TOOL_NAMES, build_live_config
from app.llm.factory import make_pipeline_llm
from app.pipeline.graph import build_graph, run_pipeline
from app.services.sessions import ClaimService, SessionError, SessionStore
from app.services.view import session_view

logger = logging.getLogger(__name__)

OWNER_COOKIE = "claimvoice_owner"
FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
DEFAULT_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    id: str | None = Field(default=None, max_length=64)


def lazy_pipeline_runner(settings: Settings):
    """Builds the LLM and graph on first use, so the server starts even before keys are set."""
    state: dict[str, Any] = {}

    async def runner(turns, **kwargs):
        if "graph" not in state:
            llm = make_pipeline_llm(settings)
            state["llm"], state["graph"] = llm, build_graph(llm)
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
) -> FastAPI:
    settings = settings or get_settings()
    store = store or SessionStore()
    service = service or ClaimService(lazy_pipeline_runner(settings))
    live_connect = live_connect or gemini_connect_factory(settings)
    allowed_origins = [o.strip() for o in os.getenv("CLAIMVOICE_ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()]

    app = FastAPI(title="ClaimVoice API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.state.store, app.state.service = store, service

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
        }

    @app.post("/api/claims", status_code=201)
    def create_claim(request: Request, response: Response) -> dict[str, Any]:
        owner = owner_of(request) or secrets.token_urlsafe(32)
        session = store.create(owner)
        response.set_cookie(
            OWNER_COOKIE, owner, httponly=True, samesite="strict", secure=request.url.scheme == "https", max_age=24 * 3600
        )
        return {"id": session.id, "state": session_view(session)}

    @app.get("/api/claims/{claim_id}")
    def get_claim(claim_id: str, request: Request) -> dict[str, Any]:
        return {"id": claim_id, "state": session_view(store.get(claim_id, owner_of(request)))}

    @app.delete("/api/claims/{claim_id}")
    def delete_claim(claim_id: str, request: Request) -> dict[str, bool]:
        store.delete(store.get(claim_id, owner_of(request)))
        return {"deleted": True}

    @app.post("/api/claims/{claim_id}/messages")
    async def post_message(claim_id: str, body: MessageIn, request: Request) -> dict[str, Any]:
        """Typed mode: no microphone or Live quota needed. The agent replies with the next question."""
        session = store.get(claim_id, owner_of(request))
        if session.live_connected:
            raise HTTPException(409, "A live call is active; send text through the call instead.")
        if session.add_turn("claimant", body.text, turn_id=body.id) is None:
            return {"id": claim_id, "state": session_view(session)}  # duplicate delivery
        try:
            result = await service.refresh(session)
        except Exception as exc:
            logger.exception("pipeline failed")
            raise HTTPException(503, "The claims team is unavailable right now. Your message was saved.") from exc
        session.add_turn("agent", result.packet.next_question)
        return {"id": claim_id, "state": session_view(session)}

    @app.get("/api/claims/{claim_id}/packet")
    def download_packet(claim_id: str, request: Request) -> Response:
        session = store.get(claim_id, owner_of(request))
        if session.result is None:
            raise HTTPException(409, "No claim packet yet. Describe the loss first.")
        return Response(
            session.result.packet.markdown,
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="claim-{claim_id[:8]}.md"'},
        )

    @app.websocket("/ws/claims/{claim_id}/live")
    async def live(websocket: WebSocket, claim_id: str) -> None:
        # Browsers always send Origin on WebSocket upgrades; cookies alone would allow cross-site hijacking.
        if websocket.headers.get("origin") not in allowed_origins:
            await websocket.close(code=1008)
            return
        try:
            session = store.get(claim_id, websocket.cookies.get(OWNER_COOKIE))
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
        relay = LiveRelay(session, service, websocket, live_connect, model_name=settings.live_model)
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

    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app


app = create_app()
