import asyncio
from datetime import datetime, timezone

import pytest
from aiohttp import ClientSession
from aiogram import Dispatcher, F, Router
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from prometheus_client import REGISTRY

from bot.middlewares.update_metrics import HandlerNameMiddleware, UpdateMetricsMiddleware
from bot.utils.metrics import external_call, start_metrics_server


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def callback_update(data: str) -> Update:
    user = User(id=1, is_bot=False, first_name="A")
    message = Message(message_id=1, date=datetime.now(timezone.utc),
                      chat=Chat(id=1, type="private"))
    return Update(update_id=1,
                  callback_query=CallbackQuery(id="cb", from_user=user, chat_instance="ci",
                                               data=data, message=message))


def build_dispatcher() -> Dispatcher:
    router = Router()

    @router.callback_query(F.data.startswith("buy:"))
    async def buy_handler(callback: CallbackQuery):
        return None

    @router.callback_query(F.data == "boom")
    async def failing_handler(callback: CallbackQuery):
        raise RuntimeError("boom")

    dp = Dispatcher()
    dp.update.outer_middleware(UpdateMetricsMiddleware())
    dp.callback_query.middleware(HandlerNameMiddleware())
    dp.include_router(router)
    return dp


def test_matched_callback_is_labelled_with_handler_name(recording_bot):
    labels = dict(kind="callback", handler="buy_handler", outcome="ok")
    before = sample("vpnox_bot_updates_total", **labels)
    asyncio.run(build_dispatcher().feed_update(recording_bot, callback_update("buy:3")))
    assert sample("vpnox_bot_updates_total", **labels) == before + 1
    assert sample("vpnox_bot_update_seconds_count", kind="callback", handler="buy_handler") >= 1


def test_forged_callback_data_does_not_create_a_label(recording_bot):
    labels = dict(kind="callback", handler="unhandled", outcome="ok")
    before = sample("vpnox_bot_updates_total", **labels)
    asyncio.run(build_dispatcher().feed_update(recording_bot, callback_update("forged_123:x")))
    assert sample("vpnox_bot_updates_total", **labels) == before + 1
    assert sample("vpnox_bot_updates_total", kind="callback", handler="forged_123", outcome="ok") == 0


def test_handler_exception_counts_as_error(recording_bot):
    labels = dict(kind="callback", handler="failing_handler", outcome="error")
    before = sample("vpnox_bot_updates_total", **labels)
    with pytest.raises(RuntimeError):
        asyncio.run(build_dispatcher().feed_update(recording_bot, callback_update("boom")))
    assert sample("vpnox_bot_updates_total", **labels) == before + 1


def test_external_call_timeout_raises_and_counts():
    async def slow():
        async with external_call("panel", "test_timeout", timeout=0.01):
            await asyncio.sleep(1)

    before = sample("vpnox_bot_external_call_errors_total", service="panel", op="test_timeout")
    with pytest.raises(TimeoutError):
        asyncio.run(slow())
    assert sample("vpnox_bot_external_call_errors_total", service="panel", op="test_timeout") == before + 1


def test_external_call_counts_reported_failures_only():
    async def calls():
        async with external_call("panel", "test_ok"):
            pass
        async with external_call("panel", "test_bad_status") as call:
            call.fail()

    asyncio.run(calls())
    assert sample("vpnox_bot_external_call_errors_total", service="panel", op="test_ok") == 0
    assert sample("vpnox_bot_external_call_errors_total", service="panel", op="test_bad_status") == 1
    assert sample("vpnox_bot_external_call_seconds_count", service="panel", op="test_ok") == 1


def test_metrics_runner_serves_prometheus_text():
    async def scrape():
        runner = await start_metrics_server(0, "test-version")
        try:
            port = runner.addresses[0][1]
            async with ClientSession() as client:
                async with client.get(f"http://127.0.0.1:{port}/metrics") as response:
                    return response.status, response.headers["Content-Type"], await response.text()
        finally:
            await runner.cleanup()

    status, content_type, body = asyncio.run(scrape())
    assert status == 200
    assert content_type.startswith("text/plain")
    assert 'vpnox_bot_info{version="test-version"} 1.0' in body
    assert "# TYPE vpnox_bot_updates_total counter" in body
