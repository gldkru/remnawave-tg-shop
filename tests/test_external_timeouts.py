import asyncio
import time
from types import SimpleNamespace

from prometheus_client import REGISTRY

import bot.services.yookassa_service as yookassa_module
from bot.services.panel_api_service import PanelApiService
from bot.services.yookassa_service import YooKassaService


def errors(service: str, op: str) -> float:
    return REGISTRY.get_sample_value("vpnox_bot_external_call_errors_total",
                                     {"service": service, "op": op}) or 0.0


def panel_service(timeout: float) -> PanelApiService:
    return PanelApiService(SimpleNamespace(PANEL_API_URL="http://panel.invalid/api",
                                           PANEL_API_KEY=None,
                                           PANEL_API_TIMEOUT_SECONDS=timeout))


def test_hung_panel_request_returns_timeout_error_response():
    service = panel_service(0.01)

    async def hang(*args, **kwargs):
        await asyncio.sleep(1)

    service._send_request = hang
    before = errors("panel", "get_user")
    result = asyncio.run(service.get_user_by_panel_id(1))
    assert result is None
    assert errors("panel", "get_user") == before + 1


def test_panel_not_found_is_not_an_error():
    service = panel_service(1)

    async def not_found(*args, **kwargs):
        return {"error": True, "status_code": 404, "details": {}}

    service._send_request = not_found
    before = errors("panel", "get_user_by_username")
    assert asyncio.run(service.get_users_by_filter(username="tg_1")) == []
    assert errors("panel", "get_user_by_username") == before


def test_yookassa_lookup_timeout_asks_for_redelivery(monkeypatch):
    def slow_find_one(payment_id):
        time.sleep(0.2)

    monkeypatch.setattr(yookassa_module.YooKassaPayment, "find_one", staticmethod(slow_find_one))
    service = YooKassaService("shop", "secret", "https://t.me/bot",
                              settings_obj=SimpleNamespace(PAYMENT_API_TIMEOUT_SECONDS=0.01))
    before = errors("yookassa", "find_payment")
    payment, lookup_error = asyncio.run(service.fetch_payment("pay-1"))
    assert payment is None
    assert lookup_error  # the webhook answers 503 only on a truthy error
    assert errors("yookassa", "find_payment") == before + 1
