import asyncio
import base64
import hashlib
import json
import logging
import math
from asyncio import create_task
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from time import monotonic
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

from app.database import async_session_maker
from app.models.ai_call import AICall

logger = logging.getLogger(__name__)
MAX_PENDING = 128
WRITE_TIMEOUT = 3.0
recording_failures = 0
_pending: set[asyncio.Task[None]] = set()
_write_slots = asyncio.Semaphore(2)


@dataclass
class CallContext:
    """User and feature attribution for one request's provider attempt chain."""

    user_id: UUID
    feature: str
    resource_type: str | None = None
    resource_id: UUID | None = None
    last_call_id: UUID | None = None
    failure_reason: str | None = None


def _failed(reason: str) -> None:
    global recording_failures
    recording_failures += 1
    logger.warning(
        "AI recording failed: %s; ai_recording_failures_total=%d", reason, recording_failures
    )


def _fingerprint(data: bytes) -> dict[str, int | str]:
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _usage_counts(values: dict[str, Any]) -> dict[str, Any]:
    counts = {}
    for name, value in values.items():
        if isinstance(value, dict):
            nested = _usage_counts(value)
            if nested:
                counts[name] = nested
        elif isinstance(value, int) and not isinstance(value, bool):
            counts[name] = value
        elif isinstance(value, float) and math.isfinite(value):
            counts[name] = value
    return counts


def _bounded_text(value: Any) -> str | None:
    return value[:120] if isinstance(value, str) else None


def _reported_cost(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        cost = Decimal(str(value))
    except InvalidOperation:
        return None
    return cost if cost.is_finite() else None


def _logprob_summary(logprobs: dict[str, Any]) -> dict[str, int | float]:
    values = [
        token["logprob"]
        for token in logprobs.get("content", [])
        if isinstance(token, dict)
        and isinstance(token.get("logprob"), (int, float))
        and not isinstance(token["logprob"], bool)
        and math.isfinite(token["logprob"])
    ]
    return (
        {
            "token_count": len(values),
            "mean_logprob": sum(values) / len(values),
            "min_logprob": min(values),
        }
        if values
        else {}
    )


def _request_metadata(body: dict[str, Any], request: bytes) -> dict[str, Any]:
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
    return {
        "request_bytes": len(request),
        "request_sha256": hashlib.sha256(request).hexdigest(),
        "prompt_bytes": sum(len(text) for text in texts),
        "prompt_sha256": digest.hexdigest(),
        "images": images,
    }


def _response_metadata(
    response: httpx.Response, native: bool, response_format: str = "chat"
) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, UnicodeError):
        return {"error_kind": "malformed_response"}
    if not isinstance(payload, dict):
        return {"error_kind": "malformed_response"}
    result = {"returned_model": _bounded_text(payload.get("modelVersion" if native else "model"))}
    usage = payload.get("usageMetadata" if native else "usage")
    if isinstance(usage, dict):
        result["usage"] = _usage_counts(usage)
        cost = _reported_cost(usage.get("cost"))
        if cost is not None:
            result["reported_cost"] = cost
            result["cost_currency"] = _bounded_text(usage.get("currency"))
    if response_format == "decision":
        from app.services.buy_check_service import parse_answer

        result["answer"] = {
            "structured": [],
            "text": [],
            "images": [],
        }
        if response.status_code >= 400:
            result["error_kind"] = "http_error"
            return result
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            result["error_kind"] = "invalid_answer"
            return result
        # Invalid decision answers still need to fit JSONB.
        result["answer"]["structured"] = [
            json.loads(json.dumps(answers), parse_constant=lambda _constant: "non-finite")
        ]
        try:
            parse_answer(payload)
        except ValueError:
            result["error_kind"] = "invalid_answer"
        return result
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
                answer["logprobs"] = _logprob_summary(choice["logprobs"])
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
    if not any(answer[name] for name in ("structured", "text", "images")):
        result.setdefault("error_kind", "empty_answer")
    result["answer"] = answer
    return result


async def _persist(record: dict[str, Any]) -> None:
    async with async_session_maker() as session:
        session.add(AICall(**record))
        await session.commit()


async def _write(record: dict[str, Any]) -> None:
    try:
        async with asyncio.timeout(WRITE_TIMEOUT):
            async with _write_slots:
                await _persist(record)
    except TimeoutError:
        _failed("write_timeout")
    except Exception:
        _failed("database")


def _enqueue(record: dict[str, Any]) -> None:
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
    client: httpx.AsyncClient,
    url: str,
    *,
    context: CallContext | None,
    model: str,
    body: dict[str, Any],
    headers: Mapping[str, str] | None = None,
    native: bool = False,
    feature: str | None = None,
    response_format: Literal["chat", "decision"] = "chat",
    notice_status: str | None = None,
) -> httpx.Response:
    """POST a model request and record the attempt without awaiting database writes.

    New providers use this function with a request-local CallContext containing
    the signed-in user's ID and feature. Inputs retain only byte counts and hashes.
    Structured answers are preserved; plain-text answers and images retain only
    counts and hashes.
    The original response or exception is preserved even if recording fails.
    Health checks must call their HTTP client directly.
    """
    if context is None:
        return await client.post(url, headers=headers, json=body)
    request = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
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
                **_request_metadata(body, request),
            }
            context.last_call_id = record["id"]
        except Exception:
            _failed("serialization")
    started = monotonic()
    try:
        request_headers = httpx.Headers(headers)
        request_headers["Content-Type"] = "application/json"
        response = await client.post(url, headers=request_headers, content=request)
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
                        **(
                            {
                                "answer": {
                                    "structured": [],
                                    "text": [],
                                    "images": [],
                                    "fallback_notice": notice_status,
                                }
                            }
                            if notice_status
                            else {}
                        ),
                    }
                )
            except Exception:
                _failed("serialization")
        raise
    if record is not None:
        try:
            metadata = _response_metadata(response, native, response_format)
            if notice_status:
                metadata.setdefault("answer", {"structured": [], "text": [], "images": []})[
                    "fallback_notice"
                ] = notice_status
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


async def drain_recordings(timeout: float = 5) -> None:
    """Wait for writes, then cancel them and allow one more second for cleanup."""
    tasks = set(_pending)
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in pending:
            _failed("shutdown_timeout")
            task.cancel()
        if pending:
            cleanup = asyncio.gather(*pending, return_exceptions=True)
            completed, _ = await asyncio.wait({cleanup}, timeout=1)
            if not completed:
                _failed("shutdown_cleanup_timeout")
        _pending.difference_update(task for task in tasks if task.done())
