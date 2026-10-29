import asyncio
import io
import logging

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.utils.log_redaction import install_log_redaction, redact_secrets

# Shaped like a real token so the redaction has to recognise it, but not a real one.
BOT_TOKEN = "1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"
CONNECT_TOKEN = "AAAAAAAAAAUAAGkv8nTqZ3l9vVb1xQdJbU1ZcHkK2l1nqyYp0H4sUe8mN0aF3w"


@pytest.fixture
def root_output():
    """Root logging as main.py sets it up: one stream handler at INFO, redaction installed."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    install_log_redaction()
    yield stream
    root.handlers, root.level = saved_handlers, saved_level


def test_webhook_and_connect_requests_never_log_their_tokens(root_output):
    async def ok(request):
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_post(f"/{BOT_TOKEN}", ok)
    app.router.add_get("/go/connect/{token}", ok)

    async def requests():
        async with TestClient(TestServer(app)) as client:
            assert (await client.post(f"/{BOT_TOKEN}", json={"update_id": 1})).status == 200
            assert (await client.get(f"/go/connect/{CONNECT_TOKEN}")).status == 200

    asyncio.run(requests())
    output = root_output.getvalue()
    assert "aiohttp.access" in output  # the access log is still written
    assert "POST /<bot-token>" in output
    assert "GET /go/connect/<token>" in output
    assert BOT_TOKEN not in output
    assert BOT_TOKEN.split(":")[1] not in output
    assert CONNECT_TOKEN not in output


def test_tokens_in_messages_args_and_tracebacks_are_redacted(root_output):
    log = logging.getLogger("test.redaction")
    log.info("webhook set to https://shop.example/%s", BOT_TOKEN)
    try:
        raise RuntimeError(f"request to /{BOT_TOKEN} failed")
    except RuntimeError:
        log.exception("boom")
    output = root_output.getvalue()
    assert BOT_TOKEN not in output
    assert output.count("<bot-token>") == 2  # the message and the exception line


def test_redact_secrets_leaves_ordinary_text_alone():
    assert redact_secrets("GET /webhook/yookassa 200 user 123456789") == "GET /webhook/yookassa 200 user 123456789"
