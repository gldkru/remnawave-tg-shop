"""Stop buttons from spinning when a handler forgets or is too slow to answer.

Telegram keeps a pressed inline button in its loading state until answerCallbackQuery
arrives. CallbackAnswerTracker sees every answer that goes through the Bot API session,
so the guard knows whether the handler answered without touching handler code.
"""
import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware, Bot
from aiogram.client.session.middlewares.base import (BaseRequestMiddleware,
                                                     NextRequestMiddlewareType)
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery
from aiogram.methods.base import TelegramMethod, TelegramType
from aiogram.types import CallbackQuery, TelegramObject

from bot.middlewares.update_metrics import ROUTE_KEY
from bot.utils.metrics import CALLBACKS_UNANSWERED

DEFAULT_ANSWER_TIMEOUT_SECONDS = 2.5


class PendingCallback:
    __slots__ = ("chat_id", "answered", "answered_by_guard")

    def __init__(self, chat_id: Optional[int]) -> None:
        self.chat_id = chat_id
        self.answered = False
        self.answered_by_guard = False


class CallbackAnswerTracker(BaseRequestMiddleware):
    """Bot API request middleware: lets exactly one answer per tracked callback through.

    A later answer is dropped instead of failing with "query is too old". If it carried
    an alert, typically an error message from a slow handler, the text is sent as a chat
    message so the user still sees it.
    """

    def __init__(self) -> None:
        self._pending: Dict[str, PendingCallback] = {}

    def begin(self, callback_query_id: str, chat_id: Optional[int]) -> PendingCallback:
        pending = PendingCallback(chat_id)
        self._pending[callback_query_id] = pending
        return pending

    def end(self, callback_query_id: str) -> None:
        self._pending.pop(callback_query_id, None)

    async def __call__(self, make_request: NextRequestMiddlewareType[TelegramType], bot: Bot,
                       method: TelegramMethod[TelegramType]):
        if not isinstance(method, AnswerCallbackQuery):
            return await make_request(bot, method)
        pending = self._pending.get(method.callback_query_id)
        if pending is None:
            return await make_request(bot, method)
        if pending.answered:
            if method.show_alert and method.text and pending.chat_id:
                await self._deliver_late_alert(bot, pending.chat_id, method.text)
            return True
        # Set before the request: no other coroutine may slip a second answer in while it is in flight.
        pending.answered = True
        return await make_request(bot, method)

    @staticmethod
    async def _deliver_late_alert(bot: Bot, chat_id: int, text: str) -> None:
        try:
            await bot.send_message(chat_id, text, parse_mode=None)
        except Exception:
            logging.exception("Failed to deliver a late callback alert as a message")


def _is_already_answered(error: TelegramBadRequest) -> bool:
    message = error.message.lower()
    return "query is too old" in message or "query id is invalid" in message


class CallbackAnswerGuardMiddleware(BaseMiddleware):
    """Outer callback_query middleware: answers for the handler when it does not.

    The guard answers with an empty answer once `timeout` seconds pass, or as soon as the
    handler returns or raises without having answered, whichever comes first.
    """

    def __init__(self, tracker: CallbackAnswerTracker,
                 timeout: float = DEFAULT_ANSWER_TIMEOUT_SECONDS) -> None:
        self.tracker = tracker
        self.timeout = timeout

    async def __call__(self, handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
                       event: CallbackQuery, data: Dict[str, Any]) -> Any:
        bot: Bot = data["bot"]
        chat_id = event.message.chat.id if event.message else event.from_user.id
        pending = self.tracker.begin(event.id, chat_id)
        route = data.get(ROUTE_KEY)

        async def watchdog() -> None:
            await asyncio.sleep(self.timeout)
            await self._answer(bot, event.id, pending, route)

        watchdog_task = asyncio.create_task(watchdog())
        try:
            return await handler(event, data)
        finally:
            if pending.answered_by_guard:
                await watchdog_task
            else:
                watchdog_task.cancel()
                await self._answer(bot, event.id, pending, route)
            self.tracker.end(event.id)

    @staticmethod
    async def _answer(bot: Bot, callback_query_id: str, pending: PendingCallback, route) -> None:
        if pending.answered:
            return
        pending.answered_by_guard = True
        CALLBACKS_UNANSWERED.labels(route.name if route else "unknown").inc()
        try:
            await bot.answer_callback_query(callback_query_id)
        except TelegramBadRequest as error:
            if not _is_already_answered(error):
                logging.exception("Callback answer guard failed to answer")
        except Exception:
            logging.exception("Callback answer guard failed to answer")
