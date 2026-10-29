"""Signed links for the URL connect button: /go/connect/<token> on the public web app.

token = urlsafe base64 (unpadded) of user_id (8 bytes) + expiry unix time (4 bytes)
+ HMAC-SHA256 over both, keyed with CONNECT_LINK_SECRET. The route resolves the user's
current subscription URL at click time, so a button never hands out a stale panel token.
"""
import base64
import binascii
import hashlib
import hmac
import struct
import time
from typing import Optional

CONNECT_PATH_PREFIX = "/go/connect/"

_PAYLOAD = struct.Struct(">QI")
_MAC_SIZE = hashlib.sha256().digest_size
# Domain separation: a MAC made for anything else with the same key never verifies here.
_MAC_CONTEXT = b"vpnox-connect-link:"


def _mac(secret: str, payload: bytes) -> bytes:
    return hmac.new(secret.encode(), _MAC_CONTEXT + payload, hashlib.sha256).digest()


def sign_connect_token(secret: str, user_id: int, expires_at: int) -> str:
    payload = _PAYLOAD.pack(user_id, expires_at)
    return base64.urlsafe_b64encode(payload + _mac(secret, payload)).rstrip(b"=").decode()


def verify_connect_token(secret: str, token: str, now: Optional[float] = None) -> Optional[int]:
    """The user id of a genuine, unexpired token; None for anything else."""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, binascii.Error):
        return None
    if len(raw) != _PAYLOAD.size + _MAC_SIZE:
        return None
    payload, mac = raw[:_PAYLOAD.size], raw[_PAYLOAD.size:]
    if not hmac.compare_digest(mac, _mac(secret, payload)):
        return None
    user_id, expires_at = _PAYLOAD.unpack(payload)
    if expires_at <= (time.time() if now is None else now):
        return None
    return user_id


def connect_button_url(settings, user_id: int, config_link: Optional[str]) -> Optional[str]:
    """URL for a plain URL connect button: the tracked redirect when it is configured,
    otherwise the subscription link itself."""
    if not config_link:
        return None
    if not settings.CONNECT_LINK_SECRET or not settings.WEBHOOK_BASE_URL:
        return config_link
    expires_at = int(time.time()) + settings.CONNECT_LINK_TTL_DAYS * 86400
    token = sign_connect_token(settings.CONNECT_LINK_SECRET, user_id, expires_at)
    return f"{settings.WEBHOOK_BASE_URL.rstrip('/')}{CONNECT_PATH_PREFIX}{token}"
