import json
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from app.models.outfit import Outfit
from app.models.preview import OutfitPreview, PreviewFeedback
from app.services.photo_redraw_service import PhotoRedrawService
from tests.test_ai_telemetry import telemetry as telemetry
from tests.test_image_undo_replace import _jpeg_bytes
from tests.test_outfit_preview import preview_setup as preview_setup
from tests.test_outfit_preview import provider as provider
from tests.test_outfit_preview import upload

REAL_REQUEST = PhotoRedrawService.request


@pytest.fixture
async def comparison_setup(client, auth_headers, preview_setup, provider):
    await upload(client, auth_headers)
    assert (await client.post(preview_setup["url"], headers=auth_headers)).status_code == 200
    photo_url = preview_setup["url"].removesuffix("/preview") + "/photo"
    assert (
        await client.put(
            photo_url,
            headers=auth_headers,
            files={"image": ("photo.jpg", _jpeg_bytes(), "image/jpeg")},
        )
    ).status_code == 200
    return preview_setup


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "not json",
        "{}",
        "[]",
        "[null]",
        '["ok", 1]',
        json.dumps(["long" * 40]),
        json.dumps(["fine"] * 5),
    ],
)
async def test_malformed_comparison_is_clear_error(
    client, auth_headers, comparison_setup, provider, reply
):
    provider.return_value = {"candidates": [{"content": {"parts": [{"text": reply}]}}]}
    result = await client.post(comparison_setup["url"] + "/compare", headers=auth_headers)
    assert result.status_code == 502, result.text
    assert result.json() == {"detail": "COMPARISON_FAILED"}


async def test_comparison_missing_and_cross_user_never_calls(
    client, auth_headers, preview_setup, provider, db_session
):
    result = await client.post(preview_setup["url"] + "/compare", headers=auth_headers)
    assert result.status_code == 409
    outfit = await db_session.get(Outfit, preview_setup["outfit"])
    from app.models.user import User

    other = User(
        display_name="Other", id=uuid4(), external_id=str(uuid4()), email=f"{uuid4()}@example.com"
    )
    db_session.add(other)
    await db_session.flush()
    outfit.user_id = other.id
    await db_session.commit()
    result = await client.post(preview_setup["url"] + "/compare", headers=auth_headers)
    assert result.status_code == 404
    provider.assert_not_called()


async def test_comparison_links_both_calls_and_preserves_like_state(
    client, auth_headers, comparison_setup, provider, db_session, telemetry, test_user
):
    setup = comparison_setup
    before = await db_session.get(OutfitPreview, setup["outfit"])
    corrected_call = uuid4()
    before.usage = {"_ai_call_id": str(corrected_call)}
    await db_session.commit()
    response = httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": '["**Shorten** the shirt hem", "Match the trouser color"]'}
                        ]
                    }
                }
            ]
        },
        request=httpx.Request("POST", "https://stub.test"),
    )
    with (
        patch.object(PhotoRedrawService, "request", new=REAL_REQUEST),
        patch("httpx.AsyncClient.post", AsyncMock(return_value=response)) as sent,
    ):
        from app.services.preview_compare_service import compare_preview

        data = await compare_preview(db_session, setup["outfit"], test_user.id)
    comparison_id = UUID(data["call_id"])
    assert data["note"] == "Shorten the shirt hem\nMatch the trouser color"
    parts = json.loads(sent.call_args.kwargs["content"])["contents"][0]["parts"]
    assert len([part for part in parts if "inlineData" in part]) == 2
    await telemetry.drain_recordings()
    exported = await client.get(f"/api/v1/ai-calls/{comparison_id}", headers=auth_headers)
    assert exported.status_code == 200, exported.text
    assert exported.json()["feature"] == "preview_photo_compare"
    assert exported.json()["feedback"][0]["kind"] == "preview_comparison"
    saved = await client.get(setup["url"], headers=auth_headers)
    assert saved.json()["liked"] is False
    invalid = await client.post(
        setup["url"] + "?regenerate=true",
        headers=auth_headers,
        json={"note": "Edited hem", "comparison_call_id": str(uuid4())},
    )
    assert invalid.status_code == 409
    result = await client.post(
        setup["url"] + "?regenerate=true",
        headers=auth_headers,
        json={"note": "Edited hem", "comparison_call_id": str(comparison_id)},
    )
    assert result.status_code == 200, result.text
    feedback = await db_session.scalar(
        select(PreviewFeedback).where(
            PreviewFeedback.outfit_id == setup["outfit"], PreviewFeedback.kind == "fix"
        )
    )
    assert feedback.note == "Edited hem"
    assert feedback.ai_call_id == corrected_call
    assert feedback.comparison_call_id == comparison_id
    assert "Shorten the shirt hem" not in provider.call_args.args[1]
    assert "Edited hem" in provider.call_args.args[1]


async def test_live_http_compare_edit_redraw_and_cross_user(
    client, auth_headers, comparison_setup, provider, db_session, telemetry, monkeypatch
):
    import asyncio
    import base64
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import uvicorn

    from app.api.auth import create_access_token
    from app.main import app
    from app.models.user import User
    from tests.identity_helpers import add_users_with_identities

    requests = []

    class Stub(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            modality = body["generationConfig"]["responseModalities"][0]
            parts = (
                [{"text": '["Shorten the hem"]'}]
                if modality == "TEXT"
                else [{"inlineData": {"data": base64.b64encode(_jpeg_bytes()).decode()}}]
            )
            payload = json.dumps({"candidates": [{"content": {"parts": parts}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    thread = threading.Thread(target=stub.serve_forever, daemon=True)
    thread.start()
    original_post = httpx.AsyncClient.post

    async def local_post(sender, url, *args, **kwargs):
        if str(url).startswith("https://generativelanguage.googleapis.com/"):
            url = f"http://127.0.0.1:{stub.server_port}/generate"
        return await original_post(sender, url, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", local_post)
    request_patch = patch.object(PhotoRedrawService, "request", REAL_REQUEST)
    request_patch.start()
    ready = asyncio.Event()

    class LiveServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            ready.set()

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(False)
    server = LiveServer(uvicorn.Config(app, lifespan="off", log_level="error"))
    running = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        await ready.wait()
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{listener.getsockname()[1]}"
        ) as live:
            url = comparison_setup["url"]
            compared = await live.post(url + "/compare", headers=auth_headers)
            assert compared.status_code == 200, compared.text
            assert compared.json()["note"] == "Shorten the hem"
            call_id = compared.json()["call_id"]
            redraw = await live.post(
                url + "?regenerate=true",
                headers=auth_headers,
                json={"note": "Shorten only the front hem", "comparison_call_id": call_id},
            )
            assert redraw.status_code == 200, redraw.text
            other = User(
                id=uuid4(),
                external_id=str(uuid4()),
                email=f"{uuid4()}@example.com",
                display_name="Second",
            )
            (identity,) = await add_users_with_identities(db_session, other)
            await db_session.commit()
            token = create_access_token(other.id, identity.id)
            refused = await live.post(
                url + "/compare", headers={"Authorization": f"Bearer {token}"}
            )
            assert refused.status_code == 404, refused.text
        await telemetry.drain_recordings()
        feedback = await db_session.scalar(
            select(PreviewFeedback).where(
                PreviewFeedback.outfit_id == comparison_setup["outfit"],
                PreviewFeedback.kind == "fix",
            )
        )
        assert str(feedback.comparison_call_id) == call_id
        assert feedback.note == "Shorten only the front hem"
        assert len(requests) == 2
        assert len(requests[0]["contents"][0]["parts"]) == 3
        print(
            "SMOKE: compare=200; stub note=Shorten the hem; edited redraw=200; photo-informed=True; comparison call linked=True; second user=404; local provider calls=2; paid calls=0"
        )
    finally:
        request_patch.stop()
        server.should_exit = True
        await running
        listener.close()
        await asyncio.to_thread(stub.shutdown)
        stub.server_close()
        thread.join()
