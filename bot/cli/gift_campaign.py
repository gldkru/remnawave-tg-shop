"""Gift campaign CLI. Run inside the bot container:

    docker exec remnawave-tg-shop python -m bot.cli.gift_campaign <command> ...

    plan    freeze today's lapsed users into groups (--dry-run prints the split only)
    add     put one user into a group, e.g. staff into group 0 for a live test
    run     grant days and send the message to one group (--dry-run lists who would get it);
            --next is the unattended daily form: next pending group, halts itself on errors,
            posts a summary to the log chat, and the final report once the last window closes
    resume  lift a halt set by run --next
    report  per-group, per-segment outcome table
    export  per-user outcome rows as CSV on stdout
"""
import argparse
import asyncio
import csv
import logging
import sys
from collections import Counter, defaultdict
from datetime import timedelta

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.middlewares.i18n import get_i18n_instance
from bot.services.gift_campaign_service import GiftCampaignService
from bot.services.panel_api_service import PanelApiService
from bot.services.subscription_service import SubscriptionService
from config.settings import get_settings
from db.dal.gift_campaign_dal import SEGMENT_PAID, SEGMENT_TRIAL
from db.database_setup import init_db, init_db_connection


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m bot.cli.gift_campaign")
    sub = p.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan")
    plan.add_argument("--code", required=True)
    plan.add_argument("--title", required=True, help="human label, goes into every grant comment")
    plan.add_argument("--message-key", required=True, help="locale key of the gift message")
    plan.add_argument("--paid-days", type=int, required=True)
    plan.add_argument("--trial-days", type=int, required=True)
    plan.add_argument("--group-size", type=int, default=200)
    plan.add_argument("--seed", type=int, required=True, help="makes the random split reproducible")
    plan.add_argument("--dry-run", action="store_true")

    add = sub.add_parser("add")
    add.add_argument("--code", required=True)
    add.add_argument("--user", type=int, required=True, help="Telegram user id")
    add.add_argument("--segment", choices=[SEGMENT_PAID, SEGMENT_TRIAL], required=True)
    add.add_argument("--days", type=int, required=True)
    add.add_argument("--group", type=int, default=0)

    run = sub.add_parser("run")
    run.add_argument("--code", required=True)
    target = run.add_mutually_exclusive_group(required=True)
    target.add_argument("--group", type=int)
    target.add_argument("--next", action="store_true", help="next pending group, for a daily timer")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--min-interval-hours", type=float, default=20,
                     help="with --next: skip if the previous group ran more recently than this")

    for name in ("resume", "report", "export"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--code", required=True)
    return p


def _print_plan(counts: Counter, dry_run: bool) -> None:
    groups = sorted({g for g, _ in counts})
    print(f"{'DRY RUN, nothing written' if dry_run else 'planned'}: "
          f"{sum(counts.values())} users in {len(groups)} groups")
    print(f"{'group':>5} {'paid':>6} {'trial':>6} {'total':>6}")
    for g in groups:
        paid, trial = counts[(g, SEGMENT_PAID)], counts[(g, SEGMENT_TRIAL)]
        print(f"{g:>5} {paid:>6} {trial:>6} {paid + trial:>6}")


def _print_report(rows) -> None:
    def tally(subset):
        granted = [r for r in subset if r["granted_at"]]
        closed = [r for r in granted if r["retained"] is not None]
        return [
            len(subset),
            len(granted),
            sum(1 for r in subset if r["skip_reason"]),
            sum(1 for r in granted if r["message"] == "sent"),
            sum(1 for r in granted if r["message"] in ("blocked", "failed")),
            sum(1 for r in granted if r["connected"]),
            sum(1 for r in granted if r["renewed"]),
            f"{sum(1 for r in closed if r['retained'])}/{len(closed)}",
        ]

    header = ["group", "segment", "planned", "granted", "skipped", "sent", "undeliv",
              "connected", "renewed", "retained"]
    print(" ".join(f"{h:>9}" for h in header))
    buckets = defaultdict(list)
    for r in rows:
        buckets[(r["group"], r["segment"])].append(r)
        buckets[("all", r["segment"])].append(r)
        buckets[("all", "all")].append(r)
    order = sorted((k for k in buckets if k[0] != "all")) + \
        [("all", SEGMENT_PAID), ("all", SEGMENT_TRIAL), ("all", "all")]
    for key in order:
        if key in buckets:
            print(" ".join(f"{str(v):>9}" for v in [*key, *tally(buckets[key])]))
    skips = Counter(r["skip_reason"] for r in rows if r["skip_reason"])
    if skips:
        print("skip reasons: " + ", ".join(f"{k}={v}" for k, v in skips.most_common()))
    print("retained = renewed and still paid 14 days after the gift ended; "
          "shown as kept/closed, where closed counts users whose 14 days are over")


async def main() -> int:
    args = _parser().parse_args()
    settings = get_settings()
    session_factory = init_db_connection(settings)
    await init_db(settings, session_factory)

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    panel_service = PanelApiService(settings)
    i18n = get_i18n_instance(path="locales", default=settings.DEFAULT_LANGUAGE)
    service = GiftCampaignService(
        settings, bot, i18n, session_factory, panel_service,
        SubscriptionService(settings, panel_service, bot, i18n))
    try:
        if args.command == "plan":
            counts = await service.plan(
                args.code, args.title, args.message_key,
                {SEGMENT_PAID: args.paid_days, SEGMENT_TRIAL: args.trial_days},
                args.group_size, args.seed, args.dry_run)
            _print_plan(counts, args.dry_run)
        elif args.command == "add":
            await service.add_user(args.code, args.user, args.segment, args.days, args.group)
            print(f"added user {args.user} to group {args.group}: +{args.days} days, {args.segment}")
        elif args.command == "run" and args.next:
            if args.dry_run:
                raise ValueError("--next has no dry run; use --group N --dry-run")
            print(await service.run_next(args.code, timedelta(hours=args.min_interval_hours)))
        elif args.command == "run":
            result = await service.run_group(args.code, args.group, args.dry_run)
            print(f"{'DRY RUN, nothing changed' if args.dry_run else 'done'}: group {args.group}")
            for key, value in sorted(result.counts.items()):
                print(f"  {key}: {value}")
            for problem in result.problems:
                print(f"  problem: {problem}")
            if not result.counts:
                print("  nothing pending in this group")
        elif args.command == "resume":
            reason = await service.resume(args.code)
            print(f"resumed, cleared halt: {reason}" if reason else "campaign was not halted")
        elif args.command == "report":
            _print_report(await service.collect_outcomes(args.code))
        elif args.command == "export":
            rows = await service.collect_outcomes(args.code)
            if rows:
                writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    finally:
        await panel_service.close_session()
        await bot.session.close()
    return 0


if __name__ == "__main__":
    # Root stays at WARNING on purpose: init_db_connection logs the full DATABASE_URL at INFO,
    # and this output is read by people and agents.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        sys.exit(asyncio.run(main()))
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
