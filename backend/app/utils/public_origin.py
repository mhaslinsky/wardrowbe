from ipaddress import ip_address
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


def public_hostnames(value: str) -> set[str]:
    hosts = set()
    for entry in value.split(","):
        host = entry.strip().lower()
        if not host:
            continue
        parsed = urlsplit(f"//{host}")
        labels = host.split(".")
        if (
            parsed.hostname != host
            or parsed.netloc != host
            or parsed.path
            or parsed.query
            or parsed.fragment
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or not all(
                    character.isascii() and (character.isalnum() or character == "-")
                    for character in label
                )
                for label in labels
            )
        ):
            raise ValueError("PUBLIC_HOSTS must contain hostnames without schemes, paths or ports")
        hosts.add(host)
    return hosts


def internal_host(value: str) -> bool:
    try:
        parsed = urlsplit(f"//{value}")
        hostname = parsed.hostname
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return False
        if parsed.port is not None and not 0 < parsed.port < 65536:
            return False
        if hostname in {"localhost", "backend", "frontend"}:
            return True
        address = ip_address(hostname or "")
        return address.is_private or address.is_loopback
    except ValueError:
        return False


class PublicHostMiddleware:
    def __init__(self, app: ASGIApp, settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        hosts = public_hostnames(self.settings.public_hosts)
        if scope["type"] == "http" and hosts:
            values = [
                value.decode("latin-1").lower()
                for name, value in scope["headers"]
                if name.lower() == b"host"
            ]
            if len(values) != 1 or (values[0] not in hosts and not internal_host(values[0])):
                await JSONResponse({"detail": "Unknown host"}, status_code=400)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)
