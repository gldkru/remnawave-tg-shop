import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import (BaseRequestMiddleware,
                                                     NextRequestMiddlewareType)
from aiogram.methods.base import TelegramMethod, TelegramType
from aiogram.types import TelegramObject, Update

from bot.utils.metrics import UPDATE_SECONDS, UPDATES, external_call

# Key in handler data under which the outer middleware shares the HandlerRoute.
ROUTE_KEY = "metrics_route"


class HandlerRoute:
    """Which handler took the update, filled in once aiogram has matched one.

    Callback data and command text are client-controlled (any client can send arbitrary
    callback_data or /commands), so labels use the matched handler's function name,
    which is bounded by code.
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


def update_kind(event: Update) -> str:
    if event.message:
        return "message"
    if event.callback_query:
        return "callback"
    return "other"


class UpdateMetricsMiddleware(BaseMiddleware):
    """Outermost update middleware: counts and times every update."""

    async def __call__(self, handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
                       event: Update, data: Dict[str, Any]) -> Any:
        kind = update_kind(event)
        route = HandlerRoute("unhandled" if kind != "other" else event.event_type)
        data[ROUTE_KEY] = route
        started = time.monotonic()
        outcome = "error"
        try:
            result = await handler(event, data)
            outcome = "ok"
            return result
        finally:
            UPDATE_SECONDS.labels(kind, route.name).observe(time.monotonic() - started)
            UPDATES.labels(kind, route.name, outcome).inc()


class HandlerNameMiddleware(BaseMiddleware):
    """Inner message/callback middleware: runs only once a handler matched."""

    async def __call__(self, handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: Dict[str, Any]) -> Any:
        route = data.get(ROUTE_KEY)
        handler_object = data.get("handler")
        if route is not None and handler_object is not None:
            route.name = getattr(handler_object.callback, "__name__", "unknown")
        return await handler(event, data)


class TelegramCallMetricsMiddleware(BaseRequestMiddleware):
    """Bot API request middleware: latency and errors per API method."""

    async def __call__(self, make_request: NextRequestMiddlewareType[TelegramType], bot,
                       method: TelegramMethod[TelegramType]):
        async with external_call("telegram", type(method).__name__):
            return await make_request(bot, method)
