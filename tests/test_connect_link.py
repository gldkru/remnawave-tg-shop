import asyncio
import time
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.app.web import connect_link as connect_route_module
from bot.app.web.connect_link import CONNECT_ROUTE, connect_link_route
from bot.middlewares.i18n import JsonI18n
from bot.utils.connect_link import (connect_button_url, sign_connect_token,
                                    verify_connect_token)

SECRET = "test-secret"
NOW = 1_800_000_000


def test_valid_token_returns_user_id():
    token = sign_connect_token(SECRET, 123456789, NOW + 60)
    assert verify_connect_token(SECRET, token, now=NOW) == 123456789


def test_tampered_token_is_rejected():
    token = sign_connect_token(SECRET, 123456789, NOW + 60)
    other_user = sign_connect_token(SECRET, 987654321, NOW + 60)
    # Payload of one user with the MAC of another.
    spliced = other_user[:16] + token[16:]
    flipped = token[:5] + ("A" if token[5] != "A" else "B") + token[6:]
    for bad in (spliced, flipped, token[:-2], token + "AA", "", "not a token", "ü" * 60):
        assert verify_connect_token(SECRET, bad, now=NOW) is None
    assert verify_connect_token("another-secret", token, now=NOW) is None


def test_expired_token_is_rejected():
    token = sign_connect_token(SECRET, 42, NOW)
    assert verify_connect_token(SECRET, token, now=NOW) is None
    assert verify_connect_token(SECRET, token, now=NOW - 1) == 42


def link_settings(**overrides):
    values = dict(CONNECT_LINK_SECRET=SECRET, WEBHOOK_BASE_URL="https://shop.example/",
                  CONNECT_LINK_TTL_DAYS=30)
    values.update(overrides)
    return SimpleNamespace(**values)


def test_button_url_is_tracked_only_when_configured():
    direct = "https://panel.example/sub/abc"
    assert connect_button_url(link_settings(CONNECT_LINK_SECRET=None), 1, direct) == direct
    assert connect_button_url(link_settings(WEBHOOK_BASE_URL=None), 1, direct) == direct
    assert connect_button_url(link_settings(), 1, None) is None

    tracked = connect_button_url(link_settings(), 77, direct)
    prefix = "https://shop.example/go/connect/"
    assert tracked.startswith(prefix)
    assert verify_connect_token(SECRET, tracked[len(prefix):], now=time.time()) == 77
    assert "77" not in tracked


class FakeSessionFactory:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


class FakeSubscriptionService:
    def __init__(self, urls):
        self.urls = urls

    async def get_current_subscription_url(self, session, user_id):
        return self.urls.get(user_id)


class FakeBot:
    async def me(self):
        return SimpleNamespace(username="vpn_test_bot")


def run_request(path: str, urls, tracked):
    app = web.Application()
    app["settings"] = link_settings()
    app["async_session_factory"] = FakeSessionFactory
    app["subscription_service"] = FakeSubscriptionService(urls)
    app["i18n"] = JsonI18n(path="locales", default="ru")
    app["bot"] = FakeBot()
    app.router.add_get(CONNECT_ROUTE, connect_link_route)

    async def go():
        async with TestClient(TestServer(app)) as client:
            response = await client.get(path, allow_redirects=False)
            return response.status, response.headers, await response.text()

    original_track = connect_route_module.track
    connect_route_module.track = lambda user_id, name, **props: tracked.append((user_id, name, props))
    try:
        return asyncio.run(go())
    finally:
        connect_route_module.track = original_track


def test_route_redirects_to_current_subscription_url():
    tracked = []
    token = sign_connect_token(SECRET, 5, int(time.time()) + 60)
    status, headers, _ = run_request(f"/go/connect/{token}", {5: "https://panel.example/sub/fresh"}, tracked)
    assert status == 302
    assert headers["Location"] == "https://panel.example/sub/fresh"
    assert headers["Cache-Control"] == "no-store"
    assert tracked == [(5, "connect_link_opened", {"result": "redirect"})]


def test_route_shows_press_again_page_for_expired_token():
    tracked = []
    token = sign_connect_token(SECRET, 5, int(time.time()) - 1)
    status, headers, body = run_request(f"/go/connect/{token}", {5: "https://panel.example/sub/x"}, tracked)
    assert status == 410
    assert "Ссылка устарела" in body and "This link has expired" in body
    assert "https://t.me/vpn_test_bot" in body
    assert "panel.example" not in body
    assert tracked == []
