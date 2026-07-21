from datetime import datetime, timedelta, timezone

from aiogram.types import Chat as TgChat, User as TgUser
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import BorshEvent, BorshProof, Chat, ChatUserStat, PhotoMessage, User


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


async def adjust_borsh_for_user(session: AsyncSession, tg_chat: TgChat, user: User, amount: int) -> int:
    chat = await upsert_chat(session, tg_chat)
    stat = await get_or_create_stat(session, chat, user)

    if stat.borsh_count + amount < 0:
        raise ValueError("нельзя убрать больше борщей, чем есть у пользователя в этом чате")

    now = datetime.now(timezone.utc)
    stat.borsh_count += amount

    if amount > 0:
        stat.last_borsh_at = now
        if stat.first_borsh_at is None:
            stat.first_borsh_at = now
        session.add_all(BorshEvent(chat_id=chat.id, user_id=user.id) for _ in range(amount))
    else:
        event_ids = (
            select(BorshEvent.id)
            .where(BorshEvent.chat_id == chat.id, BorshEvent.user_id == user.id)
            .order_by(BorshEvent.created_at.desc(), BorshEvent.id.desc())
            .limit(abs(amount))
            .subquery()
        )
        await session.execute(
            delete(BorshEvent)
            .where(BorshEvent.id.in_(select(event_ids.c.id)))
            .execution_options(synchronize_session=False)
        )

        if stat.borsh_count == 0:
            stat.first_borsh_at = None
            stat.last_borsh_at = None
        else:
            bounds = await session.execute(
                select(func.min(BorshEvent.created_at), func.max(BorshEvent.created_at)).where(
                    BorshEvent.chat_id == chat.id,
                    BorshEvent.user_id == user.id,
                )
            )
            first_borsh_at, last_borsh_at = bounds.one()
            stat.first_borsh_at = first_borsh_at
            stat.last_borsh_at = last_borsh_at

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


async def find_user(session: AsyncSession, query: str) -> User | None:
    q = query.strip().lstrip("@")
    if not q:
        return None

    conditions = [
        func.lower(User.telegram_username) == q.lower(),
        func.lower(User.custom_username) == q.lower(),
    ]
    if q.isdigit():
        conditions.append(User.telegram_user_id == int(q))

    result = await session.execute(select(User).where(or_(*conditions)).limit(1))
    return result.scalar_one_or_none()


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

async def remember_photo_message(session: AsyncSession, tg_chat: TgChat, tg_user: TgUser, message_id: int, file_id: str, file_unique_id: str | None, message_date: datetime) -> None:
    user = await upsert_user(session, tg_user)
    chat = await upsert_chat(session, tg_chat)
    result = await session.execute(
        select(PhotoMessage).where(PhotoMessage.chat_id == chat.id, PhotoMessage.telegram_message_id == message_id)
    )
    existing = result.scalar_one_or_none()
    if existing is None:
        session.add(
            PhotoMessage(
                chat_id=chat.id,
                user_id=user.id,
                telegram_message_id=message_id,
                telegram_file_id=file_id,
                telegram_file_unique_id=file_unique_id,
                message_date=message_date,
            )
        )
    await session.commit()


async def get_photo_by_message_id(
    session: AsyncSession,
    tg_chat: TgChat,
    tg_user: TgUser,
    message_id: int,
) -> PhotoMessage | None:
    user = await upsert_user(session, tg_user)
    chat = await upsert_chat(session, tg_chat)
    result = await session.execute(
        select(PhotoMessage).where(
            PhotoMessage.chat_id == chat.id,
            PhotoMessage.user_id == user.id,
            PhotoMessage.telegram_message_id == message_id,
        )
    )
    return result.scalar_one_or_none()


async def get_recent_photo_candidates(
    session: AsyncSession,
    tg_chat: TgChat,
    tg_user: TgUser,
    before_message_id: int,
    limit: int = 1,
    since: datetime | None = None,
) -> list[PhotoMessage]:
    user = await upsert_user(session, tg_user)
    chat = await upsert_chat(session, tg_chat)
    conditions = [
        PhotoMessage.chat_id == chat.id,
        PhotoMessage.user_id == user.id,
        PhotoMessage.telegram_message_id < before_message_id,
    ]
    if since is not None:
        conditions.append(PhotoMessage.message_date >= since)
    result = await session.execute(
        select(PhotoMessage)
        .where(*conditions)
        .order_by(PhotoMessage.telegram_message_id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def photo_already_confirmed(session: AsyncSession, photo: PhotoMessage) -> bool:
    # Название функции осталось старым для совместимости с handlers.py,
    # но по смыслу проверяем любую уже выполненную экспертизу фото.
    # И подтвержденное, и отклоненное фото нельзя отправлять на повторный подсчет,
    # иначе PostgreSQL ловит UniqueViolationError по uq_borsh_proof_chat_user_photo.
    result = await session.execute(
        select(BorshProof.id).where(
            BorshProof.chat_id == photo.chat_id,
            BorshProof.user_id == photo.user_id,
            BorshProof.photo_message_id == photo.id,
        )
    )
    return result.scalar_one_or_none() is not None


async def save_borsh_proof_and_increment(
    session: AsyncSession,
    tg_chat: TgChat,
    tg_user: TgUser,
    photo: PhotoMessage,
    command_message_id: int,
    confirmed: bool,
    agent_response: str,
) -> int | None:
    user = await upsert_user(session, tg_user)
    chat = await upsert_chat(session, tg_chat)
    stat = await get_or_create_stat(session, chat, user)

    existing_proof = await session.scalar(
        select(BorshProof).where(
            BorshProof.chat_id == chat.id,
            BorshProof.user_id == user.id,
            BorshProof.photo_message_id == photo.id,
        )
    )
    if existing_proof is not None:
        await session.rollback()
        return None

    proof = BorshProof(
        chat_id=chat.id,
        user_id=user.id,
        photo_message_id=photo.id,
        borsh_command_message_id=command_message_id,
        confirmed=confirmed,
        agent_response=agent_response,
    )
    session.add(proof)

    if not confirmed:
        await session.commit()
        return None

    now = datetime.now(timezone.utc)
    stat.borsh_count += 1
    stat.last_borsh_at = now
    if stat.first_borsh_at is None:
        stat.first_borsh_at = now
    session.add(BorshEvent(chat_id=chat.id, user_id=user.id))
    await session.commit()
    return stat.borsh_count
