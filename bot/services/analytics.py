"""Product events: track(user_id, name, **props) from anywhere, never raising.

Events are written to analytics_events by a background task with a session of its own,
so a failed insert neither slows the handler nor touches the handler's transaction.
Props carry bounded descriptors only (provider, months, section, reason class), never
raw start parameters, payloads or tokens.
"""
import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional, Set, Tuple

from db.dal import analytics_dal

EVENT_NAMES = frozenset({
    "start",
    "trial_activated",
    "section_viewed",
    "subscribe_period_selected",
    "payment_started",
    "payment_succeeded",
    "payment_failed",
    "referral_shared",
    "connect_link_opened",
    "autorenew_toggled",
})

SESSION_IDLE_SECONDS = 30 * 60
_PRUNE_EVERY = 1024

_session_factory = None
_sessions: Dict[int, Tuple[str, float]] = {}
_session_writes = 0
_pending: Set[asyncio.Task] = set()


def configure(session_factory) -> None:
    global _session_factory
    _session_factory = session_factory


def session_id_for(user_id: int, now: Optional[float] = None) -> str:
    """Same id while the user keeps producing events; a new one after 30 idle minutes.

    In memory only: a restart starts new sessions.
    """
    global _session_writes
    now = time.monotonic() if now is None else now
    current = _sessions.get(user_id)
    session_id = current[0] if current and now - current[1] < SESSION_IDLE_SECONDS else uuid.uuid4().hex
    _sessions[user_id] = (session_id, now)
    _session_writes += 1
    if _session_writes % _PRUNE_EVERY == 0:
        for stale_user in [u for u, (_, seen) in _sessions.items() if now - seen >= SESSION_IDLE_SECONDS]:
            del _sessions[stale_user]
    return session_id


def track(user_id: Optional[int], name: str, **props: Any) -> None:
    try:
        if name not in EVENT_NAMES:
            logging.warning(f"analytics: dropped event outside the vocabulary: {name!r}")
            return
        if _session_factory is None:
            return
        clean_props = json.loads(json.dumps(props, default=str))
        session_id = session_id_for(user_id) if user_id is not None else None
        task = asyncio.get_running_loop().create_task(
            _store(user_id, name, clean_props, session_id))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except Exception:
        logging.exception(f"analytics: failed to queue event {name!r}")


async def _store(user_id: Optional[int], name: str, props: Dict[str, Any],
                 session_id: Optional[str]) -> None:
    try:
        async with _session_factory() as session:
            await analytics_dal.add_event(session, user_id=user_id, name=name, props=props,
                                          session_id=session_id)
            await session.commit()
    except Exception:
        logging.warning(f"analytics: failed to store event {name!r}", exc_info=True)


async def flush() -> None:
    """Wait for queued writes, e.g. on shutdown."""
    if _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)
