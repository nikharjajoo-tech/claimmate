import asyncio
import contextlib
import io
import zipfile
from dataclasses import replace
from datetime import date

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.main import OWNER_COOKIE, create_app
from app.config import get_settings
from app.services.sessions import ClaimService
from app.services.store import SessionStore
from app.storage.db import create_all, make_engine, make_sessionmaker
from app.storage.repository import ClaimRepository
from tests.fakes import fake_runner

ORIGIN = {"origin": "http://localhost:5173"}


class EchoLive:
    """Minimal live session: acknowledges typed text with an agent transcript."""

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()

    async def send_realtime_input(self, **_):
        pass

    async def send_client_content(self, *, turns, turn_complete=True):
        from google.genai import types

        if turn_complete:
            self.queue.put_nowait(types.LiveServerMessage(server_content=types.LiveServerContent(
                output_transcription=types.Transcription(text="Got it."), turn_complete=True)))

    async def send_tool_response(self, **_):
        pass

    async def receive(self):
        while True:
            yield await self.queue.get()


@contextlib.asynccontextmanager
async def echo_connect():
    yield EchoLive()


@pytest.fixture
def client(tmp_path):
    settings = replace(get_settings(), google_api_key="test")
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    asyncio.run(create_all(engine))
    app = create_app(
        settings=settings,
        store=SessionStore(ClaimRepository(make_sessionmaker(engine), tmp_path / "evidence")),
        service=ClaimService(fake_runner(), today=lambda: date(2026, 9, 24)),
        live_connect=echo_connect,
    )
    with TestClient(app) as c:
        yield c


def new_claim(client):
    response = client.post("/api/claims", headers=ORIGIN)
    assert response.status_code == 201
    return response.json()


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] and body["tools"] == ["lookup_policy", "update_claim", "escalate_to_human", "capture_evidence"]


def test_create_sets_owner_cookie_and_greets(client):
    claim = new_claim(client)
    assert client.cookies.get(OWNER_COOKIE)
    assert claim["state"]["transcript"][0]["speaker"] == "agent"
    assert client.get(f"/api/claims/{claim['id']}").status_code == 200


def test_other_owner_cannot_read_claim(client):
    claim = new_claim(client)
    client.cookies.clear()
    assert client.get(f"/api/claims/{claim['id']}").status_code == 404


def test_typed_mode_runs_pipeline_and_agent_replies(client):
    claim = new_claim(client)
    response = client.post(f"/api/claims/{claim['id']}/messages", json={"text": "Elena Brooks, HO-20417, basement flooded"})
    state = response.json()["state"]
    assert state["route"] == "needs_docs"
    assert [t["speaker"] for t in state["transcript"]] == ["agent", "claimant", "agent"]
    assert state["transcript"][-1]["text"] == state["next_question"]

    packet = client.get(f"/api/claims/{claim['id']}/packet")
    assert packet.status_code == 200 and packet.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(packet.content)).namelist()
    assert names == ["claim.md", "transcript.md", "evidence.json"]


def test_duplicate_message_id_is_idempotent(client):
    claim = new_claim(client)
    url = f"/api/claims/{claim['id']}/messages"
    client.post(url, json={"text": "flooded", "id": "m1"})
    state = client.post(url, json={"text": "flooded", "id": "m1"}).json()["state"]
    assert sum(t["speaker"] == "claimant" for t in state["transcript"]) == 1


def test_message_validation_and_packet_before_facts(client):
    claim = new_claim(client)
    assert client.post(f"/api/claims/{claim['id']}/messages", json={"text": ""}).status_code == 422
    assert client.get(f"/api/claims/{claim['id']}/packet").status_code == 409


def test_new_claim_discards_empty_and_submits_real_intakes(client):
    empty = new_claim(client)
    assert client.delete(f"/api/claims/{empty['id']}").json() == {"status": "discarded"}
    assert client.get(f"/api/claims/{empty['id']}").status_code == 404

    real = new_claim(client)
    client.post(f"/api/claims/{real['id']}/messages", json={"text": "Basement flooded"})
    assert client.delete(f"/api/claims/{real['id']}").json() == {"status": "submitted"}
    # D5: no more changes, but the claimant can still read their own claim and download the packet
    response = client.post(f"/api/claims/{real['id']}/messages", json={"text": "one more thing"})
    assert response.status_code == 410 and "submitted for review" in response.text
    assert client.get(f"/api/claims/{real['id']}").json()["state"]["status"] == "submitted"
    assert client.get(f"/api/claims/{real['id']}/packet").status_code == 200


def test_websocket_rejects_foreign_origin_and_wrong_owner(client):
    claim = new_claim(client)
    url = f"/ws/claims/{claim['id']}/live"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(url, headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/claims/nope/live", headers=ORIGIN) as ws:
            ws.receive_json()


def test_websocket_live_round_trip(client):
    claim = new_claim(client)
    with client.websocket_connect(f"/ws/claims/{claim['id']}/live", headers=ORIGIN) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "text", "text": "Elena Brooks, HO-20417, basement flooded"})
        seen, route = [], None
        while route is None or "Got it." not in seen:
            message = ws.receive_json()
            if message["type"] == "transcript" and message["final"]:
                seen.append(message["text"])
            if message["type"] == "state":
                route = message["state"]["route"] or route
        assert route == "needs_docs"
        # a second live connection to the same claim is refused while this one is open
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/claims/{claim['id']}/live", headers=ORIGIN) as ws2:
                ws2.receive_json()
        ws.send_json({"type": "close"})
