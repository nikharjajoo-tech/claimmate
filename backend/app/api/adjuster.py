"""Adjuster API (workflow 6): passcode sign-in, queue, claim detail, lifecycle actions, override."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.config import Settings
from app.services import lifecycle
from app.services.packet_zip import build_packet_zip
from app.services.store import SessionStore
from app.services.view import session_view
from app.storage.repository import operations_metrics

ADJUSTER_COOKIE = "claimvoice_adjuster"
SESSION_HOURS = 8
LOGIN_WINDOW_S, LOGIN_MAX_ATTEMPTS = 60.0, 5
OPEN_STATUSES = {"intake", "submitted", "in_review", "awaiting_docs"}


class LoginIn(BaseModel):
    passcode: str = Field(max_length=200)


class StatusIn(BaseModel):
    status: str
    note: str = Field(default="", max_length=1000)


class OverrideIn(BaseModel):
    route: str
    reason: str = Field(max_length=1000)


def _sign(secret: str, expires: int) -> str:
    return hmac.new(secret.encode(), f"adjuster:{expires}".encode(), hashlib.sha256).hexdigest()


def make_token(secret: str, now: float | None = None) -> str:
    expires = int((now or time.time()) + SESSION_HOURS * 3600)
    return f"{expires}.{_sign(secret, expires)}"


def token_valid(secret: str, token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    expires_text, signature = token.split(".", 1)
    if not expires_text.isdigit() or int(expires_text) < (now or time.time()):
        return False
    return hmac.compare_digest(signature, _sign(secret, int(expires_text)))


def adjuster_router(settings: Settings, store: SessionStore) -> APIRouter:
    router = APIRouter(prefix="/api/adjuster", tags=["adjuster"])
    attempts: dict[str, deque[float]] = defaultdict(deque)

    def require_enabled() -> None:
        if not settings.adjuster_passcode:
            raise HTTPException(503, "The adjuster view is disabled. Set CLAIMVOICE_ADJUSTER_PASSCODE in .env.")
        if store.repo is None:
            raise HTTPException(503, "The adjuster view needs the database.")

    def require_adjuster(request: Request) -> None:
        require_enabled()
        if not token_valid(settings.session_secret, request.cookies.get(ADJUSTER_COOKIE)):
            raise HTTPException(401, "Sign in to the adjuster view.")

    @router.get("/me")
    def me(request: Request) -> dict[str, bool]:
        return {
            "enabled": bool(settings.adjuster_passcode) and store.repo is not None,
            "signed_in": token_valid(settings.session_secret, request.cookies.get(ADJUSTER_COOKIE)),
        }

    @router.post("/login", status_code=204)
    def login(body: LoginIn, request: Request, response: Response) -> None:
        require_enabled()
        client = request.client.host if request.client else "unknown"
        window, now = attempts[client], time.monotonic()
        while window and now - window[0] > LOGIN_WINDOW_S:
            window.popleft()
        if len(window) >= LOGIN_MAX_ATTEMPTS:
            raise HTTPException(429, "Too many sign-in attempts. Wait a minute and try again.")
        window.append(now)
        if not secrets.compare_digest(body.passcode.encode(), settings.adjuster_passcode.encode()):
            raise HTTPException(401, "Wrong passcode.")
        window.clear()
        response.set_cookie(
            ADJUSTER_COOKIE, make_token(settings.session_secret), httponly=True, samesite="strict",
            secure=request.url.scheme == "https", max_age=SESSION_HOURS * 3600, path="/api/adjuster",
        )

    @router.post("/logout", status_code=204)
    def logout(response: Response) -> None:
        response.delete_cookie(ADJUSTER_COOKIE, path="/api/adjuster")

    @router.get("/metrics", dependencies=[Depends(require_adjuster)])
    async def metrics() -> dict[str, Any]:
        """Live operations metrics (PRD section 8) computed from stored claims and runs."""
        return await operations_metrics(store.repo)

    @router.get("/claims", dependencies=[Depends(require_adjuster)])
    async def queue(status: str = "open", route: str | None = None, type: str | None = None) -> dict[str, Any]:
        statuses = OPEN_STATUSES if status == "open" else None if status == "all" else {status}
        if statuses is not None and not statuses <= set(lifecycle.STATUSES):
            raise HTTPException(422, f"Unknown status filter {status!r}.")
        items = await store.repo.queue(statuses=statuses, route=route, claim_type=type)
        live = store.live_claim_ids()
        for item in items:
            item["live"] = item["id"] in live
        return {"claims": items}

    async def detail(claim_id: str) -> dict[str, Any]:
        session = await store.get_for_adjuster(claim_id)
        state = session_view(session, evidence_url_prefix=f"/api/adjuster/claims/{claim_id}/evidence")
        return {"id": claim_id, "state": state, "audit": await store.repo.audit_log(claim_id)}

    @router.get("/claims/{claim_id}", dependencies=[Depends(require_adjuster)])
    async def get_claim(claim_id: str) -> dict[str, Any]:
        return await detail(claim_id)

    @router.post("/claims/{claim_id}/open", dependencies=[Depends(require_adjuster)])
    async def open_claim(claim_id: str) -> dict[str, Any]:
        """Opening a submitted claim starts review and freezes its route (decision D3)."""
        session = await store.get_for_adjuster(claim_id)
        if session.status == "submitted":
            lifecycle.transition(session, "in_review", actor="adjuster")
            await store.save(session)
        return await detail(claim_id)

    @router.post("/claims/{claim_id}/status", dependencies=[Depends(require_adjuster)])
    async def set_status(claim_id: str, body: StatusIn) -> dict[str, Any]:
        if body.status not in lifecycle.ADJUSTER_STATUSES:
            raise HTTPException(422, "Adjusters can set in_review, awaiting_docs, or closed.")
        session = await store.get_for_adjuster(claim_id)
        lifecycle.transition(session, body.status, actor="adjuster", note=body.note)
        await store.save(session)
        return await detail(claim_id)

    @router.post("/claims/{claim_id}/override", dependencies=[Depends(require_adjuster)])
    async def override(claim_id: str, body: OverrideIn) -> dict[str, Any]:
        session = await store.get_for_adjuster(claim_id)
        lifecycle.override_route(session, body.route, body.reason)
        await store.save(session)
        return await detail(claim_id)

    @router.get("/claims/{claim_id}/evidence/{capture_id}", dependencies=[Depends(require_adjuster)])
    async def evidence(claim_id: str, capture_id: str) -> Response:
        session = await store.get_for_adjuster(claim_id)
        image = session.evidence_images.get(capture_id)
        if image is None:
            raise HTTPException(404, "No such evidence.")
        return Response(image, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @router.get("/claims/{claim_id}/packet.zip", dependencies=[Depends(require_adjuster)])
    async def packet(claim_id: str) -> Response:
        session = await store.get_for_adjuster(claim_id)
        if session.result is None:
            raise HTTPException(409, "No claim packet yet.")
        return Response(
            build_packet_zip(session), media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="claim-{claim_id[:8]}.zip"'},
        )

    return router
