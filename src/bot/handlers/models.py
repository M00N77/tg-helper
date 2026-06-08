"""Модели базы данных для Telegram Assistant (Кейс профи - командная работа)."""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ========== ПОЛЬЗОВАТЕЛИ И НАСТРОЙКИ ==========

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    settings: Mapped["UserSettings"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan", lazy="selectin",
    )
    session: Mapped["TelegramSession | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan", lazy="selectin",
    )
    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin",
    )


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    auto_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_provider: Mapped[str] = mapped_column(String(16), default="openai")
    use_heavy_model: Mapped[bool] = mapped_column(Boolean, default=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    digest_time: Mapped[str] = mapped_column(String(5), default="09:00")
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    transcription_mode: Mapped[str] = mapped_column(String(16), default="local")
    auto_reply_cooldown_min: Mapped[int] = mapped_column(Integer, default=30)
    auto_reply_mode: Mapped[str] = mapped_column(String(8), default="static")
    auto_reply_text: Mapped[str] = mapped_column(
        Text,
        default="Сейчас не у телефона, отвечу как только смогу.",
    )
    ignore_archived: Mapped[bool] = mapped_column(Boolean, default=True)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_lead_hours: Mapped[int] = mapped_column(Integer, default=2)
    reminder_overdue_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    news_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    news_window_hours: Mapped[int] = mapped_column(Integer, default=24)
    news_digest_time: Mapped[str] = mapped_column(String(5), default="08:00")

    user: Mapped[User] = relationship(back_populates="settings")


class TelegramSession(Base):
    __tablename__ = "telegram_sessions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    api_id: Mapped[int] = mapped_column(BigInteger)
    api_hash_enc: Mapped[str] = mapped_column(Text)
    session_string_enc: Mapped[str] = mapped_column(Text)
    phone: Mapped[str] = mapped_column(String(32))
    account_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="session")


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_api_key_user_provider"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(16))
    key_enc: Mapped[str] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="api_keys")


# ========== КОМАНДНАЯ РАБОТА (НОВОЕ ДЛЯ КЕЙСА) ==========

class Team(Base):
    """Команда - группа пользователей с общим чатом и канбан-доской"""
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Канбан-доска
    kanban_provider: Mapped[str] = mapped_column(String(32), nullable=True)  # yougile, trello
    kanban_token_enc: Mapped[str] = mapped_column(Text, nullable=True)
    kanban_board_id: Mapped[str] = mapped_column(String(128), nullable=True)
    
    # Связи
    members: Mapped[list["TeamMember"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )
    meetings: Mapped[list["Meeting"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )
    kanban_tasks: Mapped[list["KanbanTask"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )


class TeamMember(Base):
    """Участник команды"""
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "telegram_id", name="uq_team_member"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(32), default="member")  # admin, member
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Связи
    team: Mapped[Team] = relationship(back_populates="members")


# ========== КАНБАН-ДОСКА (НОВОЕ ДЛЯ КЕЙСА) ==========

class KanbanTask(Base):
    """Задача из канбан-доски (синхронизация с внешним API)"""
    __tablename__ = "kanban_tasks"
    __table_args__ = (UniqueConstraint("team_id", "external_id", name="uq_kanban_task"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(128))  # ID в YouGile/Trello
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="todo")  # todo, in_progress, done, cancelled
    column_id: Mapped[str] = mapped_column(String(128), nullable=True)
    assignee_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    assignee_name: Mapped[str] = mapped_column(String(128), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # 0-низкий, 1-средний, 2-высокий
    deadline_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Связи
    team: Mapped[Team] = relationship(back_populates="kanban_tasks")


# ========== ОСНОВНЫЕ МОДЕЛИ (БЫЛИ РАНЬШЕ) ==========

class Contact(Base):
    """Сохранённый профиль чата/контакта (peer)."""
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("user_id", "peer_id", name="uq_contact_user_peer"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    peer_kind: Mapped[str] = mapped_column(String(16))  # user | chat | channel
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    is_news_source: Mapped[bool] = mapped_column(Boolean, default=False)
    display_name: Mapped[str] = mapped_column(String(256))
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    style_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # JSON-объект
    style_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Message(Base):
    """Кэш сообщений из чатов."""
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("user_id", "peer_id", "message_id", name="uq_msg_user_peer_id"),
        Index("ix_messages_user_peer_date", "user_id", "peer_id", "date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    sender_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_outgoing: Mapped[bool] = mapped_column(Boolean, default=False)
    date: Mapped[datetime] = mapped_column(DateTime, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="text")
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed_in_vector: Mapped[bool] = mapped_column(Boolean, default=False)


class Commitment(Base):
    """Извлечённые обещания."""
    __tablename__ = "commitments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    peer_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    direction: Mapped[str] = mapped_column(String(8))  # mine | theirs
    text: Mapped[str] = mapped_column(Text)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open, done, cancelled, reminded
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AutoReplyLog(Base):
    """Лог авто-ответов для прозрачности."""
    __tablename__ = "auto_reply_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    peer_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    incoming_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class IndexJob(Base):
    """Состояние индексации чата (последний обработанный message_id)."""
    __tablename__ = "index_jobs"
    __table_args__ = (UniqueConstraint("user_id", "peer_id", name="uq_index_user_peer"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    last_indexed_message_id: Mapped[int] = mapped_column(BigInteger, default=0)
    last_indexed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TranscriptionCache(Base):
    """Кэш транскрипций по telegram media file_id."""
    __tablename__ = "transcription_cache"

    file_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PendingAction(Base):
    """Промежуточные действия, ожидающие подтверждения."""
    __tablename__ = "pending_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # send_message, catchup_reply, ...
    payload: Mapped[dict] = mapped_column(JSON)  # JSON-объект
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NewsTopic(Base):
    """Темы-фавориты для авто-новостей."""
    __tablename__ = "news_topics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(256))
    hours: Mapped[int] = mapped_column(Integer, default=24)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)