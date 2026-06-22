from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.db import SessionLocal
from bot.services import add_borsh, display_name, find_user_in_chat, get_top, get_user_stat, set_custom_username, upsert_chat, upsert_user, get_or_create_stat

router = Router()


HELP_TEXT = """
🍲 БОРЩЕБОТ — групповой учет борщей

Команды:
/borsh — съел борщ, +1 в текущем чате
/stat — топ-10 борщеедов
/stat 5 — топ-5 борщеедов
/stat username — подробная статистика пользователя
/stat 123456789 — статистика по Telegram ID
/username vasya — задать имя для статистики
/me — моя статистика

Статистика считается отдельно для каждого чата.
""".strip()


@router.message(Command("start", "help"))
async def help_cmd(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("borsh"))
async def borsh_cmd(message: Message) -> None:
    if not message.from_user:
        return
    async with SessionLocal() as session:
        total = await add_borsh(session, message.chat, message.from_user)
    await message.answer(f"🍲 Засчитано! Теперь у тебя {total} борщ(ей) в этом чате. Борщевой дух крепнет!")


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
        await message.answer("🍲 Пока борщей нет. Кто первым отправит /borsh?")
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
