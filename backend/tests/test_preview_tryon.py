import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings, get_settings
from app.models.ai_call import AICall
from app.models.considering import ItemPurchase
from app.models.preview import OutfitPreview
from app.models.user import User
from app.services import ai_telemetry_service, preview_service
from app.services.preview_service import PreviewService, layer_jpeg, snapshot
from tests.test_image_undo_replace import _jpeg_bytes
from tests.test_outfit_preview import (
    configure_layers,
    request_images,
    request_prompt,
    upload,
)
from tests.test_outfit_preview import (
    layer_transport as layer_transport,
)
from tests.test_outfit_preview import (
    preview_setup as preview_setup,
)

KOLORS = "fal-ai/kling/v1-5/kolors-virtual-try-on"
FASHN = "fal-ai/fashn/tryon/v1.6"
FAKE_KEY = "per291-secret-never-record"


@pytest.fixture
def fal_http(monkeypatch, layer_transport):
    gemini_post = httpx.AsyncClient.post
    state = SimpleNamespace(
        sequence=[],
        calls=layer_transport.calls,
        gemini=layer_transport.requests,
        gemini_wire=[],
        fal=[],
        downloads=[],
        failure=None,
        outputs=layer_transport.outputs,
        tryon=_jpeg_bytes(color="purple"),
        cleanup_failure=False,
    )
    monkeypatch.setitem(get_settings().__dict__, "fal_key", FAKE_KEY)
    monkeypatch.setattr(ai_telemetry_service, "_enqueue", state.calls.append)
    monkeypatch.setattr(
        preview_service,
        "face_reference",
        lambda paths: (None, preview_service.reference_hash(paths)),
    )

    async def post(_client, url, **kwargs):
        if url.startswith("/"):
            return await _client.request("POST", url, **kwargs)
        if url.startswith("https://queue.fal.run/"):
            assert kwargs["headers"]["Authorization"] == "Key " + FAKE_KEY
            body = kwargs.get("json") or json.loads(kwargs["content"])
            endpoint = url.removeprefix("https://queue.fal.run/")
            state.fal.append(body)
            state.sequence.append(("fal", endpoint))
            if state.failure == "http_error":
                return httpx.Response(500, request=httpx.Request("POST", url), text=FAKE_KEY)
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "request_id": "fixture",
                    "status_url": "https://queue.fal.run/status",
                    "response_url": "https://queue.fal.run/result",
                },
            )
        body = json.loads(kwargs["content"])
        state.gemini_wire.append(kwargs["content"])
        state.sequence.append(("gemini", len(request_images(body))))
        if state.cleanup_failure and len(state.gemini) == 1:
            state.gemini.append(body)
            return httpx.Response(500, request=httpx.Request("POST", url), text="cleanup failed")
        return await gemini_post(_client, url, **kwargs)

    async def get(_client, url, **kwargs):
        if url.startswith("/"):
            return await _client.request("GET", url, **kwargs)
        if url.endswith("/status"):
            if state.failure == "timeout":
                raise httpx.ReadTimeout(FAKE_KEY, request=httpx.Request("GET", url))
            payload = {"status": "COMPLETED"}
        elif url.endswith("/result"):
            payload = (
                {}
                if state.failure == "malformed_result"
                else {
                    "image": {"url": "https://v3.fal.media/output.png"},
                    "images": [{"url": "https://cdn.fashn.ai/output.png"}],
                }
            )
        else:
            assert not kwargs.get("headers", {}).get("Authorization")
            state.downloads.append(url)
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                content=(b"not an image" if state.failure == "invalid_image" else state.tryon),
            )
        return httpx.Response(200, request=httpx.Request("GET", url), json=payload)

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    return state


@pytest.mark.parametrize("cardigan", [False, True])
@pytest.mark.parametrize("considering", [False, True])
async def test_hybrid_order_images_telemetry_and_cache(
    cardigan,
    considering,
    client,
    auth_headers,
    preview_setup,
    db_session,
    fal_http,
    monkeypatch,
    caplog,
):
    pieces = [
        ("t-shirt", "base"),
        *([("cardigan", "mid")] if cardigan else []),
        ("jacket", "outer"),
    ]
    outfit = await configure_layers(db_session, preview_setup, pieces)
    layer = next(link.item for link in outfit.items if link.item.type == "jacket")
    layer.notes = "Size: XXL\nNear knee length <keep>"
    layer.tags = {"fit": "relaxed", "material": "knit <wool>"}
    outfit.style_notes = "Wear <open>"
    user = await db_session.get(User, preview_setup["user"])
    user.body_measurements = {"height": 180, "shirt_size": "XL"}
    if considering:
        db_session.add(ItemPurchase(item_id=layer.id, state="considering"))
        outfit.source_item_id = layer.id
    await db_session.commit()
    await upload(client, auth_headers)
    clothes_key, images, garments = snapshot(outfit)
    await PreviewService(db_session).generate(outfit.id, user.id, True, note="Keep <length>")
    expected = [("gemini", 2)]
    for endpoint in [FASHN, KOLORS] if cardigan else [KOLORS]:
        expected.extend([("fal", endpoint), ("gemini", 3)])
    assert fal_http.sequence == expected
    layers = ["cardigan", "jacket"] if cardigan else ["jacket"]
    for index, kind in enumerate(layers):
        previous = layer_jpeg(fal_http.outputs[index])
        garment = next(
            image for image, record in zip(images, garments, strict=True) if record["type"] == kind
        )
        body = fal_http.fal[index]
        fields = (
            ("model_image", "garment_image")
            if kind == "cardigan"
            else (
                "human_image_url",
                "garment_image_url",
            )
        )
        assert [base64.b64decode(body[field].split(",", 1)[1]) for field in fields] == [
            previous,
            garment,
        ]
        if kind == "cardigan":
            assert {name: value for name, value in body.items() if name not in fields} == {
                "category": "tops",
                "mode": "quality",
                "garment_photo_type": "auto",
                "num_samples": 1,
                "segmentation_free": True,
                "output_format": "png",
            }
        cleanup = fal_http.gemini[index + 1]
        assert request_images(cleanup) == [layer_jpeg(fal_http.tryon), garment, previous]
        assert "third image" in request_prompt(cleanup)
    prompt = request_prompt(fal_http.gemini[-1])
    for fact in (
        '"size": "XXL"',
        '"height": 180',
        '"shirt_size": "XL"',
        "Near knee length \\u003ckeep\\u003e",
        "knit \\u003cwool\\u003e",
        "Wear \\u003copen\\u003e",
        "Keep \\u003clength\\u003e",
    ):
        assert fact in prompt
    saved = await db_session.get(OutfitPreview, outfit.id)
    await db_session.refresh(saved, attribute_names=["clothes_key", "cache_key"])
    original_cache = saved.cache_key
    assert saved.clothes_key == clothes_key
    assert saved.usage["_ai_call_ids"] == [str(call["id"]) for call in fal_http.calls]
    assert saved.usage["_ai_call_id"] == str(fal_http.calls[-1]["id"])
    assert saved.usage["IMAGE"]["totalTokenCount"] == sum(
        30 + 2 * index for index in range(len(layers) + 1)
    )
    assert {call["feature"] for call in fal_http.calls} == {
        "considering_preview" if considering else "preview_redraw",
    }
    fal_calls = [call for call in fal_http.calls if call["provider"] == "fal"]
    assert [call["requested_model"] for call in fal_calls] == (
        [FASHN, KOLORS] if cardigan else [KOLORS]
    )
    assert all(call["outcome"] == "success" for call in fal_calls)
    assert all(
        call["answer"]["images"] == [ai_telemetry_service._fingerprint(fal_http.tryon)]
        for call in fal_calls
    )
    for call in fal_http.calls:
        assert FAKE_KEY not in repr(call)
        db_session.add(AICall(**call))
    await db_session.commit()
    rows = list((await db_session.scalars(select(AICall).where(AICall.user_id == user.id))).all())
    assert len(rows) == len(fal_http.calls)
    for row in rows:
        assert FAKE_KEY not in repr(
            {column.name: getattr(row, column.name) for column in AICall.__table__.columns}
        )
    assert FAKE_KEY not in repr(Settings(fal_key=FAKE_KEY))
    assert FAKE_KEY not in caplog.text
    monkeypatch.setitem(get_settings().__dict__, "fal_key", None)
    cached = await PreviewService(db_session).generate(outfit.id, user.id, False)
    assert cached["stale"] is False and saved.cache_key == original_cache
    assert fal_http.sequence == expected


@pytest.mark.parametrize("failure", ["http_error", "timeout", "malformed_result", "invalid_image"])
async def test_tryon_failure_falls_back_without_secret(
    failure,
    client,
    auth_headers,
    preview_setup,
    db_session,
    fal_http,
    caplog,
):
    outfit = await configure_layers(
        db_session, preview_setup, [("t-shirt", "base"), ("jacket", "outer")]
    )
    fal_http.failure = failure
    await upload(client, auth_headers)
    response = await client.post(preview_setup["url"], headers=auth_headers)
    assert response.status_code == 200, response.text
    assert fal_http.sequence == [("gemini", 2), ("fal", KOLORS), ("gemini", 2)]
    saved = await db_session.get(OutfitPreview, outfit.id)
    layer_id = str(next(link.item_id for link in outfit.items if link.item.type == "jacket"))
    reason = "malformed_result" if failure == "invalid_image" else failure
    assert saved.usage["IMAGE"]["tryon_fallbacks"] == [{"layer": layer_id, "reason": reason}]
    assert saved.usage["_ai_call_ids"] == [str(call["id"]) for call in fal_http.calls]
    assert saved.usage["_ai_call_id"] == str(fal_http.calls[-1]["id"])
    assert fal_http.calls[1]["provider"] == "fal"
    assert fal_http.calls[1]["outcome"] == "error"
    assert fal_http.calls[1]["error_kind"] == reason
    assert FAKE_KEY not in repr(fal_http.calls) + caplog.text
    assert "Try-on failed" in caplog.text


async def test_cleanup_failure_leaves_no_preview(
    client,
    auth_headers,
    preview_setup,
    db_session,
    fal_http,
):
    await configure_layers(db_session, preview_setup, [("t-shirt", "base"), ("jacket", "outer")])
    await upload(client, auth_headers)
    before = set(preview_setup["folder"].glob("*.jpg"))
    fal_http.cleanup_failure = True
    response = await client.post(preview_setup["url"], headers=auth_headers)
    assert fal_http.sequence == [("gemini", 2), ("fal", KOLORS), ("gemini", 3)]
    assert response.status_code == 502
    saved = await db_session.get(OutfitPreview, preview_setup["outfit"])
    assert saved.paths == {} and saved.claim_id is None and saved.started_at is None
    assert set(preview_setup["folder"].glob("*.jpg")) == before


@pytest.mark.parametrize("key", [None, "", "   "])
async def test_unconfigured_requests_match_base_recording(
    key,
    client,
    auth_headers,
    preview_setup,
    db_session,
    fal_http,
    monkeypatch,
):
    monkeypatch.setitem(get_settings().__dict__, "fal_key", key)
    await configure_layers(
        db_session, preview_setup, [("t-shirt", "base"), ("cardigan", "mid"), ("jacket", "outer")]
    )
    await upload(client, auth_headers)
    await PreviewService(db_session).generate(preview_setup["outfit"], preview_setup["user"], False)
    assert fal_http.sequence == [("gemini", 2), ("gemini", 2), ("gemini", 2)]
    assert [hashlib.sha256(wire).hexdigest() for wire in fal_http.gemini_wire] == [
        "ce5606c3fd2b62661769b7da78519b0dd15040b02fb809efe43dac0684015786",
        "371a1114cfc5c33962f0fcec556a7dc83f04d2c93893ccb8e6e2027597c9413c",
        "8ffe9b8d1422ff6faae8e34bb26da3f5ddc22b1d843fdfccad62957f9654d008",
    ]


def test_cleanup_prompt_and_configuration():
    root = Path(__file__).parents[2]
    assert "try-on step" in (root / "docs/user-guide.md").read_text()
    assert "FAL_KEY: ${FAL_KEY:-}" in (root / "docker-compose.yml").read_text()
    assert "# FAL_KEY=" in (root / ".env.example").read_text()
    prompt = (root / "backend/app/prompts/outfit_preview_cleanup.txt").read_text()
    for required in ("body outline and proportions", "third image", "item_notes", "pullover"):
        assert required in prompt
    assert "mauve" not in prompt


async def test_poll_deadline_is_shared_and_wait_is_injected(monkeypatch):
    from app.services.tryon_service import TryOnError, TryOnService

    clock = SimpleNamespace(now=0, waits=0, requests=0)

    async def post(_client, url, **kwargs):
        clock.now += 35
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "status_url": "https://queue.fal.run/status",
                "response_url": "https://queue.fal.run/result",
            },
        )

    async def get(_client, url, **kwargs):
        clock.requests += 1
        clock.now += 20
        return httpx.Response(
            200, request=httpx.Request("GET", url), json={"status": "IN_PROGRESS"}
        )

    async def advance(delay):
        clock.waits += 1
        clock.now += delay

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    context = ai_telemetry_service.CallContext(uuid4(), "preview")
    service = TryOnService(FAKE_KEY, context, wait=advance, clock=lambda: clock.now)
    with pytest.raises(TryOnError, match="timeout"):
        await service.request([_jpeg_bytes(), _jpeg_bytes()], "jacket")
    assert clock.waits > 0 and clock.requests == 3
