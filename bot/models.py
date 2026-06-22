from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    custom_username: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    chat_stats: Mapped[list["ChatUserStat"]] = relationship(back_populates="user")
    events: Mapped[list["BorshEvent"]] = relationship(back_populates="user")


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user_stats: Mapped[list["ChatUserStat"]] = relationship(back_populates="chat")
    events: Mapped[list["BorshEvent"]] = relationship(back_populates="chat")


class ChatUserStat(Base):
    __tablename__ = "chat_user_stats"
    __table_args__ = (UniqueConstraint("chat_id", "user_id", name="uq_chat_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    borsh_count: Mapped[int] = mapped_column(Integer, default=0)
    first_borsh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_borsh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    chat: Mapped[Chat] = relationship(back_populates="user_stats")
    user: Mapped[User] = relationship(back_populates="chat_stats")


class BorshEvent(Base):
    __tablename__ = "borsh_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    chat: Mapped[Chat] = relationship(back_populates="events")
    user: Mapped[User] = relationship(back_populates="events")
