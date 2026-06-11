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
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class EncryptedString(TypeDecorator):
    """Прозрачное шифрование строкового столбца (Fernet) на уровне ORM.

    Значение шифруется при записи в БД и расшифровывается при чтении — код,
    обращающийся к атрибуту модели, работает с открытым текстом и ничего не знает
    о шифровании. Для обратной совместимости со старыми записями, сохранёнными в
    открытом виде, при чтении используется try_decrypt: если значение не валидный
    Fernet-токен, оно возвращается как есть (легаси-плейнтекст).
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        # Импорт здесь, чтобы избежать цикла models → crypto → config на этапе сборки.
        from src.crypto import encrypt
        return encrypt(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        from src.crypto import try_decrypt
        return try_decrypt(value)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
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
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")  # IANA tz
    digest_time: Mapped[str] = mapped_column(String(5), default="09:00")  # HH:MM в timezone юзера
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    transcription_mode: Mapped[str] = mapped_column(String(16), default="local")  # local | api | hybrid
    auto_reply_cooldown_min: Mapped[int] = mapped_column(Integer, default=30)
    auto_reply_mode: Mapped[str] = mapped_column(String(8), default="static")  # static | smart
    auto_reply_text: Mapped[str] = mapped_column(
        Text,
        default="Сейчас не у телефона, отвечу как только смогу.",
    )
    ignore_archived: Mapped[bool] = mapped_column(Boolean, default=True)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_lead_hours: Mapped[int] = mapped_column(Integer, default=2)
    reminder_overdue_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reminder_work_hours_start: Mapped[int] = mapped_column(Integer, default=9)
    reminder_work_hours_end: Mapped[int] = mapped_column(Integer, default=21)
    reminder_work_days: Mapped[str] = mapped_column(String(13), default="1,2,3,4,5")
    news_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    news_window_hours: Mapped[int] = mapped_column(Integer, default=24)
    news_digest_time: Mapped[str] = mapped_column(String(5), default="08:00")  # HH:MM в UTC

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


class Contact(Base):
    """Сохранённый профиль чата/контакта (peer)."""

    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("user_id", "peer_id", name="uq_contact_user_peer"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    peer_kind: Mapped[str] = mapped_column(String(16))  # user | chat | channel
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)  # лежит в архиве в Telegram
    is_news_source: Mapped[bool] = mapped_column(Boolean, default=False)  # помеченные для /news каналы
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
    kind: Mapped[str] = mapped_column(String(16), default="text")  # text | voice | audio | document | photo | other
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # для документов
    indexed_in_vector: Mapped[bool] = mapped_column(Boolean, default=False)
    reply_to_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


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
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | done | cancelled | trashed
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
    """Промежуточные действия, ожидающие подтверждения (отправка сообщения и т.п.)."""

    __tablename__ = "pending_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # send_message | catchup_reply | ...
    payload: Mapped[dict] = mapped_column(JSON)  # JSON-объект
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NewsTopic(Base):
    """Темы-фавориты для авто-новостей. Каждая утром собирается отдельным дайджестом."""

    __tablename__ = "news_topics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(256))
    hours: Mapped[int] = mapped_column(Integer, default=24)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, default=0)
    kanban_token: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    kanban_board_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    kanban_provider: Mapped[str | None] = mapped_column(Text, nullable=True, default="yougile")
    active_board_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    active_board_name: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    mtslink_token: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    standup_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    standup_time: Mapped[str] = mapped_column(String(5), default="09:30")
    standup_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    activities_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    pulse_time: Mapped[str] = mapped_column(String(5), default="17:00")  # HH:MM в TZ команды (Europe/Moscow)
    pulse_auto_close_minutes: Mapped[int] = mapped_column(Integer, default=60)  # через сколько минут авто-итоги
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    meetings: Mapped[list["Meeting"]] = relationship(back_populates="team")
    members: Mapped[list["TeamMember"]] = relationship(
        back_populates="team", cascade="all, delete-orphan",
    )
    standups: Mapped[list["Standup"]] = relationship(
        back_populates="team", cascade="all, delete-orphan",
    )
    blockers: Mapped[list["Blocker"]] = relationship(
        back_populates="team", cascade="all, delete-orphan",
    )
    time_logs: Mapped[list["TimeLog"]] = relationship(
        back_populates="team", cascade="all, delete-orphan",
    )


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "telegram_id", name="uq_team_member"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    role: Mapped[str] = mapped_column(String(32), default="member")
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    yougile_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    team: Mapped[Team] = relationship(back_populates="members")


class PendingInvite(Base):
    __tablename__ = "pending_invites"
    __table_args__ = (
        UniqueConstraint("team_id", "username", name="uq_pending_invite"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    username: Mapped[str] = mapped_column(String(64))
    invited_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Meeting(Base):
    """Запись встречи (платформонезависимая)."""

    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    meeting_url: Mapped[str] = mapped_column(Text)
    mtslink_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mtslink_record_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mtslink_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    platform: Mapped[str] = mapped_column(String(32), default="unknown", server_default="unknown")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_llm_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="recording")

    team: Mapped[Team] = relationship(back_populates="meetings")
    extracted_tasks: Mapped[list["MeetingTask"]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan"
    )


class MeetingTask(Base):
    """Задачи, извлечённые из встречи."""

    __tablename__ = "meeting_tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    assignee_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    assignee_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_in_kanban: Mapped[bool] = mapped_column(Boolean, default=False)
    kanban_card_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    meeting: Mapped[Meeting] = relationship(back_populates="extracted_tasks")


class Standup(Base):
    __tablename__ = "standups"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", "date", name="uq_standup_team_user_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    date: Mapped[datetime] = mapped_column(DateTime, index=True)
    done_today: Mapped[str] = mapped_column(Text, default="")
    plan_today: Mapped[str] = mapped_column(Text, default="")
    blockers: Mapped[str] = mapped_column(Text, default="")
    mood: Mapped[str] = mapped_column(String(16), default="neutral")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    team: Mapped["Team"] = relationship(back_populates="standups")


class Blocker(Base):
    __tablename__ = "blockers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    reported_by: Mapped[int] = mapped_column(BigInteger)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(16), default="open")
    standup_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    team: Mapped["Team"] = relationship(back_populates="blockers")


class TimeLog(Base):
    __tablename__ = "time_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    minutes: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text, default="")
    date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    team: Mapped["Team"] = relationship(back_populates="time_logs")


class SociometryCache(Base):
    __tablename__ = "sociometry_cache"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ActivitySession(Base):
    """Конкретный запуск групповой активности (пульс-опрос, метафора, квиз) в чате команды."""

    __tablename__ = "activity_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    activity_code: Mapped[str] = mapped_column(String(64), index=True)  # код механики из registry
    kind: Mapped[str] = mapped_column(String(32), default="pulse")  # pulse | metaphor | quiz | icebreaker
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    question: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | closed
    summary_posted: Mapped[bool] = mapped_column(Boolean, default=False)  # итоги уже опубликованы в чат
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    responses: Mapped[list["ActivityResponse"]] = relationship(
        back_populates="session", cascade="all, delete-orphan",
    )


class ActivityResponse(Base):
    """Ответ участника на активность.

    Анонимность: для is_anonymous-сессий user_id НЕ пишется (NULL), вместо него
    хранится respondent_hash = HMAC(secret, telegram_id + session_id). Это даёт
    дедупликацию «один человек — один голос» без хранения связи с личностью.
    """

    __tablename__ = "activity_responses"
    __table_args__ = (
        UniqueConstraint("session_id", "respondent_hash", name="uq_activity_resp_session_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("activity_sessions.id", ondelete="CASCADE"), index=True
    )
    respondent_hash: Mapped[str] = mapped_column(String(64), index=True)  # HMAC-hex, всегда заполнен
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # NULL для анонимных
    answer_value: Mapped[int | None] = mapped_column(Integer, nullable=True)  # числовой ответ (шкала 1..5)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # свободный текст
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["ActivitySession"] = relationship(back_populates="responses")


class EmailMessage(Base):
    __tablename__ = "email_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text)
    sender: Mapped[str] = mapped_column(String(256))
    received_at: Mapped[datetime] = mapped_column(DateTime)
    has_deadline: Mapped[bool] = mapped_column(Boolean, default=False)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    commitment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
