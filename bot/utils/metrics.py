"""Prometheus metrics for the bot, served by a runner of their own.

The 8080 app is published to the internet, so /metrics must never be routed there.
Every label value is bounded by code: handler function names, service and operation
constants, Telegram API method names. Never put user ids, payloads or tokens in labels.
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from aiohttp import web
from prometheus_client import (CONTENT_TYPE_LATEST, Counter, Histogram, Info,
                               generate_latest)

LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 15, 30)

UPDATES = Counter(
    "vpnox_bot_updates_total",
    "Telegram updates processed",
    ["kind", "handler", "outcome"],
)
UPDATE_SECONDS = Histogram(
    "vpnox_bot_update_seconds",
    "Time to process a Telegram update, middlewares included",
    ["kind", "handler"],
    buckets=LATENCY_BUCKETS,
)
EXTERNAL_CALL_SECONDS = Histogram(
    "vpnox_bot_external_call_seconds",
    "Latency of outbound calls to the panel, payment providers and Telegram",
    ["service", "op"],
    buckets=LATENCY_BUCKETS,
)
EXTERNAL_CALL_ERRORS = Counter(
    "vpnox_bot_external_call_errors_total",
    "Outbound calls that timed out, raised or got an error response",
    ["service", "op"],
)
CALLBACKS_UNANSWERED = Counter(
    "vpnox_bot_callbacks_unanswered_total",
    "Callback queries the handler left unanswered, answered by the guard instead",
    ["handler"],
)
BOT_INFO = Info("vpnox_bot", "Running bot build")


class ExternalCall:
    """Handle yielded by external_call; fail() records an error that did not raise."""

    __slots__ = ("failed",)

    def __init__(self) -> None:
        self.failed = False

    def fail(self) -> None:
        self.failed = True


@asynccontextmanager
async def external_call(service: str,
                        op: str,
                        timeout: Optional[float] = None) -> AsyncIterator[ExternalCall]:
    """Time an outbound call, count its failure, and cancel it after `timeout` seconds.

    A timeout surfaces as TimeoutError, like any other exception it propagates.
    """
    call = ExternalCall()
    started = time.monotonic()
    try:
        if timeout is None:
            yield call
        else:
            async with asyncio.timeout(timeout):
                yield call
    except Exception:
        call.failed = True
        raise
    finally:
        EXTERNAL_CALL_SECONDS.labels(service, op).observe(time.monotonic() - started)
        if call.failed:
            EXTERNAL_CALL_ERRORS.labels(service, op).inc()


async def _metrics_handler(request: web.Request) -> web.Response:
    return web.Response(body=generate_latest(),
                        headers={"Content-Type": CONTENT_TYPE_LATEST})


async def start_metrics_server(port: int, version: str) -> web.AppRunner:
    BOT_INFO.info({"version": version})
    app = web.Application()
    app.router.add_get("/metrics", _metrics_handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host="0.0.0.0", port=port).start()
    logging.info(f"Metrics server started on http://0.0.0.0:{port}/metrics")
    return runner
