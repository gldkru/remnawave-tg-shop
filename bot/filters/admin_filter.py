from typing import List, Union
from aiogram.filters import Filter
from aiogram.types import Message, CallbackQuery, User


class AdminFilter(Filter):

    def __init__(self, admin_ids: List[int]):
        self.admin_ids = admin_ids

    async def __call__(self, event: Union[Message, CallbackQuery],
                       event_from_user: User) -> bool:
        if not event_from_user:
            return False
        if not self.admin_ids:
            return False
        return event_from_user.id in self.admin_ids


class StaffFilter(Filter):
    """Admins and moderators alike — the surface both roles share."""

    def __init__(self, admin_ids: List[int], moderator_ids: List[int]):
        self.staff_ids = set(admin_ids) | set(moderator_ids)

    async def __call__(self, event: Union[Message, CallbackQuery],
                       event_from_user: User) -> bool:
        if not event_from_user:
            return False
        return event_from_user.id in self.staff_ids


class ModeratorFilter(Filter):
    """Moderators only. An admin is deliberately excluded: they get the full panel,
    and matching both would hand the same update to two routers."""

    def __init__(self, moderator_ids: List[int]):
        self.moderator_ids = set(moderator_ids)

    async def __call__(self, event: Union[Message, CallbackQuery],
                       event_from_user: User) -> bool:
        if not event_from_user:
            return False
        return event_from_user.id in self.moderator_ids
