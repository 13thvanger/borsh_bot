import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import settings
from bot.db import init_db
from bot.handlers import router


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    await init_db()

    if settings.agent_enabled:
        logging.info("AI verification enabled: model=%s url=%s", settings.agent_model, settings.agent_url)
    elif settings.agent_required:
        logging.warning("AI verification required but disabled: %s", settings.agent_disabled_reason)
    else:
        logging.warning("AI verification disabled: %s. /borsh will work without photo confirmation.", settings.agent_disabled_reason)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
