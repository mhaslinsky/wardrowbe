from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.config import Settings
from app.main import app, settings
from app.models.item import ClothingItem
from app.services.notification_providers import (
    NotificationMessage,
    build_family_invite_email,
    build_notification_email,
)
from app.services.notification_service import NotificationDispatcher
from app.workers.notifications import wash_reminder_message

HOSTS = "wardrobe.haslinsky.net,christina.haslinsky.net"
EMAIL = "christina.rosario91@gmail.com"


def configured(**kwargs):
    return Settings(
        _env_file=None,
        app_url="https://wardrobe.haslinsky.net",
        public_hosts=HOSTS,
        public_host_by_email={EMAIL: "christina.haslinsky.net"},
        **kwargs,
    )


def test_background_links_use_recipient_host_and_fallback():
    config = configured()
    assert config.app_link("/dashboard", email=EMAIL) == "https://christina.haslinsky.net/dashboard"
    assert (
        config.app_link("/dashboard", email="other@example.com")
        == "https://wardrobe.haslinsky.net/dashboard"
    )
    assert config.app_link("/dashboard") == "https://wardrobe.haslinsky.net/dashboard"


def test_email_footer_and_invite_use_recipient_host():
    with patch("app.services.notification_providers.get_settings", return_value=configured()):
        email = build_notification_email(EMAIL, NotificationMessage(title="Test", body="Body"))
        assert "https://christina.haslinsky.net/dashboard/notifications" in email.html_body
        invite = build_family_invite_email(EMAIL, "Family", "Inviter", "test-token")
        assert "https://christina.haslinsky.net/invite?token=test-token" in invite.text_body
        assert "wardrobe.haslinsky.net" not in invite.html_body


@pytest.mark.parametrize(
    "host", ["attacker.example", "https://christina.haslinsky.net", "christina.haslinsky.net/path"]
)
def test_mapping_cannot_select_an_unlisted_or_malformed_host(host):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, public_hosts=HOSTS, public_host_by_email={EMAIL: host})


@pytest.mark.parametrize("host", HOSTS.split(","))
async def test_both_public_hosts_work(host, monkeypatch):
    monkeypatch.setattr(settings, "public_hosts", HOSTS, raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=f"https://{host}") as client:
        assert (await client.get("/api/v1/health")).status_code == 200


async def test_unlisted_host_is_refused_even_with_forged_forwarded_host(monkeypatch):
    monkeypatch.setattr(settings, "public_hosts", HOSTS, raising=False)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://attacker.example"
    ) as client:
        response = await client.get(
            "/api/v1/health", headers={"X-Forwarded-Host": "christina.haslinsky.net"}
        )
        assert response.status_code == 400


@pytest.mark.parametrize(
    "host", ["localhost:8000", "127.0.0.1:8000", "backend:8000", "10.0.0.207:8000"]
)
async def test_internal_callers_work(host, monkeypatch):
    monkeypatch.setattr(settings, "public_hosts", HOSTS, raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=f"http://{host}") as client:
        assert (await client.get("/api/v1/health")).status_code == 200
        assert (await client.get("/api/v1/auth/config")).status_code == 200


async def test_unset_and_reset_preserve_single_host_behavior(monkeypatch):
    for value in ("", HOSTS, ""):
        monkeypatch.setattr(settings, "public_hosts", value, raising=False)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://attacker.example"
        ) as client:
            assert (await client.get("/api/v1/health")).status_code == (400 if value else 200)


def test_outfit_and_laundry_links_use_the_owners_email():
    with (
        patch("app.services.notification_service.get_settings", return_value=configured()),
        patch("app.workers.notifications.get_settings", return_value=configured()),
    ):
        outfit = SimpleNamespace(
            id=uuid4(),
            scheduled_for=None,
            occasion="casual",
            weather_data=None,
            reasoning=None,
            ai_raw_response=None,
            style_notes=None,
        )
        user = SimpleNamespace(display_name="Test", timezone="UTC", email=EMAIL)
        message = NotificationDispatcher(None)._build_outfit_message(outfit, user)
        assert message.url == f"https://christina.haslinsky.net/dashboard/outfits/{outfit.id}"
        laundry = wash_reminder_message([ClothingItem(type="shirt", name="Test shirt")], EMAIL)
        assert laundry.url == "https://christina.haslinsky.net/dashboard/wardrobe"


@pytest.mark.parametrize(
    "host", ["https://example.com", "example.com/path", "example.com:443", "example.com?x=1"]
)
def test_invalid_public_host_configuration_is_refused(host):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, public_hosts=host)


def test_mapping_normalizes_email_and_host_case(monkeypatch):
    monkeypatch.setenv("PUBLIC_HOSTS", HOSTS)
    monkeypatch.setenv("PUBLIC_HOST_BY_EMAIL", '{" SECOND@EXAMPLE.COM ":"CHRISTINA.HASLINSKY.NET"}')
    config = Settings(_env_file=None)
    assert (
        config.app_link("test", email="second@example.com")
        == "https://christina.haslinsky.net/test"
    )


async def test_missing_and_duplicate_hosts_are_refused(monkeypatch):
    monkeypatch.setattr(settings, "public_hosts", HOSTS)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://wardrobe.haslinsky.net"
    ) as client:
        missing = client.build_request("GET", "/api/v1/health")
        del missing.headers["host"]
        assert (await client.send(missing)).status_code == 400
        assert (
            await client.get(
                "/api/v1/health",
                headers=[("host", "wardrobe.haslinsky.net"), ("host", "christina.haslinsky.net")],
            )
        ).status_code == 400


@pytest.mark.parametrize("hosts", [",", " , ", "https://invalid.example, /invalid"])
def test_nonempty_invalid_host_list_fails_settings_startup(hosts):
    with pytest.raises(ValidationError, match="PUBLIC_HOSTS"):
        Settings(_env_file=None, public_hosts=hosts)
