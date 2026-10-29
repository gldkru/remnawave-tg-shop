import asyncio
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from prometheus_client import REGISTRY

from bot.middlewares.callback_answer_guard import (CallbackAnswerGuardMiddleware,
                                                   CallbackAnswerTracker)
from bot.middlewares.update_metrics import HandlerNameMiddleware, UpdateMetricsMiddleware
from tests.conftest import TEST_BOT_TOKEN, RecordingSession

GUARD_TIMEOUT = 0.05


async def answers_at_once(callback: CallbackQuery):
    await callback.answer()


async def never_answers(callback: CallbackQuery):
    return None


async def answers_late_with_alert(callback: CallbackQuery):
    await asyncio.sleep(GUARD_TIMEOUT * 4)
    await callback.answer("Payment service error", show_alert=True)


async def fails_without_answer(callback: CallbackQuery):
    raise RuntimeError("handler bug")


def unanswered(handler_name: str) -> float:
    return REGISTRY.get_sample_value("vpnox_bot_callbacks_unanswered_total",
                                     {"handler": handler_name}) or 0.0


def run_callback(handler, session: RecordingSession) -> None:
    bot = Bot(token=TEST_BOT_TOKEN, session=session)
    tracker = CallbackAnswerTracker()
    bot.session.middleware(tracker)

    router = Router()
    router.callback_query(F.data == "press")(handler)
    dp = Dispatcher()
    dp.update.outer_middleware(UpdateMetricsMiddleware())
    dp.callback_query.outer_middleware(CallbackAnswerGuardMiddleware(tracker, timeout=GUARD_TIMEOUT))
    dp.callback_query.middleware(HandlerNameMiddleware())
    dp.include_router(router)

    user = User(id=7, is_bot=False, first_name="A")
    message = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=7, type="private"))
    update = Update(update_id=1, callback_query=CallbackQuery(
        id="cb-1", from_user=user, chat_instance="ci", data="press", message=message))

    async def feed():
        try:
            await dp.feed_update(bot, update)
        except RuntimeError:
            pass

    asyncio.run(feed())


def answers(session: RecordingSession):
    return [r for r in session.requests if isinstance(r, AnswerCallbackQuery)]


def test_handler_answer_is_left_alone():
    session = RecordingSession()
    before = unanswered("answers_at_once")
    run_callback(answers_at_once, session)
    assert len(answers(session)) == 1
    assert unanswered("answers_at_once") == before


def test_guard_answers_once_when_handler_returns_without_answering():
    session = RecordingSession()
    before = unanswered("never_answers")
    run_callback(never_answers, session)
    sent = answers(session)
    assert len(sent) == 1
    assert sent[0].text is None and not sent[0].show_alert
    assert unanswered("never_answers") == before + 1


def test_guard_answers_on_handler_exception():
    session = RecordingSession()
    before = unanswered("fails_without_answer")
    run_callback(fails_without_answer, session)
    assert len(answers(session)) == 1
    assert unanswered("fails_without_answer") == before + 1


def test_slow_handler_is_answered_by_guard_without_double_answer():
    session = RecordingSession()
    before = unanswered("answers_late_with_alert")
    run_callback(answers_late_with_alert, session)
    assert len(answers(session)) == 1
    assert unanswered("answers_late_with_alert") == before + 1
    late_alerts = [r for r in session.requests if isinstance(r, SendMessage)]
    assert [m.text for m in late_alerts] == ["Payment service error"]


def test_guard_swallows_already_answered_error():
    def respond(method):
        if isinstance(method, AnswerCallbackQuery):
            raise TelegramBadRequest(
                method=method,
                message="Bad Request: query is too old and response timeout expired or query ID is invalid")
        return True

    session = RecordingSession(respond)
    before = unanswered("never_answers")
    run_callback(never_answers, session)
    assert len(answers(session)) == 1
    assert unanswered("never_answers") == before + 1
