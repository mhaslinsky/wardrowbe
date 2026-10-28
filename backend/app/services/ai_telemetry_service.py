import asyncio
import base64
import hashlib
import json
import logging
from asyncio import create_task
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from app.database import async_session_maker
from app.models.ai_call import AICall

logger = logging.getLogger(__name__)
MAX_PENDING = 128
recording_failures = 0
_pending: set[asyncio.Task] = set()


@dataclass
class CallContext:
    user_id: UUID
    feature: str
    resource_type: str | None = None
    resource_id: UUID | None = None
    last_call_id: UUID | None = None
    failure_reason: str | None = None


def _failed(reason):
    global recording_failures
    recording_failures += 1
    try:
        logger.warning(
            "AI recording failed: %s; ai_recording_failures_total=%d", reason, recording_failures
        )
    except Exception:
        return False
    return True


def _fingerprint(data):
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _usage_counts(values):
    return {
        name: _usage_counts(value) if isinstance(value, dict) else value
        for name, value in values.items()
        if isinstance(value, (dict, int, float)) and not isinstance(value, bool)
    }


def _request_metadata(body):
    texts, images = [], []
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content.encode())
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    texts.append(part["text"].encode())
                elif part.get("type") == "image_url":
                    location = part["image_url"]["url"]
                    images.append(
                        _fingerprint(base64.b64decode(location.split(",", 1)[1], validate=True))
                    )
    for content in body.get("contents", []):
        for part in content.get("parts", []):
            if "text" in part:
                texts.append(part["text"].encode())
            if "inlineData" in part:
                images.append(
                    _fingerprint(base64.b64decode(part["inlineData"]["data"], validate=True))
                )
    digest = hashlib.sha256()
    for text in texts:
        digest.update(len(text).to_bytes(8, "big"))
        digest.update(text)
    request = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    return {
        "request_bytes": len(request),
        "request_sha256": hashlib.sha256(request).hexdigest(),
        "prompt_bytes": sum(len(text) for text in texts),
        "prompt_sha256": digest.hexdigest(),
        "images": images,
    }


def _response_metadata(response, native):
    try:
        payload = response.json()
    except (ValueError, UnicodeError):
        return {"error_kind": "malformed_response"}
    if not isinstance(payload, dict):
        return {"error_kind": "malformed_response"}
    result = {"returned_model": payload.get("modelVersion" if native else "model")}
    usage = payload.get("usageMetadata" if native else "usage")
    if isinstance(usage, dict):
        result["usage"] = _usage_counts(usage)
        cost = usage.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            result["reported_cost"] = cost
            result["cost_currency"] = usage.get("currency")
    if payload.get("error"):
        result["error_kind"] = "provider_error"
        return result
    answer = {"structured": [], "text": [], "images": []}
    try:
        if native:
            parts = payload["candidates"][0]["content"]["parts"]
        else:
            choice = payload["choices"][0]
            parts = [{"text": choice["message"].get("content")}]
            if choice.get("logprobs"):
                answer["logprobs"] = choice["logprobs"]
        for part in parts:
            text = part.get("text")
            if text:
                clean = text.strip()
                if not clean:
                    continue
                if clean.startswith("```"):
                    clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                try:
                    structured = json.loads(clean)
                except ValueError:
                    structured = None
                if isinstance(structured, (dict, list)):
                    answer["structured"].append(structured)
                else:
                    answer["text"].append(_fingerprint(text.encode()))
            if part.get("inlineData"):
                answer["images"].append(
                    _fingerprint(base64.b64decode(part["inlineData"]["data"], validate=True))
                )
    except IndexError:
        result["error_kind"] = "empty_answer"
    except (KeyError, TypeError, AttributeError, ValueError):
        result["error_kind"] = "malformed_response"
    if not any(answer.values()):
        result.setdefault("error_kind", "empty_answer")
    result["answer"] = answer
    return result


async def _persist(record):
    async with async_session_maker() as session:
        session.add(AICall(**record))
        await session.commit()


async def _write(record):
    try:
        await _persist(record)
    except asyncio.CancelledError:
        raise
    except Exception:
        _failed("database")


def _enqueue(record):
    if len(_pending) >= MAX_PENDING:
        _failed("full")
        return
    coroutine = _write(record)
    try:
        task = create_task(coroutine)
    except Exception:
        coroutine.close()
        _failed("scheduling")
        return
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def record_ai_call(
    client, url, *, context, model, body, headers=None, native=False, feature=None
):
    """POST a model request and record the attempt without awaiting database writes.

    New providers use this function with a request-local CallContext containing
    the signed-in user's ID and feature. Inputs retain only byte counts and hashes.
    Structured answers are preserved; plain-text answers and images retain only
    counts and hashes.
    The original response or exception is preserved even if recording fails.
    Health checks must call their HTTP client directly.
    """
    record = None
    if context is not None:
        try:
            record = {
                "id": uuid4(),
                "created_at": datetime.now(UTC),
                "user_id": context.user_id,
                "feature": feature or context.feature,
                "resource_type": context.resource_type,
                "resource_id": context.resource_id,
                "previous_call_id": context.last_call_id if context.failure_reason else None,
                "fallback_reason": context.failure_reason,
                "provider": urlsplit(url).hostname,
                "requested_model": model,
                **_request_metadata(body),
            }
            context.last_call_id = record["id"]
        except Exception:
            _failed("serialization")
    started = monotonic()
    try:
        response = await client.post(url, headers=headers, json=body)
    except BaseException as error:
        if record is not None:
            try:
                context.failure_reason = type(error).__name__[:80]
                _enqueue(
                    {
                        **record,
                        "latency_ms": (monotonic() - started) * 1000,
                        "outcome": "error",
                        "error_kind": context.failure_reason,
                    }
                )
            except Exception:
                _failed("serialization")
        raise
    if record is not None:
        try:
            metadata = _response_metadata(response, native)
            if response.status_code >= 400:
                metadata["error_kind"] = "http_error"
            context.failure_reason = metadata.get("error_kind")
            _enqueue(
                {
                    **record,
                    **metadata,
                    "latency_ms": (monotonic() - started) * 1000,
                    "http_status": response.status_code,
                    "outcome": "error" if context.failure_reason else "success",
                }
            )
        except Exception:
            _failed("serialization")
    return response


async def drain_recordings(timeout=5):
    """Wait at most timeout seconds for pending writes, then cancel unfinished writes."""
    tasks = set(_pending)
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in pending:
            _failed("shutdown_timeout")
            task.cancel()
        _pending.difference_update(tasks)
