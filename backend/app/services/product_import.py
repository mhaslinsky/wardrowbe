import asyncio
import json
import sys

import httpx
from fastapi import HTTPException

from app.product_parser import Element as Element
from app.product_parser import ProductHTML as ProductHTML
from app.product_parser import extract_product as extract_product
from app.product_parser import has_percentage as has_percentage
from app.services.public_download import download_public

_parse_slots = asyncio.Semaphore(2)


async def parse_product(content: bytes, url: str) -> dict:
    if _parse_slots.locked():
        raise HTTPException(429, "Product parsing is busy")
    await _parse_slots.acquire()
    process = None
    launch = None
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
            process = await asyncio.shield(launch)
            output, _stderr = await process.communicate(content)
        if process.returncode != 0:
            raise ValueError("Product parser failed")
        capture = json.loads(output)
        if not isinstance(capture, dict) or not capture.get("name") or not capture.get("images"):
            raise ValueError("Empty product parser result")
        return capture
    finally:
        try:
            # Reap before releasing capacity so timed-out parses cannot outlive their slot.
            if process is None and launch is not None:
                process = await launch
            if process is not None:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        await process.wait()
                await process.wait()
        finally:
            _parse_slots.release()


async def fetch_product(url: str) -> dict:
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
