"""Gift campaigns: plan recipients into daily groups, grant days, report outcomes.

Driven from the command line (bot/cli/gift_campaign.py), never from a chat handler: one run
touches hundreds of live subscriptions. `run --next` is the unattended form for a daily timer;
it halts itself on panel errors or failed delivery and reports to the log chat.
"""
import asyncio
from html import escape
import logging
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.orm import sessionmaker

from bot.keyboards.inline.user_keyboards import get_connect_and_main_keyboard
from bot.middlewares.i18n import JsonI18n
from bot.services.panel_api_service import PanelApiService
from bot.services.subscription_service import SubscriptionService
from bot.utils.date_utils import format_days, format_long_date
from config.settings import Settings
from db.dal import gift_campaign_dal
from db.dal.gift_campaign_dal import SEGMENT_PAID, SEGMENT_TRIAL
from db.models import GiftGrant, User

SEGMENT_LABELS = {SEGMENT_PAID: "платили ранее", SEGMENT_TRIAL: "только пробный период"}
# Renewal counts if paid from the grant until this long after the gift ends.
RENEWAL_WINDOW = timedelta(days=14)
# Telegram allows ~30 messages/s per bot; stay well under it while the bot keeps serving users.
SEND_INTERVAL_SECONDS = 0.1
# The panel stores expireAt with its own rounding; anything this close counts as applied.
EXPIRY_TOLERANCE = timedelta(minutes=1)
TEST_GROUP = 0


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_int(value: Any) -> Optional[int]:
    # 3.x serialises BigInt counters as strings.
    return int(value) if value is not None else None


def _pct(part: int, whole: int) -> str:
    return f"{part * 100 // whole}%" if whole else "—"


@dataclass
class RunResult:
    counts: Counter = field(default_factory=Counter)
    problems: List[str] = field(default_factory=list)


class GiftCampaignService:

    def __init__(self, settings: Settings, bot: Bot, i18n: JsonI18n,
                 session_factory: sessionmaker, panel_service: PanelApiService,
                 subscription_service: SubscriptionService):
        self.settings = settings
        self.bot = bot
        self.i18n = i18n
        self.session_factory = session_factory
        self.panel_service = panel_service
        self.subscription_service = subscription_service

    # ---------- planning ----------

    @staticmethod
    def _comment(title: str, segment: str, days: int, group_no: int) -> str:
        return f"{title}: +{days} дн., {SEGMENT_LABELS.get(segment, segment)}, группа {group_no}"

    async def plan(self, code: str, title: str, message_key: str, days_by_segment: Dict[str, int],
                   group_size: int, seed: int, dry_run: bool) -> Counter:
        """Freeze today's lapsed users into random groups, each with the same segment mix.

        Returns counts keyed by (group_no, segment).
        """
        async with self.session_factory() as session:
            if await gift_campaign_dal.get_campaign_by_code(session, code):
                raise ValueError(f"campaign {code!r} already exists")
            candidates = await gift_campaign_dal.select_lapsed_users(session)

            rng = random.Random(seed)
            by_segment: Dict[str, List[int]] = {}
            for user_id, segment in candidates:
                by_segment.setdefault(segment, []).append(user_id)
            # Spread each shuffled segment evenly over the whole order, so every slice of
            # group_size carries the same paid/trial proportion as the full list.
            keyed = []
            for segment, user_ids in by_segment.items():
                rng.shuffle(user_ids)
                n = len(user_ids)
                keyed.extend(((i + 0.5) / n, segment, uid) for i, uid in enumerate(user_ids))
            keyed.sort(key=lambda row: row[0])

            counts: Counter = Counter()
            rows = []
            for position, (_, segment, user_id) in enumerate(keyed):
                group_no = position // group_size + 1
                days = days_by_segment[segment]
                counts[(group_no, segment)] += 1
                rows.append(GiftGrant(
                    user_id=user_id, segment=segment, group_no=group_no, bonus_days=days,
                    comment=self._comment(title, segment, days, group_no)))

            if dry_run:
                return counts
            campaign = await gift_campaign_dal.create_campaign(session, code, title, message_key)
            for row in rows:
                row.gift_campaign_id = campaign.gift_campaign_id
            session.add_all(rows)
            await session.commit()
            return counts

    async def add_user(self, code: str, user_id: int, segment: str, days: int, group_no: int) -> None:
        """Put one extra user into a group, typically staff into group 0 for a live test."""
        async with self.session_factory() as session:
            campaign = await gift_campaign_dal.get_campaign_by_code(session, code)
            if not campaign:
                raise ValueError(f"campaign {code!r} not found")
            if not await session.get(User, user_id):
                raise ValueError(f"user {user_id} not found in the bot DB")
            session.add(GiftGrant(
                gift_campaign_id=campaign.gift_campaign_id, user_id=user_id, segment=segment,
                group_no=group_no, bonus_days=days,
                comment=self._comment(campaign.title, segment, days, group_no)))
            await session.commit()

    # ---------- running a group ----------

    async def _skip_reason(self, session, grant: GiftGrant, user: Optional[User],
                           panel_user: Optional[Dict[str, Any]]) -> Optional[str]:
        if not user:
            return "user_missing"
        if user.is_banned:
            return "banned"
        if not panel_user:
            return "no_panel_user"
        if grant.group_no == TEST_GROUP:
            return None
        # Planned as lapsed; whoever renewed or got days since then no longer qualifies.
        if await gift_campaign_dal.has_running_subscription(session, user.user_id):
            return "subscription_running"
        status = (panel_user.get("status") or "").upper()
        if status != "EXPIRED":
            return f"panel_status_{status.lower() or 'unknown'}"
        return None

    async def _confirm_on_panel(self, panel_user_id: int, new_end: datetime) -> Optional[str]:
        """Check the panel took the new expiry; reactivate an EXPIRED user. Returns a problem or None.

        extend_active_subscription_days only logs a failed panel PATCH and still returns the
        new date, so the panel is re-read here before anything is committed or announced.
        """
        panel_user = await self.panel_service.get_user_by_panel_id(panel_user_id, log_response=False)
        if not panel_user:
            return "panel_reread_failed"
        expire_at = _parse_dt(panel_user.get("expireAt"))
        if not expire_at or expire_at < new_end - EXPIRY_TOLERANCE:
            return "panel_update_failed"
        if (panel_user.get("status") or "").upper() == "EXPIRED":
            updated = await self.panel_service.update_user_details_on_panel(
                panel_user_id, {"status": "ACTIVE"}, log_response=False)
            if not updated or (updated.get("status") or "").upper() != "ACTIVE":
                return "panel_activate_failed"
        return None

    def _gift_keyboard(self, lang: str, config_link: Optional[str]) -> InlineKeyboardMarkup:
        # Connect opens the subscription page itself, not the mini app: the mini app validates
        # Telegram initData against its own copy of the bot token and fails silently when that
        # copy is stale, and a gift nobody can open is worse than a plainer page.
        if not config_link:
            return get_connect_and_main_keyboard(lang, self.i18n, self.settings, None)
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=self.i18n.gettext(lang, "connect_button"), url=config_link))
        builder.row(InlineKeyboardButton(text=self.i18n.gettext(lang, "back_to_main_menu_button"),
                                         callback_data="main_action:back_to_main"))
        return builder.as_markup()

    async def _send_gift_message(self, message_key: str, user: User, days: int, new_end: datetime,
                                 config_link: Optional[str]) -> str:
        lang = user.language_code or self.settings.DEFAULT_LANGUAGE
        text = self.i18n.gettext(lang, message_key, days=format_days(days, lang),
                                 end_date=format_long_date(new_end, lang))
        markup = self._gift_keyboard(lang, config_link)
        for attempt in range(2):
            try:
                await self.bot.send_message(user.user_id, text, reply_markup=markup)
                return "sent"
            except TelegramRetryAfter as e:
                if attempt:
                    return "failed"
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                return "blocked"
            except TelegramAPIError as e:
                logging.warning(f"Gift message to {user.user_id} rejected: {e}")
                return "failed"
        return "failed"

    async def run_group(self, code: str, group_no: int, dry_run: bool) -> RunResult:
        result = RunResult()
        async with self.session_factory() as session:
            campaign = await gift_campaign_dal.get_campaign_by_code(session, code)
            if not campaign:
                raise ValueError(f"campaign {code!r} not found")
            message_key = campaign.message_key
            if not dry_run and group_no != TEST_GROUP:
                # Stamped before the first grant, so a crash mid-group still counts as today's run.
                campaign.last_run_at = datetime.now(timezone.utc)
                await session.commit()
            grant_ids = [g.gift_grant_id for g in await gift_campaign_dal.get_pending_grants(
                session, campaign.gift_campaign_id, group_no)]

        panel_users = await self.panel_service.get_all_panel_users()
        if panel_users is None:
            raise RuntimeError("panel user list unavailable, nothing was changed")
        panel_by_id = {u["id"]: u for u in panel_users}

        for grant_id in grant_ids:
            async with self.session_factory() as session:
                grant = await session.get(GiftGrant, grant_id)
                user = await session.get(User, grant.user_id)
                panel_user = panel_by_id.get(user.panel_user_id) if user and user.panel_user_id else None

                reason = await self._skip_reason(session, grant, user, panel_user)
                if reason:
                    result.counts[f"skipped:{reason}"] += 1
                    if not dry_run:
                        grant.skip_reason = reason
                        await session.commit()
                    continue
                if dry_run:
                    result.counts[f"would_grant:{grant.segment}"] += 1
                    continue

                traffic = panel_user.get("userTraffic") or {}
                grant.baseline_online_at = _parse_dt(traffic.get("onlineAt"))
                grant.baseline_lifetime_traffic_bytes = _to_int(traffic.get("lifetimeUsedTrafficBytes"))
                grant.end_date_before = _parse_dt(panel_user.get("expireAt"))
                # "promo code" in the reason selects the paid-user traffic limit, as a promo
                # code activation does, instead of the trial one.
                new_end = await self.subscription_service.extend_active_subscription_days(
                    session, grant.user_id, grant.bonus_days, reason=f"promo code gift campaign {code}")
                problem = (await self._confirm_on_panel(panel_user["id"], new_end)
                           if new_end else "extend_failed")
                if problem:
                    await session.rollback()
                    grant = await session.get(GiftGrant, grant_id)
                    grant.skip_reason = problem
                    await session.commit()
                    result.counts[f"skipped:{problem}"] += 1
                    result.problems.append(f"user {grant.user_id}: {problem}")
                    continue
                grant.granted_at = datetime.now(timezone.utc)
                grant.end_date_after = new_end
                await session.commit()
                result.counts[f"granted:{grant.segment}"] += 1

                status = await self._send_gift_message(
                    message_key, user, grant.bonus_days, new_end, panel_user.get("subscriptionUrl"))
                grant.message_status = status
                grant.message_sent_at = datetime.now(timezone.utc) if status == "sent" else None
                await session.commit()
                result.counts[f"message:{status}"] += 1
            await asyncio.sleep(SEND_INTERVAL_SECONDS)
        return result

    # ---------- unattended daily run ----------

    async def _post_to_log(self, text: str) -> None:
        chat_id = self.settings.LOG_CHAT_ID
        if not chat_id:
            logging.warning("LOG_CHAT_ID is not set; gift campaign summary not posted")
            return
        try:
            await self.bot.send_message(chat_id, text, message_thread_id=self.settings.LOG_THREAD_ID)
        except TelegramAPIError as e:
            logging.error(f"Could not post gift campaign summary to the log chat: {e}")

    @staticmethod
    def _segment_line(rows: List[Dict[str, Any]], with_retained: bool = False) -> str:
        granted = [r for r in rows if r["granted_at"]]
        n = len(granted)
        connected = sum(1 for r in granted if r["connected"])
        renewed = sum(1 for r in granted if r["renewed"])
        line = (f"выдано {n} · зашли {connected} ({_pct(connected, n)})"
                f" · продлили {renewed} ({_pct(renewed, n)})")
        if with_retained:
            closed = [r for r in granted if r["retained"] is not None]
            kept = sum(1 for r in closed if r["retained"])
            line += f" · остались {kept} ({_pct(kept, len(closed))})"
        return line

    def _totals_block(self, rows: List[Dict[str, Any]], with_retained: bool = False) -> str:
        by_segment = {s: [r for r in rows if r["segment"] == s] for s in (SEGMENT_PAID, SEGMENT_TRIAL)}
        return "\n".join([
            f"всего: {self._segment_line(rows, with_retained)}",
            f"платили: {self._segment_line(by_segment[SEGMENT_PAID], with_retained)}",
            f"тест: {self._segment_line(by_segment[SEGMENT_TRIAL], with_retained)}",
        ])

    async def run_next(self, code: str, min_interval: timedelta) -> str:
        """Run the next pending group, unless halted or the previous group ran too recently.

        Once every group is out, posts the final report when the last renewal window closes.
        Returns a one-line outcome for the timer's journal; the owner reads the log chat.
        """
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            campaign = await gift_campaign_dal.get_campaign_by_code(session, code)
            if not campaign:
                raise ValueError(f"campaign {code!r} not found")
            if campaign.halted_reason:
                return f"halted since {campaign.halted_at:%Y-%m-%d %H:%M} UTC: {campaign.halted_reason}"
            all_groups, pending = await gift_campaign_dal.get_group_numbers(session, campaign.gift_campaign_id)
            planned = [g for g in all_groups if g != TEST_GROUP]
            pending = [g for g in pending if g != TEST_GROUP]
            title, last_run_at = campaign.title, campaign.last_run_at
            final_reported_at = campaign.final_reported_at

        if not pending:
            return await self._final_report_if_due(code, title, final_reported_at, now)
        if last_run_at and now - last_run_at < min_interval:
            return f"previous group ran at {last_run_at:%Y-%m-%d %H:%M} UTC, waiting"

        group_no = pending[0]
        try:
            result = await self.run_group(code, group_no, dry_run=False)
        except Exception as e:
            await self._post_to_log(
                f"🎁 <b>{escape(title)}</b>: группа {group_no} не выдана или выдана не полностью, "
                f"ошибка <code>{escape(str(e))}</code>. Остаток группы уйдёт в следующий запуск.")
            raise

        granted = {s: result.counts.get(f"granted:{s}", 0) for s in (SEGMENT_PAID, SEGMENT_TRIAL)}
        total_granted = sum(granted.values())
        failed = result.counts.get("message:failed", 0)
        halt = None
        if result.problems:
            halt = f"панель не приняла продление ({len(result.problems)} польз.)"
        elif total_granted and failed > max(5, total_granted // 10):
            halt = f"не отправлено {failed} из {total_granted} сообщений"
        if halt:
            async with self.session_factory() as session:
                campaign = await gift_campaign_dal.get_campaign_by_code(session, code)
                campaign.halted_reason, campaign.halted_at = halt, datetime.now(timezone.utc)
                await session.commit()

        skips = {k.split(":", 1)[1]: v for k, v in result.counts.items() if k.startswith("skipped:")}
        lines = [
            f"🎁 <b>{escape(title)}</b> — группа {group_no} из {len(planned)}",
            f"Выдано: {total_granted} (платили {granted[SEGMENT_PAID]} · тест {granted[SEGMENT_TRIAL]})",
            f"Пропущено: {sum(skips.values())}"
            + (f" ({', '.join(f'{k}={v}' for k, v in sorted(skips.items()))})" if skips else ""),
            f"Не доставлено: бот заблокирован {result.counts.get('message:blocked', 0)} · ошибка {failed}",
        ]
        if result.problems:
            shown = result.problems[:10]
            lines.append("Проблемы: " + "; ".join(escape(p) for p in shown)
                         + (f" и ещё {len(result.problems) - len(shown)}" if len(result.problems) > len(shown) else ""))
        try:
            rows = [r for r in await self.collect_outcomes(code) if r["group"] != TEST_GROUP]
            lines += ["", "<b>Итого по кампании</b>", self._totals_block(rows)]
        except Exception as e:
            logging.error(f"Gift campaign totals unavailable: {e}")
            lines += ["", "Итоги по кампании сейчас недоступны (панель не ответила)."]
        lines.append("")
        if halt:
            lines.append(f"⛔ Кампания остановлена: {halt}. Следующие группы не уйдут, пока стоп не снят "
                         f"(<code>resume --code {escape(code)}</code>).")
        elif len(pending) > 1:
            lines.append(f"Следующая группа — в следующий запуск, осталось групп: {len(pending) - 1}.")
        else:
            lines.append("Все группы выданы. Итоговый отчёт придёт сюда, когда у последней группы "
                         "пройдут 14 дней после подарка.")
        await self._post_to_log("\n".join(lines))
        return f"group {group_no}: granted {total_granted}" + (f", HALTED: {halt}" if halt else "")

    async def _final_report_if_due(self, code: str, title: str, final_reported_at: Optional[datetime],
                                   now: datetime) -> str:
        if final_reported_at:
            return "campaign complete, final report already posted"
        rows = await self.collect_outcomes(code)
        granted = [r for r in rows if r["granted_at"] and r["group"] != TEST_GROUP]
        if not granted:
            return "nothing was granted, no report"
        due = max(r["end_after"] for r in granted) + RENEWAL_WINDOW
        if now < due:
            return f"all groups out, final report due {due:%Y-%m-%d %H:%M} UTC"

        campaign_rows = [r for r in rows if r["group"] != TEST_GROUP]
        undelivered = sum(1 for r in granted if r["message"] in ("blocked", "failed"))
        await self._post_to_log("\n".join([
            f"📊 <b>{escape(title)}</b> — итоговый отчёт",
            f"В плане {len(campaign_rows)}, получили подарок {len(granted)}, "
            f"не доставлено сообщение {undelivered}.",
            "",
            self._totals_block(campaign_rows, with_retained=True),
            "",
            "Зашли — подключались за время подарка. Продлили — оплатили до 14 дней после его конца. "
            "Остались — через 14 дней после подарка подписка оплачена.",
        ]))
        async with self.session_factory() as session:
            campaign = await gift_campaign_dal.get_campaign_by_code(session, code)
            campaign.final_reported_at = datetime.now(timezone.utc)
            await session.commit()
        return "final report posted"

    async def resume(self, code: str) -> Optional[str]:
        """Lift a halt. Returns the reason that was cleared, or None if the campaign was running."""
        async with self.session_factory() as session:
            campaign = await gift_campaign_dal.get_campaign_by_code(session, code)
            if not campaign:
                raise ValueError(f"campaign {code!r} not found")
            reason = campaign.halted_reason
            campaign.halted_reason = campaign.halted_at = None
            await session.commit()
            return reason

    # ---------- analytics ----------

    async def collect_outcomes(self, code: str) -> List[Dict[str, Any]]:
        """One row per planned recipient with what happened and how they responded."""
        async with self.session_factory() as session:
            campaign = await gift_campaign_dal.get_campaign_by_code(session, code)
            if not campaign:
                raise ValueError(f"campaign {code!r} not found")
            grants = await gift_campaign_dal.get_all_grants(session, campaign.gift_campaign_id)
            granted = [g for g in grants if g.granted_at]
            user_ids = [g.user_id for g in grants]
            since = min((g.granted_at for g in granted), default=datetime.now(timezone.utc))
            payments = await gift_campaign_dal.get_paid_payment_times(session, user_ids, since)
            latest_end = await gift_campaign_dal.get_latest_end_dates(session, user_ids)
            panel_ids = await gift_campaign_dal.get_panel_user_ids(session, user_ids)

        panel_users = await self.panel_service.get_all_panel_users()
        if panel_users is None:
            raise RuntimeError("panel user list unavailable")
        panel_by_id = {u["id"]: u for u in panel_users}

        now = datetime.now(timezone.utc)
        rows = []
        for g in grants:
            panel_user = panel_by_id.get(panel_ids.get(g.user_id))
            traffic = (panel_user or {}).get("userTraffic") or {}
            online_at = _parse_dt(traffic.get("onlineAt"))
            lifetime = _to_int(traffic.get("lifetimeUsedTrafficBytes"))

            connected = renewed = retained = None
            if g.granted_at:
                traffic_grew = (lifetime is not None and g.baseline_lifetime_traffic_bytes is not None
                                and lifetime > g.baseline_lifetime_traffic_bytes)
                connected = bool((online_at and online_at > g.granted_at) or traffic_grew)
                window_end = g.end_date_after + RENEWAL_WINDOW
                renewed = any(g.granted_at <= t <= window_end for t in payments.get(g.user_id, []))
                if now >= window_end:
                    end = latest_end.get(g.user_id)
                    retained = bool(renewed and end and end > window_end)
            rows.append({
                "user_id": g.user_id,
                "group": g.group_no,
                "segment": g.segment,
                "days": g.bonus_days,
                "comment": g.comment,
                "granted_at": g.granted_at,
                "skip_reason": g.skip_reason,
                "message": g.message_status,
                "end_before": g.end_date_before,
                "end_after": g.end_date_after,
                "connected": connected,
                "traffic_bytes_since_grant": (lifetime - g.baseline_lifetime_traffic_bytes
                                              if g.granted_at and lifetime is not None
                                              and g.baseline_lifetime_traffic_bytes is not None else None),
                "renewed": renewed,
                "retained": retained,
            })
        return rows
