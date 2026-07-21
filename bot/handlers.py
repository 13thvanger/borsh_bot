import logging
from datetime import timedelta

import httpx
from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.ai_agent import check_borsh_image, telegram_photo_to_data_url
from bot.config import settings
from bot.db import SessionLocal
from bot.messages import random_message
from bot.services import (
    adjust_borsh_for_user,
    add_borsh,
    display_name,
    find_user,
    find_user_in_chat,
    get_or_create_stat,
    get_photo_by_message_id,
    get_recent_photo_candidates,
    get_top,
    get_user_stat,
    photo_already_confirmed,
    remember_photo_message,
    save_borsh_proof_and_increment,
    set_custom_username,
    upsert_chat,
    upsert_user,
)

router = Router()
logger = logging.getLogger(__name__)


HELP_TEXT = """
🍲 БОРЩЕБОТ — групповой учет борщей

Команды:
/borsh — добавить +1 борщ. Если ИИ-проверка включена, бот проверит твое последнее свежее фото.
/borsh в ответ на фото — проверить именно это фото
/stat — топ-10 борщеедов
/stat 5 — топ-5 борщеедов
/stat username — подробная статистика пользователя
/stat 123456789 — статистика по Telegram ID
/username vasya — задать имя для статистики
/me — моя статистика
/health — состояние бота, БД и ИИ-проверки
/addborsh N user — админская команда: изменить число борщей пользователя на N по Telegram ID или username

Если AGENT_URL и AGENT_API_KEY не заданы, ИИ-проверка отключается и /borsh засчитывает борщ без фото.
Одно и то же фото нельзя засчитать дважды.
Статистика считается отдельно для каждого чата.
""".strip()


@router.message(Command("start", "help"))
async def help_cmd(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(lambda message: bool(message.photo))
async def photo_message(message: Message) -> None:
    if not message.from_user or not message.photo:
        return
    biggest_photo = message.photo[-1]
    async with SessionLocal() as session:
        await remember_photo_message(
            session=session,
            tg_chat=message.chat,
            tg_user=message.from_user,
            message_id=message.message_id,
            file_id=biggest_photo.file_id,
            file_unique_id=biggest_photo.file_unique_id,
            message_date=message.date,
        )


@router.message(Command("borsh"))
async def borsh_cmd(message: Message, bot: Bot) -> None:
    if not message.from_user:
        return

    # Если ИИ-агент не настроен и AGENT_REQUIRED=false, работаем в режиме доверия.
    if not settings.agent_enabled:
        if settings.agent_required:
            await message.answer(
                "🤖 ИИ-проверка обязательна, но агент не настроен: "
                f"{settings.agent_disabled_reason}. Борщ не засчитан."
            )
            return
        async with SessionLocal() as session:
            total = await add_borsh(session, message.chat, message.from_user)
        await message.answer(random_message("bypass.txt", total=total))
        return

    async with SessionLocal() as session:
        photo = None
        reply = message.reply_to_message
        if reply and reply.photo:
            if not reply.from_user or reply.from_user.id != message.from_user.id:
                await message.answer("Можно засчитать только свой борщ: ответь /borsh на свое фото.")
                return
            biggest_photo = reply.photo[-1]
            await remember_photo_message(
                session=session,
                tg_chat=message.chat,
                tg_user=message.from_user,
                message_id=reply.message_id,
                file_id=biggest_photo.file_id,
                file_unique_id=biggest_photo.file_unique_id,
                message_date=reply.date,
            )
            photo = await get_photo_by_message_id(session, message.chat, message.from_user, reply.message_id)
        else:
            cutoff = message.date - timedelta(minutes=settings.borsh_photo_window_minutes)
            photos = await get_recent_photo_candidates(
                session,
                message.chat,
                message.from_user,
                message.message_id,
                limit=1,
                since=cutoff,
            )
            photo = photos[0] if photos else None

        if photo is None:
            await message.answer(random_message("no_photo.txt"))
            return

        if await photo_already_confirmed(session, photo):
            await message.answer(random_message("already_counted.txt"))
            return

        try:
            data_url = await telegram_photo_to_data_url(bot, photo.telegram_file_id)
            is_borsh, confidence, reason, raw_response = await check_borsh_image(data_url)
        except httpx.TimeoutException as exc:
            logger.warning(
                "AI verification timed out: chat_id=%s user_id=%s command_message_id=%s photo_message_id=%s bypass=%s",
                message.chat.id,
                message.from_user.id,
                message.message_id,
                photo.telegram_message_id,
                settings.agent_timeout_bypass,
            )
            if not settings.agent_timeout_bypass:
                await message.answer(random_message("agent_error.txt", error=f"{type(exc).__name__}: {exc}"))
                return

            total = await save_borsh_proof_and_increment(
                session=session,
                tg_chat=message.chat,
                tg_user=message.from_user,
                photo=photo,
                command_message_id=message.message_id,
                confirmed=True,
                agent_response=f"AI timeout fallback: {type(exc).__name__}: {exc}",
            )
            if total is None:
                await message.answer(random_message("already_counted.txt"))
                return

            await message.answer(random_message("agent_timeout_bypass.txt", total=total))
            return
        except Exception as exc:
            logger.exception(
                "AI verification failed: chat_id=%s user_id=%s command_message_id=%s photo_message_id=%s error=%s",
                message.chat.id,
                message.from_user.id,
                message.message_id,
                photo.telegram_message_id,
                exc,
            )
            await message.answer(random_message("agent_error.txt", error=f"{type(exc).__name__}: {exc}"))
            return

        total = await save_borsh_proof_and_increment(
            session=session,
            tg_chat=message.chat,
            tg_user=message.from_user,
            photo=photo,
            command_message_id=message.message_id,
            confirmed=is_borsh,
            agent_response=raw_response,
        )

        if is_borsh and total is not None:
            await message.answer(
                random_message("confirmed.txt", total=total, confidence=f"{confidence:.0%}", reason=reason)
                + f"\n\n🤖 Вердикт: борщ. Уверенность в распознавании: {confidence:.0%}."
            )
            return

        if is_borsh and total is None:
            await message.answer(random_message("already_counted.txt"))
            return

        verdict = f"🤖 Вердикт: не борщ. Уверенность в распознавании: {confidence:.0%}."
        if reason:
            verdict += f"\n🥣 Вероятно, это {reason}."
        await message.answer(random_message("rejected.txt") + f"\n\n{verdict}")


@router.message(Command("health"))
async def health_cmd(message: Message) -> None:
    db_ok = False
    try:
        async with SessionLocal() as session:
            await upsert_chat(session, message.chat)
            await session.commit()
        db_ok = True
    except Exception:
        db_ok = False

    if settings.agent_enabled:
        ai_status = f"ENABLED, model={settings.agent_model}"
    elif settings.agent_required:
        ai_status = f"REQUIRED BUT DISABLED, {settings.agent_disabled_reason}"
    else:
        ai_status = f"DISABLED, {settings.agent_disabled_reason}; /borsh works without photo confirmation"

    await message.answer(
        "🍲 Borsh Bot health\n\n"
        f"Database: {'OK' if db_ok else 'ERROR'}\n"
        f"AI verification: {ai_status}"
    )


@router.message(Command("username"))
async def username_cmd(message: Message, command: CommandObject) -> None:
    if not message.from_user:
        return
    if not command.args:
        await message.answer("Использование: /username vasya")
        return
    async with SessionLocal() as session:
        try:
            username = await set_custom_username(session, message.from_user, command.args)
        except ValueError as exc:
            await message.answer(f"Не получилось: {exc}")
            return
    await message.answer(f"🪪 Имя для борщевой статистики установлено: {username}")


@router.message(Command("addborsh"))
async def addborsh_cmd(message: Message, command: CommandObject) -> None:
    if not message.from_user:
        return
    if message.from_user.id not in settings.admin_user_ids:
        await message.answer("Нет прав для ручного добавления борщей.")
        return

    args = (command.args or "").strip().split(maxsplit=1)
    if len(args) != 2:
        await message.answer("Использование: /addborsh N user, например /addborsh 3 @ivan или /addborsh -1 @ivan")
        return

    amount_raw, user_query = args
    try:
        amount = int(amount_raw)
    except ValueError:
        await message.answer("N должно быть целым числом.")
        return

    if amount == 0:
        await message.answer("N не должно быть равно 0.")
        return
    if abs(amount) > settings.manual_add_borsh_max:
        await message.answer(f"|N| слишком большое. Максимум: {settings.manual_add_borsh_max}.")
        return

    async with SessionLocal() as session:
        user = await find_user(session, user_query)
        if user is None:
            await message.answer("Не нашел пользователя бота по Telegram ID или username.")
            return

        try:
            total = await adjust_borsh_for_user(session, message.chat, user, amount)
        except ValueError as exc:
            await message.answer(f"Не получилось: {exc}")
            return

    logger.info(
        "Admin adjusted borsh: admin_user_id=%s chat_id=%s target_user_id=%s amount=%s total=%s",
        message.from_user.id,
        message.chat.id,
        user.telegram_user_id,
        amount,
        total,
    )
    sign = "+" if amount > 0 else ""
    await message.answer(f"Изменено: {sign}{amount} для {display_name(user)}. Теперь в этом чате: {total}.")


@router.message(Command("stat"))
async def stat_cmd(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    async with SessionLocal() as session:
        if not arg:
            await send_top(message, session, 10)
            return
        if arg.isdigit() and int(arg) <= 50:
            await send_top(message, session, int(arg))
            return

        found = await find_user_in_chat(session, message.chat, arg)
        if not found:
            await message.answer("Не нашел такого борщееда в текущем чате. Попробуй username без @ или Telegram ID.")
            return
        user, stat = found
        data = await get_user_stat(session, message.chat, user, stat)
    await message.answer(format_user_stat(data))


@router.message(Command("me"))
async def me_cmd(message: Message) -> None:
    if not message.from_user:
        return
    async with SessionLocal() as session:
        user = await upsert_user(session, message.from_user)
        chat = await upsert_chat(session, message.chat)
        stat = await get_or_create_stat(session, chat, user)
        data = await get_user_stat(session, message.chat, user, stat)
        await session.commit()
    await message.answer(format_user_stat(data))


async def send_top(message: Message, session, limit: int) -> None:
    rows = await get_top(session, message.chat, limit)
    if not rows:
        await message.answer("🍲 Пока борщей нет. Кто первым отправит фото борща и /borsh?")
        return
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = [f"🍲 Топ-{limit} борщеедов этого чата:"]
    for place, user, count in rows:
        icon = medals.get(place, f"{place}.")
        lines.append(f"{icon} {display_name(user)} — {count}")
    await message.answer("\n".join(lines))


def format_user_stat(data: dict) -> str:
    first = data["first_borsh_at"].strftime("%Y-%m-%d %H:%M UTC") if data["first_borsh_at"] else "нет"
    last = data["last_borsh_at"].strftime("%Y-%m-%d %H:%M UTC") if data["last_borsh_at"] else "нет"
    best_day = f"{data['best_day']} — {data['best_day_count']}" if data["best_day"] else "нет"
    litres = data["total"] * 0.4

    return (
        f"🍲 Подробная борщевая статистика: {data['name']}\n\n"
        f"🏆 Место в чате: {data['rank']}\n"
        f"🧮 Всего борщей: {data['total']}\n"
        f"📅 Сегодня: {data['today']}\n"
        f"🗓 За неделю: {data['week']}\n"
        f"🌙 За месяц: {data['month']}\n"
        f"📈 Среднее в день: {data['avg_per_day']:.2f}\n"
        f"🔥 Активных борщевых дней: {data['active_days']}\n"
        f"🚀 Лучший день: {best_day}\n"
        f"🥣 Примерный объем борща: {litres:.1f} л, если считать по 400 мл за порцию\n"
        f"🌱 Первый борщ: {first}\n"
        f"⏰ Последний борщ: {last}\n"
        f"🆔 Telegram ID: {data['telegram_user_id']}"
    )
