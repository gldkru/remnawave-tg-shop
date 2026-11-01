"""One-time vs auto-renew YooKassa payments.

save_payment_method=true narrows the YooKassa form to the methods enabled for autopay, which
dropped SBP for every buyer once autopay went live. The one-time option must not ask to save.
"""
import asyncio
from types import SimpleNamespace

import pytest

from bot.handlers.user.subscription import payments
from bot.keyboards.inline.user_keyboards import get_payment_method_keyboard
from db.dal import payment_dal

USER_ID = 4242


class KeyI18n:
    def gettext(self, lang, key, **kwargs):
        return key


def settings(autopay: bool) -> SimpleNamespace:
    return SimpleNamespace(
        DEFAULT_LANGUAGE="ru", YOOKASSA_ENABLED=True, YOOKASSA_AUTOPAYMENTS_ENABLED=autopay,
        STARS_ENABLED=False, TRIBUTE_ENABLED=False, CRYPTOPAY_ENABLED=False,
        YOOKASSA_DEFAULT_RECEIPT_EMAIL="receipts@example.invalid",
        TERMS_OF_SERVICE_URL="https://example.invalid/terms",
    )


def yookassa_callbacks(markup) -> list:
    return [b.callback_data for row in markup.inline_keyboard for b in row
            if b.callback_data and b.callback_data.startswith("pay_yk")]


class FakeYooKassa:
    configured = True

    def __init__(self):
        self.calls = []

    async def create_payment(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "yk-1", "status": "pending", "confirmation_url": "https://pay.invalid/x"}


class FakeSession:
    async def commit(self):
        pass

    async def rollback(self):
        pass


class FakeMessage:
    def __init__(self):
        self.texts = []

    async def edit_text(self, text, **kwargs):
        self.texts.append(text)


@pytest.fixture
def stub_payment_dal(monkeypatch):
    async def create_payment_record(session, data):
        return SimpleNamespace(payment_id=1)

    async def update_payment_status_by_db_id(session, *args, **kwargs):
        return None

    monkeypatch.setattr(payment_dal, "create_payment_record", create_payment_record)
    monkeypatch.setattr(payment_dal, "update_payment_status_by_db_id", update_payment_status_by_db_id)


def pay(callback_data: str, autopay_enabled: bool):
    yk = FakeYooKassa()
    message = FakeMessage()

    async def answer(*args, **kwargs):
        pass

    callback = SimpleNamespace(data=callback_data, message=message, answer=answer,
                               from_user=SimpleNamespace(id=USER_ID))
    i18n_data = {"current_language": "ru", "i18n_instance": KeyI18n()}
    asyncio.run(payments.pay_yk_callback_handler(callback, settings(autopay_enabled), i18n_data, yk, FakeSession()))
    return yk.calls, message.texts


def test_autopay_on_offers_one_time_and_autopay():
    markup = get_payment_method_keyboard(1, 199.0, None, None, "₽", "ru", KeyI18n(), settings(True))
    assert yookassa_callbacks(markup) == ["pay_yk1:1:199.0", "pay_yk:1:199.0"]


def test_autopay_off_offers_a_single_plain_payment():
    markup = get_payment_method_keyboard(1, 199.0, None, None, "₽", "ru", KeyI18n(), settings(False))
    assert yookassa_callbacks(markup) == ["pay_yk:1:199.0"]


def test_one_time_payment_does_not_save_the_method(stub_payment_dal):
    calls, texts = pay("pay_yk1:1:199.0", autopay_enabled=True)
    assert [c["save_payment_method"] for c in calls] == [False]
    assert texts == ["payment_link_message"]


def test_autopay_payment_saves_the_method_and_shows_the_terms(stub_payment_dal):
    calls, texts = pay("pay_yk:3:499.0", autopay_enabled=True)
    assert [c["save_payment_method"] for c in calls] == [True]
    assert texts == ["payment_link_message_autopay"]


def test_stale_autopay_button_with_autopay_off_does_not_save(stub_payment_dal):
    calls, texts = pay("pay_yk:1:199.0", autopay_enabled=False)
    assert [c["save_payment_method"] for c in calls] == [False]
    assert texts == ["payment_link_message"]
