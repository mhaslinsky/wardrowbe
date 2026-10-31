import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import HTTPException

from app.services import ai_telemetry_service
from app.services.ai_telemetry_service import CallContext
from app.services.image_service import ImageService

QUEUE_URL = "https://queue.fal.run"
KOLORS_ENDPOINT = "fal-ai/kling/v1-5/kolors-virtual-try-on"
FASHN_ENDPOINT = "fal-ai/fashn/tryon/v1.6"
TRYON_ENDPOINTS = {"cardigan": FASHN_ENDPOINT}
TRYON_TIMEOUT = 90.0
POLL_INTERVAL = 2.0


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
                    image_url = image["url"]
                    parsed = urlsplit(image_url)
                    if (
                        parsed.scheme != "https"
                        or not parsed.hostname
                        or parsed.username
                        or parsed.password
                        or self._key in image_url
                    ):
                        raise ValueError("Invalid image URL")
                    response = await client.get(image_url, timeout=remaining())
                    http_status = response.status_code
                    response.raise_for_status()
                    output = response.content
                    await asyncio.to_thread(validate_image, output)
                    remaining()
        except (TimeoutError, httpx.TimeoutException):
            reason = "timeout"
        except httpx.HTTPStatusError:
            reason = "http_error"
        except httpx.RequestError:
            reason = "network_error"
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
