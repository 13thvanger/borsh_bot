from datetime import date, datetime, timedelta, timezone

from aiogram.types import Chat as TgChat, User as TgUser
from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import BorshEvent, Chat, ChatUserStat, User


def display_name(user: User) -> str:
    if user.custom_username:
        return user.custom_username
    if user.telegram_username:
        return f"@{user.telegram_username}"
    parts = [user.first_name, user.last_name]
    full_name = " ".join(p for p in parts if p)
    return full_name or str(user.telegram_user_id)


async def upsert_user(session: AsyncSession, tg_user: TgUser) -> User:
    result = await session.execute(select(User).where(User.telegram_user_id == tg_user.id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            telegram_user_id=tg_user.id,
            telegram_username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
        )
        session.add(user)
        await session.flush()
    else:
        user.telegram_username = tg_user.username
        user.first_name = tg_user.first_name
        user.last_name = tg_user.last_name
    return user


async def upsert_chat(session: AsyncSession, tg_chat: TgChat) -> Chat:
    result = await session.execute(select(Chat).where(Chat.telegram_chat_id == tg_chat.id))
    chat = result.scalar_one_or_none()
    if chat is None:
        chat = Chat(telegram_chat_id=tg_chat.id, title=tg_chat.title, type=tg_chat.type)
        session.add(chat)
        await session.flush()
    else:
        chat.title = tg_chat.title
        chat.type = tg_chat.type
    return chat


async def get_or_create_stat(session: AsyncSession, chat: Chat, user: User) -> ChatUserStat:
    result = await session.execute(
        select(ChatUserStat).where(ChatUserStat.chat_id == chat.id, ChatUserStat.user_id == user.id)
    )
    stat = result.scalar_one_or_none()
    if stat is None:
        stat = ChatUserStat(chat_id=chat.id, user_id=user.id, borsh_count=0)
        session.add(stat)
        await session.flush()
    return stat


async def add_borsh(session: AsyncSession, tg_chat: TgChat, tg_user: TgUser) -> int:
    user = await upsert_user(session, tg_user)
    chat = await upsert_chat(session, tg_chat)
    stat = await get_or_create_stat(session, chat, user)

    now = datetime.now(timezone.utc)
    stat.borsh_count += 1
    stat.last_borsh_at = now
    if stat.first_borsh_at is None:
        stat.first_borsh_at = now
    session.add(BorshEvent(chat_id=chat.id, user_id=user.id))
    await session.commit()
    return stat.borsh_count


async def set_custom_username(session: AsyncSession, tg_user: TgUser, username: str) -> str:
    username = username.strip().lstrip("@")
    if not username or len(username) > 64 or " " in username:
        raise ValueError("username должен быть без пробелов и не длиннее 64 символов")
    user = await upsert_user(session, tg_user)
    user.custom_username = username
    await session.commit()
    return username


async def get_top(session: AsyncSession, tg_chat: TgChat, limit: int) -> list[tuple[int, User, int]]:
    chat = await upsert_chat(session, tg_chat)
    limit = max(1, min(limit, 50))
    result = await session.execute(
        select(ChatUserStat, User)
        .join(User, User.id == ChatUserStat.user_id)
        .where(ChatUserStat.chat_id == chat.id, ChatUserStat.borsh_count > 0)
        .order_by(ChatUserStat.borsh_count.desc(), ChatUserStat.last_borsh_at.asc())
        .limit(limit)
    )
    rows = result.all()
    return [(idx, user, stat.borsh_count) for idx, (stat, user) in enumerate(rows, start=1)]


async def find_user_in_chat(session: AsyncSession, tg_chat: TgChat, query: str) -> tuple[User, ChatUserStat] | None:
    chat = await upsert_chat(session, tg_chat)
    q = query.strip().lstrip("@")

    conditions = []
    if q.isdigit():
        conditions.append(User.telegram_user_id == int(q))
    conditions.extend([
        func.lower(User.telegram_username) == q.lower(),
        func.lower(User.custom_username) == q.lower(),
    ])

    result = await session.execute(
        select(User, ChatUserStat)
        .join(ChatUserStat, ChatUserStat.user_id == User.id)
        .where(ChatUserStat.chat_id == chat.id, or_(*conditions))
        .limit(1)
    )
    return result.first()


async def get_user_stat(session: AsyncSession, tg_chat: TgChat, user: User, stat: ChatUserStat) -> dict:
    chat = await upsert_chat(session, tg_chat)
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    week_start = today_start - timedelta(days=now.weekday())
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

    async def count_since(dt: datetime) -> int:
        result = await session.execute(
            select(func.count(BorshEvent.id)).where(
                BorshEvent.chat_id == chat.id,
                BorshEvent.user_id == user.id,
                BorshEvent.created_at >= dt,
            )
        )
        return int(result.scalar() or 0)

    today = await count_since(today_start)
    week = await count_since(week_start)
    month = await count_since(month_start)

    total_days = 1
    if stat.first_borsh_at:
        total_days = max(1, (now.date() - stat.first_borsh_at.date()).days + 1)
    avg_per_day = stat.borsh_count / total_days if total_days else 0

    rank_result = await session.execute(
        select(func.count(ChatUserStat.id) + 1).where(
            ChatUserStat.chat_id == chat.id,
            ChatUserStat.borsh_count > stat.borsh_count,
        )
    )
    rank = int(rank_result.scalar() or 1)

    best_day_result = await session.execute(
        select(func.date(BorshEvent.created_at), func.count(BorshEvent.id).label("cnt"))
        .where(BorshEvent.chat_id == chat.id, BorshEvent.user_id == user.id)
        .group_by(func.date(BorshEvent.created_at))
        .order_by(func.count(BorshEvent.id).desc())
        .limit(1)
    )
    best_day = best_day_result.first()

    active_days_result = await session.execute(
        select(func.count(func.distinct(func.date(BorshEvent.created_at)))).where(
            BorshEvent.chat_id == chat.id,
            BorshEvent.user_id == user.id,
        )
    )
    active_days = int(active_days_result.scalar() or 0)

    return {
        "name": display_name(user),
        "telegram_user_id": user.telegram_user_id,
        "rank": rank,
        "total": stat.borsh_count,
        "today": today,
        "week": week,
        "month": month,
        "avg_per_day": avg_per_day,
        "active_days": active_days,
        "first_borsh_at": stat.first_borsh_at,
        "last_borsh_at": stat.last_borsh_at,
        "best_day": best_day[0] if best_day else None,
        "best_day_count": int(best_day[1]) if best_day else 0,
    }
