import logging
from typing import Optional

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from config.settings import Settings
from bot.keyboards.inline.admin_keyboards import get_moderator_panel_keyboard
from bot.middlewares.i18n import JsonI18n
from bot.states.admin_states import AdminStates

router = Router(name="moderator_router")


@router.message(Command("admin", "moderator"))
async def moderator_panel_command_handler(message: types.Message, state: FSMContext,
                                          settings: Settings, i18n_data: dict):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n:
        logging.error("i18n missing in moderator_panel_command_handler")
        await message.answer("Language service error.")
        return

    await state.clear()
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)
    await message.answer(_(key="moderator_panel_title"),
                         reply_markup=get_moderator_panel_keyboard(i18n, current_lang))


@router.callback_query(F.data == "moderator_action:main")
async def moderator_panel_callback_handler(callback: types.CallbackQuery, state: FSMContext,
                                           settings: Settings, i18n_data: dict):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n or not callback.message:
        await callback.answer("Language service error.", show_alert=True)
        return

    await state.clear()
    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)
    markup = get_moderator_panel_keyboard(i18n, current_lang)
    try:
        await callback.message.edit_text(_(key="moderator_panel_title"), reply_markup=markup)
    except Exception:
        await callback.message.answer(_(key="moderator_panel_title"), reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "moderator_action:find_user")
async def moderator_find_user_handler(callback: types.CallbackQuery, state: FSMContext,
                                      settings: Settings, i18n_data: dict):
    current_lang = i18n_data.get("current_language", settings.DEFAULT_LANGUAGE)
    i18n: Optional[JsonI18n] = i18n_data.get("i18n_instance")
    if not i18n or not callback.message:
        await callback.answer("Language service error.", show_alert=True)
        return

    _ = lambda key, **kwargs: i18n.gettext(current_lang, key, **kwargs)
    prompt_text = _("admin_user_management_prompt")
    markup = get_moderator_panel_keyboard(i18n, current_lang)
    try:
        await callback.message.edit_text(prompt_text, reply_markup=markup)
    except Exception:
        await callback.message.answer(prompt_text, reply_markup=markup)

    await callback.answer()
    await state.set_state(AdminStates.waiting_for_user_search)
