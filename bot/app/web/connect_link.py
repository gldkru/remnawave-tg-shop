import html
import logging

from aiohttp import web

from bot.services.analytics import track
from bot.utils.connect_link import CONNECT_PATH_PREFIX, verify_connect_token

CONNECT_ROUTE = CONNECT_PATH_PREFIX + "{token}"
_NO_STORE = {"Cache-Control": "no-store"}


async def connect_link_route(request: web.Request) -> web.StreamResponse:
    """Redirect a tracked connect button to the user's current subscription URL."""
    settings = request.app["settings"]
    user_id = verify_connect_token(settings.CONNECT_LINK_SECRET, request.match_info["token"])
    if user_id is None:
        return await _press_again_page(request, status=410)

    async with request.app["async_session_factory"]() as session:
        subscription_url = await request.app["subscription_service"].get_current_subscription_url(
            session, user_id)
    track(user_id, "connect_link_opened",
          result="redirect" if subscription_url else "no_subscription")
    if not subscription_url:
        return await _press_again_page(request, status=404)
    raise web.HTTPFound(subscription_url, headers=_NO_STORE)


async def _press_again_page(request: web.Request, status: int) -> web.Response:
    i18n = request.app["i18n"]
    paragraphs = "".join(
        f"<p>{html.escape(i18n.gettext(lang, 'connect_link_press_again'))}</p>"
        for lang in ("ru", "en"))
    bot_link = ""
    try:
        bot_username = (await request.app["bot"].me()).username
        if bot_username:
            bot_link = f'<p><a href="https://t.me/{html.escape(bot_username)}">@{html.escape(bot_username)}</a></p>'
    except Exception:
        logging.warning("Connect link page: could not resolve the bot username", exc_info=True)
    body = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
        '<body style="font-family: system-ui, sans-serif; max-width: 480px; margin: 48px auto; '
        f'padding: 0 16px; line-height: 1.5">{paragraphs}{bot_link}</body></html>'
    )
    return web.Response(text=body, status=status, content_type="text/html", headers=_NO_STORE)
