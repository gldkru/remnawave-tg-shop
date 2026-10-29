from typing import Any, Callable, List, Optional

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods.base import TelegramMethod

# Shape-valid fake token; the session below never talks to Telegram.
TEST_BOT_TOKEN = "42:TEST"


class RecordingSession(BaseSession):
    """Bot API session that records requests instead of sending them."""

    def __init__(self, respond: Optional[Callable[[TelegramMethod], Any]] = None) -> None:
        super().__init__()
        self.requests: List[TelegramMethod] = []
        self._respond = respond or (lambda method: True)

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: Optional[int] = None) -> Any:
        self.requests.append(method)
        return self._respond(method)

    async def stream_content(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise NotImplementedError
        yield b""

    async def close(self) -> None:
        pass


@pytest.fixture
def recording_bot() -> Bot:
    return Bot(token=TEST_BOT_TOKEN, session=RecordingSession())
