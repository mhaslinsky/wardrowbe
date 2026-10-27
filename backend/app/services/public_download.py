import asyncio
import ipaddress
import socket

import httpx
from fastapi import HTTPException


async def download_public(
    url: str,
    *,
    content_types: set[str],
    limit: int,
    deadline: float,
    timeout: float,
    size_error: str,
) -> tuple[bytes, str, str]:
    async with asyncio.timeout(deadline):
        parsed = httpx.URL(url)
        async with httpx.AsyncClient(
            timeout=timeout, trust_env=False, follow_redirects=False
        ) as client:
            for _hop in range(5):
                if parsed.scheme not in ("http", "https") or not parsed.host or parsed.userinfo:
                    raise ValueError("Use a public HTTP or HTTPS URL")
                addresses = await asyncio.get_running_loop().getaddrinfo(
                    parsed.host,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
                if not addresses or any(
                    not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses
                ):
                    raise ValueError("Use a public URL")
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
                    content_type = response.headers.get("content-type", "").split(";")[0].lower()
                    if content_type not in content_types:
                        raise ValueError("Unsupported content type")
                    content = bytearray()
                    async for chunk in response.aiter_bytes(64 * 1024):
                        content.extend(chunk)
                        if len(content) > limit:
                            raise HTTPException(413, size_error)
                    return bytes(content), content_type, str(parsed)
            raise ValueError("Too many redirects")
