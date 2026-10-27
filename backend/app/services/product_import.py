import asyncio
import json
import logging
import sys

import httpx
from fastapi import HTTPException

from app.services.public_download import download_public

_parse_slots = asyncio.Semaphore(2)
_cleanup_tasks: set[asyncio.Task] = set()
logger = logging.getLogger(__name__)


async def reap_parser(launch, slots):
    if launch is None:
        slots.release()
        return
    try:
        process = await launch
    except Exception as error:
        slots.release()
        logger.warning("Product parser launch failed: %s", type(error).__name__)
        return
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            await process.wait()
    await process.wait()
    slots.release()


def cleanup_finished(task):
    _cleanup_tasks.discard(task)
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.error("Product parser cleanup failed", exc_info=error)


async def parse_product(content: bytes, url: str) -> dict:
    slots = _parse_slots
    if slots.locked():
        raise HTTPException(429, "Product parsing is busy")
    await slots.acquire()
    launch = None
    cancellation = None
    try:
        async with asyncio.timeout(5):
            launch = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "app.product_parser",
                    url,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            )
            async with asyncio.timeout(1):
                process = await asyncio.shield(launch)
            output, _stderr = await process.communicate(content)
        if process.returncode != 0:
            raise ValueError("Product parser failed")
        capture = json.loads(output)
        if not isinstance(capture, dict) or not capture.get("name") or not capture.get("images"):
            raise ValueError("Empty product parser result")
        return capture
    except asyncio.CancelledError as error:
        cancellation = error
        raise
    finally:
        # Cleanup owns the slot until reap, even after repeated request cancellations.
        cleanup = asyncio.create_task(reap_parser(launch, slots))
        _cleanup_tasks.add(cleanup)
        cleanup.add_done_callback(cleanup_finished)
        if launch is None or launch.done():
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError as error:
                    if cancellation is None:
                        cancellation = error
            cleanup.result()
            if cancellation is not None:
                raise cancellation


async def fetch_product(url: str) -> dict:
    if _parse_slots.locked():
        raise HTTPException(429, "Product parsing is busy")
    try:
        content, _mime, final_url = await download_public(
            url,
            content_types={"text/html", "application/xhtml+xml"},
            limit=2 * 1024 * 1024,
            deadline=15,
            timeout=8,
            size_error="Product page exceeds limit",
        )
        return await parse_product(content, final_url)
    except (
        ValueError,
        AssertionError,
        KeyError,
        OSError,
        httpx.HTTPError,
        httpx.InvalidURL,
        TimeoutError,
        RecursionError,
    ) as error:
        raise HTTPException(422, "Could not read product page. Try an image.") from error
