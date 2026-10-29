"""Keep secrets that live in URLs out of the logs.

The Telegram webhook is served at /<BOT_TOKEN>, so every access log line for it, and
every startup line that prints the webhook URL, carried the bot token. Tracked connect
links carry a bearer token as well. The filter runs on the root handlers, so it covers
aiohttp's access log, aiogram and our own messages alike.
"""
import logging
import re

_BOT_TOKEN_RE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}")
_CONNECT_TOKEN_RE = re.compile(r"(/go/connect/)[A-Za-z0-9_=-]+")


def redact_secrets(text: str) -> str:
    text = _BOT_TOKEN_RE.sub("<bot-token>", text)
    return _CONNECT_TOKEN_RE.sub(r"\1<token>", text)


class SecretRedactingFilter(logging.Filter):

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            redacted = redact_secrets(message)
            if redacted != message:
                record.msg, record.args = redacted, None
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact_secrets(record.exc_text)
            # Structured extras, e.g. aiohttp's first_request_line, for non-text formatters.
            for key, value in list(vars(record).items()):
                if key != "msg" and isinstance(value, str):
                    setattr(record, key, redact_secrets(value))
        except Exception:
            pass
        return True


def install_log_redaction() -> None:
    """Attach the filter to every root handler; call after logging is configured."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, SecretRedactingFilter) for f in handler.filters):
            handler.addFilter(SecretRedactingFilter())
