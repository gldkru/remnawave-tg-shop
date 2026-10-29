from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AnalyticsEvent


async def add_event(session: AsyncSession, *, user_id: Optional[int], name: str,
                    props: Optional[Dict[str, Any]], session_id: Optional[str]) -> None:
    session.add(AnalyticsEvent(user_id=user_id, name=name, props=props or None,
                               session_id=session_id))
