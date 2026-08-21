from aiogram import Router, F

from bot.handlers.user import user_router_aggregate
from bot.handlers import inline_mode
from bot.handlers import moderator as moderator_handlers
from bot.handlers.admin import admin_router_aggregate, staff_router_aggregate
from bot.filters.admin_filter import AdminFilter, ModeratorFilter, StaffFilter
from config.settings import Settings


def build_root_router(settings: Settings) -> Router:
    root = Router(name="root")

    # Allow all updates only in private chats (messages, callback queries, etc.)
    root.message.filter(F.chat.type == "private")
    root.callback_query.filter(F.message.chat.type == "private")

    # Public routers
    root.include_router(user_router_aggregate)
    root.include_router(inline_mode.router)

    # Moderator entry point. Registered before the admin panel so that its own
    # namespace stays separate; the filter excludes admins, so no update matches both.
    moderator_main_router = Router(name="moderator_main_filtered_router")
    moderator_filter_instance = ModeratorFilter(moderator_ids=settings.MODERATOR_IDS)
    moderator_main_router.message.filter(moderator_filter_instance)
    moderator_main_router.callback_query.filter(moderator_filter_instance)
    moderator_main_router.include_router(moderator_handlers.router)
    root.include_router(moderator_main_router)

    # The single-user surface both roles share. Authorization per action lives in the
    # handlers, because a hidden button is not a permission.
    staff_main_router = Router(name="staff_main_filtered_router")
    staff_filter_instance = StaffFilter(admin_ids=settings.ADMIN_IDS,
                                       moderator_ids=settings.MODERATOR_IDS)
    staff_main_router.message.filter(staff_filter_instance)
    staff_main_router.callback_query.filter(staff_filter_instance)
    staff_main_router.include_router(staff_router_aggregate)
    root.include_router(staff_main_router)

    # Admin routers behind filter
    admin_main_router = Router(name="admin_main_filtered_router")
    admin_filter_instance = AdminFilter(admin_ids=settings.ADMIN_IDS)
    admin_main_router.message.filter(admin_filter_instance)
    admin_main_router.callback_query.filter(admin_filter_instance)
    admin_main_router.include_router(admin_router_aggregate)
    root.include_router(admin_main_router)

    return root

