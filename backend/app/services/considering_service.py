import asyncio
import ipaddress
import socket
from io import BytesIO

import httpx
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from starlette.datastructures import Headers

from app.config import get_settings
from app.models.considering import ItemPurchase
from app.models.item import ClothingItem
from app.schemas.considering import ConsideringResponse
from app.schemas.item import ItemResponse
from app.services.item_service import ItemService


async def download_image(url: str) -> UploadFile:
    try:
        async with asyncio.timeout(20):
            parsed = httpx.URL(url)
            limit = get_settings().max_upload_size_mb * 1024 * 1024
            async with httpx.AsyncClient(
                timeout=10, trust_env=False, follow_redirects=False
            ) as client:
                for _hop in range(5):
                    if parsed.scheme not in ("http", "https") or not parsed.host or parsed.userinfo:
                        raise ValueError("Use a public HTTP or HTTPS image URL")
                    addresses = await asyncio.get_running_loop().getaddrinfo(
                        parsed.host,
                        parsed.port or (443 if parsed.scheme == "https" else 80),
                        type=socket.SOCK_STREAM,
                    )
                    if not addresses or any(
                        not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses
                    ):
                        raise ValueError("Use a public image URL")
                    # Pin the checked address so DNS cannot redirect the connection to a private host.
                    target = parsed.copy_with(host=addresses[0][4][0])
                    async with client.stream(
                        "GET",
                        target,
                        headers={"Host": parsed.netloc.decode()},
                        extensions={"sni_hostname": parsed.host},
                    ) as response:
                        if response.is_redirect:
                            parsed = parsed.join(response.headers["location"])
                            continue
                        response.raise_for_status()
                        content_type = (
                            response.headers.get("content-type", "").split(";")[0].lower()
                        )
                        extensions = {
                            "image/jpeg": ".jpg",
                            "image/png": ".png",
                            "image/webp": ".webp",
                            "image/heic": ".heic",
                            "image/heif": ".heif",
                        }
                        if content_type not in extensions:
                            raise ValueError("Unsupported image type")
                        content = bytearray()
                        async for chunk in response.aiter_bytes(64 * 1024):
                            content.extend(chunk)
                            if len(content) > limit:
                                raise HTTPException(413, "Image exceeds upload limit")
                        return UploadFile(
                            BytesIO(content),
                            filename="product" + extensions[content_type],
                            size=len(content),
                            headers=Headers({"content-type": content_type}),
                        )
                raise ValueError("Too many image redirects")
    except (
        ValueError,
        KeyError,
        OSError,
        httpx.HTTPError,
        httpx.InvalidURL,
        TimeoutError,
    ) as error:
        raise HTTPException(422, "Image URL could not be downloaded") from error


class ConsideringService:
    def __init__(self, db):
        self.db = db

    async def get(self, user_id, item_id, *, lock=False):
        query = (
            select(ItemPurchase)
            .join(ClothingItem, ClothingItem.id == ItemPurchase.item_id)
            .where(
                ClothingItem.user_id == user_id,
                ItemPurchase.item_id == item_id,
            )
        )
        if lock:
            query = query.with_for_update(of=ItemPurchase).execution_options(populate_existing=True)
        purchase = await self.db.scalar(query)
        if purchase is None:
            raise HTTPException(404, "Shelf item not found")
        return purchase

    async def response(self, user_id, purchase):
        item = await ItemService(self.db).get_by_id(purchase.item_id, user_id)
        return ConsideringResponse(
            source_url=purchase.source_url,
            price=purchase.price,
            currency=purchase.currency,
            planned_size=purchase.planned_size,
            state=purchase.state,
            passed_reason=purchase.passed_reason,
            item=ItemResponse.model_validate(item),
        )

    async def transition(self, user_id, item_id, state, reason=None):
        purchase = await self.get(user_id, item_id, lock=True)
        if purchase.state == "bought" and state != "bought":
            raise HTTPException(409, "Item already bought")
        purchase.state = state
        purchase.passed_reason = reason if state == "passed" else None
        await self.db.flush()
        return await self.response(user_id, purchase)
