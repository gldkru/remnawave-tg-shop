import asyncio
from types import SimpleNamespace

import pytest

from bot.services.panel_api_service import PanelApiService
from bot.services.subscription_service import SubscriptionService
from db.dal import subscription_dal, user_dal

USER_ID = 1001
PANEL_USER_ID = 77


async def hang(*args, **kwargs):
    await asyncio.sleep(1)


async def connection_refused(*args, **kwargs):
    return {"error": True, "status_code": -1, "message": "Connection error"}


async def server_error(*args, **kwargs):
    return {"error": True, "status_code": 500, "details": {}}


async def not_found(*args, **kwargs):
    return {"error": True, "status_code": 404, "details": {"errorCode": "A063"}}


@pytest.fixture
def db_calls(monkeypatch):
    """Stand-in DAL: one linked user; records every write."""
    calls = []
    db_user = SimpleNamespace(user_id=USER_ID, panel_user_id=PANEL_USER_ID, username=None,
                              first_name=None, last_name=None)

    async def get_user_by_id(session, user_id):
        return db_user

    async def get_active_subscription_by_user_id(session, user_id, panel_user_id=None):
        return None

    async def update_user(session, user_id, data):
        calls.append(("update_user", user_id, data))

    async def deactivate_all_user_subscriptions(session, user_id):
        calls.append(("deactivate_all_user_subscriptions", user_id))

    monkeypatch.setattr(user_dal, "get_user_by_id", get_user_by_id)
    monkeypatch.setattr(user_dal, "update_user", update_user)
    monkeypatch.setattr(subscription_dal, "get_active_subscription_by_user_id",
                        get_active_subscription_by_user_id)
    monkeypatch.setattr(subscription_dal, "deactivate_all_user_subscriptions",
                        deactivate_all_user_subscriptions)
    return calls


def service_with_panel(send_request) -> SubscriptionService:
    panel = PanelApiService(SimpleNamespace(PANEL_API_URL="http://panel.invalid/api",
                                            PANEL_API_KEY=None,
                                            PANEL_API_TIMEOUT_SECONDS=0.01))
    panel._send_request = send_request
    return SubscriptionService(SimpleNamespace(), panel)


@pytest.mark.parametrize("panel_answer", [hang, connection_refused, server_error])
def test_unavailable_panel_keeps_the_link(db_calls, panel_answer):
    service = service_with_panel(panel_answer)
    assert asyncio.run(service.get_active_subscription_details(None, USER_ID)) is None
    assert db_calls == []


def test_panel_not_found_still_unlinks(db_calls):
    service = service_with_panel(not_found)
    assert asyncio.run(service.get_active_subscription_details(None, USER_ID)) is None
    assert db_calls == [
        ("deactivate_all_user_subscriptions", USER_ID),
        ("update_user", USER_ID, {"panel_user_id": None}),
    ]


def test_unavailable_panel_does_not_relink_to_a_new_panel_user(db_calls):
    service = service_with_panel(hang)
    created = []

    async def no_user_by_telegram_id(**kwargs):
        return []

    async def create_panel_user(**kwargs):
        created.append(kwargs)
        return {"response": {"id": 999}}

    service.panel_service.get_users_by_filter = no_user_by_telegram_id
    service.panel_service.create_panel_user = create_panel_user
    result = asyncio.run(service._get_or_create_panel_user_link_details(None, USER_ID))
    assert result == (None, None, None, False)
    assert created == []
    assert db_calls == []
