from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import GiftCampaign, GiftGrant, Payment, Subscription, User

SEGMENT_PAID = "paid"
SEGMENT_TRIAL = "trial"
PAID_PAYMENT_STATUS = "succeeded"


async def get_campaign_by_code(session: AsyncSession, code: str) -> Optional[GiftCampaign]:
    result = await session.execute(select(GiftCampaign).where(GiftCampaign.code == code))
    return result.scalar_one_or_none()


async def create_campaign(session: AsyncSession, code: str, title: str, message_key: str) -> GiftCampaign:
    campaign = GiftCampaign(code=code, title=title, message_key=message_key)
    session.add(campaign)
    await session.flush()
    return campaign


async def select_lapsed_users(session: AsyncSession) -> List[Tuple[int, str]]:
    """Non-banned users who had a subscription, have none running now, and are linked to the panel.

    Segment is "paid" when the user ever completed a payment, otherwise "trial" (trial or
    bonus days only). Users who never had any subscription are not returned.
    """
    now = datetime.now(timezone.utc)
    had_subscription = exists().where(Subscription.user_id == User.user_id)
    has_running = exists().where(and_(
        Subscription.user_id == User.user_id,
        Subscription.is_active == True,  # noqa: E712
        Subscription.end_date > now,
    ))
    has_paid = exists().where(and_(
        Payment.user_id == User.user_id,
        Payment.status == PAID_PAYMENT_STATUS,
    ))
    stmt = (
        select(User.user_id, has_paid.label("has_paid"))
        .where(
            User.is_banned == False,  # noqa: E712
            User.panel_user_id.is_not(None),
            had_subscription,
            ~has_running,
        )
        .order_by(User.user_id)
    )
    rows = (await session.execute(stmt)).all()
    return [(user_id, SEGMENT_PAID if paid else SEGMENT_TRIAL) for user_id, paid in rows]


async def has_running_subscription(session: AsyncSession, user_id: int) -> bool:
    now = datetime.now(timezone.utc)
    stmt = select(exists().where(and_(
        Subscription.user_id == user_id,
        Subscription.is_active == True,  # noqa: E712
        Subscription.end_date > now,
    )))
    return bool((await session.execute(stmt)).scalar())


async def get_pending_grants(session: AsyncSession, campaign_id: int, group_no: int) -> Sequence[GiftGrant]:
    stmt = (
        select(GiftGrant)
        .where(
            GiftGrant.gift_campaign_id == campaign_id,
            GiftGrant.group_no == group_no,
            GiftGrant.granted_at.is_(None),
            GiftGrant.skip_reason.is_(None),
        )
        .order_by(GiftGrant.gift_grant_id)
    )
    return (await session.execute(stmt)).scalars().all()


async def get_all_grants(session: AsyncSession, campaign_id: int) -> Sequence[GiftGrant]:
    stmt = (
        select(GiftGrant)
        .where(GiftGrant.gift_campaign_id == campaign_id)
        .order_by(GiftGrant.group_no, GiftGrant.gift_grant_id)
    )
    return (await session.execute(stmt)).scalars().all()


async def get_paid_payment_times(
        session: AsyncSession, user_ids: Sequence[int], since: datetime) -> Dict[int, List[datetime]]:
    """Completed payment timestamps per user from `since` on, oldest first."""
    if not user_ids:
        return {}
    stmt = (
        select(Payment.user_id, Payment.created_at)
        .where(
            Payment.user_id.in_(user_ids),
            Payment.status == PAID_PAYMENT_STATUS,
            Payment.created_at >= since,
        )
        .order_by(Payment.created_at)
    )
    out: Dict[int, List[datetime]] = {}
    for user_id, created_at in (await session.execute(stmt)).all():
        out.setdefault(user_id, []).append(created_at)
    return out


async def get_latest_end_dates(session: AsyncSession, user_ids: Sequence[int]) -> Dict[int, datetime]:
    """Latest subscription end date per user, running or not."""
    if not user_ids:
        return {}
    stmt = select(Subscription.user_id, Subscription.end_date).where(Subscription.user_id.in_(user_ids))
    out: Dict[int, datetime] = {}
    for user_id, end_date in (await session.execute(stmt)).all():
        if end_date and (user_id not in out or end_date > out[user_id]):
            out[user_id] = end_date
    return out


async def get_panel_user_ids(session: AsyncSession, user_ids: Sequence[int]) -> Dict[int, Optional[int]]:
    if not user_ids:
        return {}
    stmt = select(User.user_id, User.panel_user_id).where(User.user_id.in_(user_ids))
    return dict((await session.execute(stmt)).all())


async def get_group_numbers(session: AsyncSession, campaign_id: int) -> Tuple[List[int], List[int]]:
    """(all group numbers, groups that still have pending rows), both ascending."""
    pending = GiftGrant.granted_at.is_(None) & GiftGrant.skip_reason.is_(None)
    stmt = (
        select(GiftGrant.group_no, func.count().filter(pending))
        .where(GiftGrant.gift_campaign_id == campaign_id)
        .group_by(GiftGrant.group_no)
        .order_by(GiftGrant.group_no)
    )
    rows = (await session.execute(stmt)).all()
    return [g for g, _ in rows], [g for g, n in rows if n]
