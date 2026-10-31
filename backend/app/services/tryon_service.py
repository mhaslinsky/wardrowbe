import asyncio
import base64
import hashlib
import json
import logging
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import HTTPException

from app.services import ai_telemetry_service
from app.services.ai_telemetry_service import CallContext
from app.services.image_service import ImageService
from app.services.public_download import download_public

logger = logging.getLogger(__name__)
QUEUE_URL = "https://queue.fal.run"
KOLORS_ENDPOINT = "fal-ai/kling/v1-5/kolors-virtual-try-on"
FASHN_ENDPOINT = "fal-ai/fashn/tryon/v1.6"
TRYON_ENDPOINTS = {"cardigan": FASHN_ENDPOINT}
TRYON_TIMEOUT = 90.0
POLL_INTERVAL = 2.0
TRYON_IMAGE_MAX_BYTES = 20 * 1024 * 1024
CANCEL_TIMEOUT = 3.0


class TryOnError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def queue_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "queue.fal.run"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid queue URL")
    return value


def validate_image(data):
    with ImageService()._open_bounded(data, ".png") as image:
        image.load()


class TryOnService:
    def __init__(self, key: str, telemetry: CallContext, *, wait=asyncio.sleep, clock=monotonic):
        self._key = key
        self.telemetry = telemetry
        self.wait = wait
        self.clock = clock

    async def _download(self, url: str, deadline: float) -> bytes:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not (host.endswith(".fal.media") or host == "cdn.fashn.ai")
            or parsed.username
            or parsed.password
            or self._key in url
        ):
            raise ValueError("Invalid image URL")
        try:
            data, _, _ = await download_public(
                url,
                content_types={"image/png", "image/jpeg", "image/webp"},
                limit=TRYON_IMAGE_MAX_BYTES,
                deadline=deadline,
                timeout=deadline,
                size_error="TRYON_IMAGE_TOO_LARGE",
                allow_redirects=False,
            )
        except HTTPException as error:
            if error.status_code == 413:
                raise TryOnError("image_too_large") from None
            raise
        await asyncio.to_thread(validate_image, data)
        return data

    async def _cancel(self, url: str) -> None:
        try:
            target = queue_url(url)
            async with asyncio.timeout(CANCEL_TIMEOUT):
                async with httpx.AsyncClient(timeout=CANCEL_TIMEOUT) as client:
                    response = await client.put(
                        target,
                        headers={"Authorization": "Key " + self._key},
                        timeout=CANCEL_TIMEOUT,
                    )
                    response.raise_for_status()
        except Exception as error:
            logger.warning("Try-on cancel failed: %s", type(error).__name__)

    async def request(self, images: list[bytes], garment_type: str) -> bytes:
        endpoint = TRYON_ENDPOINTS.get(garment_type, KOLORS_ENDPOINT)
        human, garment = [
            "data:image/jpeg;base64," + base64.b64encode(data).decode() for data in images
        ]
        body = (
            {
                "model_image": human,
                "garment_image": garment,
                "category": "tops",
                "mode": "quality",
                "garment_photo_type": "auto",
                "num_samples": 1,
                "segmentation_free": True,
                "output_format": "png",
            }
            if endpoint == FASHN_ENDPOINT
            else {"human_image_url": human, "garment_image_url": garment}
        )
        request = json.dumps(body, separators=(",", ":")).encode()
        context = self.telemetry
        call_id = uuid4()
        context.last_call_id = call_id
        record = {
            "id": call_id,
            "created_at": datetime.now(UTC),
            "user_id": context.user_id,
            "feature": context.feature,
            "resource_type": context.resource_type,
            "resource_id": context.resource_id,
            "provider": "fal",
            "requested_model": endpoint,
            "request_bytes": len(request),
            "request_sha256": hashlib.sha256(request).hexdigest(),
            "prompt_bytes": 0,
            "prompt_sha256": hashlib.sha256(b"").hexdigest(),
            "images": [ai_telemetry_service._fingerprint(data) for data in images],
        }
        started = self.clock()
        deadline = started + TRYON_TIMEOUT
        reason, output, http_status = None, None, None
        cancel_url = None

        def remaining():
            seconds = deadline - self.clock()
            if seconds <= 0:
                raise TimeoutError
            return seconds

        try:
            async with asyncio.timeout(TRYON_TIMEOUT):
                async with httpx.AsyncClient(timeout=TRYON_TIMEOUT) as client:
                    headers = {"Authorization": "Key " + self._key}
                    response = await client.post(
                        f"{QUEUE_URL}/{endpoint}", headers=headers, json=body, timeout=remaining()
                    )
                    http_status = response.status_code
                    response.raise_for_status()
                    queued = response.json()
                    cancel_url = queued.get("cancel_url")
                    status_url = queue_url(queued["status_url"])
                    result_url = queue_url(queued["response_url"])
                    while True:
                        response = await client.get(
                            status_url, headers=headers, timeout=remaining()
                        )
                        http_status = response.status_code
                        response.raise_for_status()
                        status = response.json()["status"]
                        if status == "COMPLETED":
                            break
                        if status not in {"IN_QUEUE", "IN_PROGRESS"}:
                            raise ValueError("Invalid queue status")
                        await self.wait(min(POLL_INTERVAL, remaining()))
                    response = await client.get(result_url, headers=headers, timeout=remaining())
                    http_status = response.status_code
                    response.raise_for_status()
                    payload = response.json()
                    image = payload["images"][0] if endpoint == FASHN_ENDPOINT else payload["image"]
                    output = await self._download(image["url"], remaining())
                    remaining()
        except (TimeoutError, httpx.TimeoutException):
            reason = "timeout"
        except httpx.HTTPStatusError as error:
            reason = "http_error"
            http_status = error.response.status_code
        except httpx.RequestError:
            reason = "network_error"
        except TryOnError as error:
            reason = error.reason
        except (
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            AttributeError,
            OSError,
            HTTPException,
        ):
            reason = "malformed_result"
        except Exception:
            reason = "provider_error"
        except asyncio.CancelledError:
            reason = "cancelled"
            raise
        finally:
            try:
                if cancel_url and reason in {
                    "timeout",
                    "cancelled",
                    "malformed_result",
                    "image_too_large",
                }:
                    await self._cancel(cancel_url)
            finally:
                try:
                    ai_telemetry_service._enqueue(
                        {
                            **record,
                            "latency_ms": (self.clock() - started) * 1000,
                            "outcome": "error" if reason else "success",
                            "error_kind": reason,
                            "http_status": http_status,
                            "answer": {
                                "structured": [],
                                "text": [],
                                "images": (
                                    [ai_telemetry_service._fingerprint(output)]
                                    if output and not reason
                                    else []
                                ),
                            },
                        }
                    )
                except Exception:
                    ai_telemetry_service._failed("serialization")
        if reason:
            raise TryOnError(reason)
        return output
