import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime


logger = logging.getLogger(__name__)

from sqlalchemy import desc, select, text as sql_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.crypto import decrypt, encrypt
from src.db.models import (
    ActivityResponse,
    ActivitySession,
    ApiKey,
    AutoReplyLog,
    Commitment,
    Contact,
    Meeting,
    Message,
    MessageRisk,
    MessageSentiment,
    NewsTopic,
    PendingAction,
    PendingInvite,
    PendingTask,
    PendingTeamTask,
    RolePermission,
    TaskStatus,
    Blocker,
    EmailMessage,
    SociometryCache,
    Standup,
    TeamDictionary,
    TimeLog,
    Team,
    TeamMember,
    YouGileUserAlias,
    TelegramSession,
    TranscriptionCache,
    User,
    UserSettings,
)


# Фоновые таски (digest, news, reminders, auto_sync) одновременно тыкаются в
# get_or_create_user на пустой БД → UNIQUE race. Сериализуем процесс-локом.
_user_lock = asyncio.Lock()


async def get_or_create_user(session: AsyncSession, telegram_id: int) -> User:
    async with _user_lock:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(telegram_id=telegram_id, settings=UserSettings())
            session.add(user)
            await session.flush()
        elif user.settings is None:
            user.settings = UserSettings(user_id=user.id)
            session.add(user.settings)
            await session.flush()
        return user


async def save_telegram_session(
    session: AsyncSession,
    user: User,
    *,
    api_id: int,
    api_hash: str,
    session_string: str,
    phone: str,
    account_label: str | None,
) -> None:
    payload = TelegramSession(
        user_id=user.id,
        api_id=api_id,
        api_hash_enc=encrypt(api_hash),
        session_string_enc=encrypt(session_string),
        phone=phone,
        account_label=account_label,
    )
    existing = await session.get(TelegramSession, user.id)
    if existing is not None:
        await session.delete(existing)
        await session.flush()
    session.add(payload)


async def load_telegram_session(session: AsyncSession, user: User) -> tuple[int, str, str] | None:
    row = await session.get(TelegramSession, user.id)
    if row is None:
        return None
    return row.api_id, decrypt(row.api_hash_enc), decrypt(row.session_string_enc)


async def delete_telegram_session(session: AsyncSession, user: User) -> None:
    row = await session.get(TelegramSession, user.id)
    if row is not None:
        await session.delete(row)


async def upsert_api_key(session: AsyncSession, user: User, provider: str, key: str) -> None:
    result = await session.execute(
        select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.provider == provider)
    )
    existing = result.scalar_one_or_none()
    enc = encrypt(key)
    if existing is None:
        session.add(ApiKey(user_id=user.id, provider=provider, key_enc=enc))
    else:
        existing.key_enc = enc


async def get_api_key(session: AsyncSession, user: User, provider: str) -> str | None:
    result = await session.execute(
        select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.provider == provider)
    )
    row = result.scalar_one_or_none()
    return decrypt(row.key_enc).strip() if row is not None else None


async def upsert_contact(
    session: AsyncSession,
    user: User,
    *,
    peer_id: int,
    peer_kind: str,
    display_name: str,
    username: str | None = None,
    phone: str | None = None,
    is_bot: bool = False,
    is_archived: bool | None = None,
) -> Contact:
    result = await session.execute(
        select(Contact).where(Contact.user_id == user.id, Contact.peer_id == peer_id)
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        contact = Contact(
            user_id=user.id,
            peer_id=peer_id,
            peer_kind=peer_kind,
            is_bot=is_bot,
            is_archived=bool(is_archived) if is_archived is not None else False,
            display_name=display_name,
            username=username,
            phone=phone,
        )
        session.add(contact)
        await session.flush()
    else:
        contact.peer_kind = peer_kind
        contact.is_bot = is_bot
        if is_archived is not None:
            contact.is_archived = is_archived
        contact.display_name = display_name
        contact.username = username
        contact.phone = phone
    return contact


async def list_contacts(
    session: AsyncSession,
    user: User,
    *,
    kinds: tuple[str, ...] | None = None,
    include_bots: bool = False,
    only_news_sources: bool = False,
    include_archived: bool | None = None,
) -> list[Contact]:
    # include_archived=None → берём решение из настроек пользователя
    if include_archived is None:
        include_archived = not user.settings.ignore_archived if user.settings else False

    query = select(Contact).where(Contact.user_id == user.id)
    if kinds:
        query = query.where(Contact.peer_kind.in_(kinds))
    if not include_bots:
        query = query.where(Contact.is_bot.is_(False))
    if only_news_sources:
        query = query.where(Contact.is_news_source.is_(True))
    if not include_archived:
        query = query.where(Contact.is_archived.is_(False))
    result = await session.execute(query)
    return list(result.scalars().all())


async def set_news_source(session: AsyncSession, user: User, peer_id: int, value: bool) -> bool:
    result = await session.execute(
        select(Contact).where(Contact.user_id == user.id, Contact.peer_id == peer_id)
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        return False
    contact.is_news_source = value
    return True


async def get_contact(session: AsyncSession, user: User, peer_id: int) -> Contact | None:
    result = await session.execute(
        select(Contact).where(Contact.user_id == user.id, Contact.peer_id == peer_id)
    )
    return result.scalar_one_or_none()


async def upsert_message(
    session: AsyncSession,
    *,
    user_id: int,
    peer_id: int,
    message_id: int,
    sender_id: int | None,
    sender_name: str | None,
    is_outgoing: bool,
    date: datetime,
    kind: str,
    text: str | None,
    transcript: str | None = None,
    media_path: str | None = None,
    extracted_text: str | None = None,
    reply_to_msg_id: int | None = None,
) -> None:
    stmt = pg_insert(Message).values(
        user_id=user_id,
        peer_id=peer_id,
        message_id=message_id,
        sender_id=sender_id,
        sender_name=sender_name,
        is_outgoing=is_outgoing,
        date=date,
        kind=kind,
        text=text,
        transcript=transcript,
        media_path=media_path,
        extracted_text=extracted_text,
        reply_to_msg_id=reply_to_msg_id,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "peer_id", "message_id"],
        set_={
            "text": stmt.excluded.text,
            "transcript": stmt.excluded.transcript,
            "extracted_text": stmt.excluded.extracted_text,
            "media_path": stmt.excluded.media_path,
            "kind": stmt.excluded.kind,
            "sender_name": stmt.excluded.sender_name,
        },
    )
    await session.execute(stmt)


async def fetch_chat_messages(
    session: AsyncSession,
    user: User,
    peer_id: int,
    limit: int = 50,
) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(Message.user_id == user.id, Message.peer_id == peer_id)
        .order_by(Message.date.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


@dataclass
class FtsHit:
    user_id: int
    peer_id: int
    message_id: int
    sender_name: str | None
    snippet: str
    rank: float


def _fts_query_for(query: str) -> str:
    # каждое слово → prefix-match, склейка через OR. Это толерантнее MATCH'а целой фразы.
    parts = []
    for raw in query.split():
        clean = "".join(ch for ch in raw if ch.isalnum() or ch in "_-")
        if len(clean) >= 2:
            parts.append(clean.lower() + "*")
    if not parts:
        return ""
    return " OR ".join(parts)


async def fts_search(
    session: AsyncSession,
    user_id: int,
    query: str,
    *,
    limit: int = 50,
) -> list[FtsHit]:
    fts_q = _fts_query_for(query)
    if not fts_q:
        return []
    sql = """
        SELECT m.user_id, m.peer_id, m.message_id, m.sender_name,
               snippet(messages_fts, -1, '', '', '…', 16) AS snippet,
               bm25(messages_fts) AS rank
        FROM messages_fts
        JOIN messages m ON m.id = messages_fts.rowid
        WHERE messages_fts MATCH :q AND m.user_id = :uid
        ORDER BY rank
        LIMIT :lim
    """
    result = await session.execute(
        sql_text(sql),
        {"q": fts_q, "uid": user_id, "lim": limit},
    )
    rows = result.mappings().all()
    return [
        FtsHit(
            user_id=int(r["user_id"]),
            peer_id=int(r["peer_id"]),
            message_id=int(r["message_id"]),
            sender_name=r["sender_name"],
            snippet=r["snippet"] or "",
            rank=float(r["rank"]) if r["rank"] is not None else 0.0,
        )
        for r in rows
    ]


async def fetch_my_messages_in_chat(
    session: AsyncSession,
    user: User,
    peer_id: int,
    limit: int = 100,
) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(
            Message.user_id == user.id,
            Message.peer_id == peer_id,
            Message.is_outgoing.is_(True),
        )
        .order_by(Message.date.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def get_cached_transcript(session: AsyncSession, file_id: str) -> str | None:
    row = await session.get(TranscriptionCache, file_id)
    return row.text if row else None


async def cache_transcript(
    session: AsyncSession,
    file_id: str,
    text: str,
    duration_seconds: float | None = None,
) -> None:
    existing = await session.get(TranscriptionCache, file_id)
    if existing is None:
        session.add(TranscriptionCache(file_id=file_id, text=text, duration_seconds=duration_seconds))
    else:
        existing.text = text
        existing.duration_seconds = duration_seconds


async def add_commitment(
    session: AsyncSession,
    *,
    user_id: int,
    peer_id: int,
    peer_name: str | None,
    message_id: int | None,
    direction: str,
    text: str,
    deadline_at: datetime | None,
) -> Commitment:
    c = Commitment(
        user_id=user_id,
        peer_id=peer_id,
        peer_name=peer_name,
        message_id=message_id,
        direction=direction,
        text=text,
        deadline_at=deadline_at,
    )
    session.add(c)
    await session.flush()
    return c


async def list_open_commitments(
    session: AsyncSession,
    user: User,
    *,
    direction: str | None = None,
) -> list[Commitment]:
    query = select(Commitment).where(
        Commitment.user_id == user.id,
        Commitment.status == "open",
    )
    if direction:
        query = query.where(Commitment.direction == direction)
    query = query.order_by(Commitment.deadline_at.is_(None), Commitment.deadline_at.asc())
    result = await session.execute(query)
    return list(result.scalars().all())


async def update_commitment_status(session: AsyncSession, commitment_id: int, status: str) -> None:
    c = await session.get(Commitment, commitment_id)
    if c is not None:
        c.status = status


async def trash_commitment(session: AsyncSession, commitment_id: int) -> bool:
    c = await session.get(Commitment, commitment_id)
    if c is None or c.status == "trashed":
        return False
    c.status = "trashed"
    c.deleted_at = datetime.utcnow()
    return True


async def restore_commitment(session: AsyncSession, commitment_id: int) -> bool:
    c = await session.get(Commitment, commitment_id)
    if c is None or c.status != "trashed":
        return False
    c.status = "open"
    c.deleted_at = None
    return True


async def list_trashed_commitments(
    session: AsyncSession,
    user: User,
) -> list[Commitment]:
    query = select(Commitment).where(
        Commitment.user_id == user.id,
        Commitment.status == "trashed",
    )
    query = query.order_by(Commitment.deleted_at.desc())
    result = await session.execute(query)
    return list(result.scalars().all())


async def hard_delete_expired_trash(session: AsyncSession) -> int:
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(hours=24)
    result = await session.execute(
        select(Commitment).where(
            Commitment.status == "trashed",
            Commitment.deleted_at.isnot(None),
            Commitment.deleted_at < cutoff,
        )
    )
    items = list(result.scalars().all())
    for c in items:
        await session.delete(c)
    return len(items)


async def add_auto_reply_log(
    session: AsyncSession,
    *,
    user_id: int,
    peer_id: int,
    peer_name: str | None,
    incoming_text: str | None,
    reply_text: str,
) -> None:
    session.add(
        AutoReplyLog(
            user_id=user_id,
            peer_id=peer_id,
            peer_name=peer_name,
            incoming_text=incoming_text,
            reply_text=reply_text,
        )
    )


async def list_recent_auto_replies(
    session: AsyncSession,
    user: User,
    *,
    limit: int = 10,
) -> list[AutoReplyLog]:
    result = await session.execute(
        select(AutoReplyLog)
        .where(AutoReplyLog.user_id == user.id)
        .order_by(AutoReplyLog.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def create_pending_action(
    session: AsyncSession,
    *,
    user_id: int,
    kind: str,
    payload: dict,
) -> PendingAction:
    pa = PendingAction(user_id=user_id, kind=kind, payload=payload)
    session.add(pa)
    await session.flush()
    return pa


async def get_pending_action(session: AsyncSession, action_id: int) -> PendingAction | None:
    return await session.get(PendingAction, action_id)


async def update_pending_action(session: AsyncSession, action_id: int, payload: dict) -> None:
    pa = await session.get(PendingAction, action_id)
    if pa is not None:
        pa.payload = payload
    await session.flush()


# ── Team Dictionary ──────────────────────────────────────────────────────


async def list_team_dictionary(
    session: AsyncSession, team_id: int,
) -> list[TeamDictionary]:
    result = await session.execute(
        select(TeamDictionary).where(TeamDictionary.team_id == team_id).order_by(TeamDictionary.term)
    )
    return list(result.scalars().all())


async def add_team_dictionary_term(
    session: AsyncSession,
    *,
    team_id: int,
    term: str,
    definition: str,
    scope: str | None = None,
) -> bool:
    """Добавляет или обновляет термин. Возвращает True если обновлён, False если создан."""
    from src.db.models import TeamDictionary
    from sqlalchemy import select

    existing = await session.execute(
        select(TeamDictionary).where(
            TeamDictionary.team_id == team_id,
            TeamDictionary.term == term,
        )
    )
    row = existing.scalar_one_or_none()

    if row is not None:
        row.definition = definition.strip()
        row.scope = scope.strip() if scope else None
        row.updated_at = datetime.utcnow()
        was_updated = True
    else:
        session.add(TeamDictionary(
            team_id=team_id,
            term=term.strip(),
            definition=definition.strip(),
            scope=scope.strip() if scope else None,
        ))
        was_updated = False

    await session.flush()
    return was_updated


async def update_team_dictionary_term(
    session: AsyncSession,
    term_id: int,
    *,
    term: str | None = None,
    definition: str | None = None,
    scope: str | None = None,
) -> TeamDictionary | None:
    entry = await session.get(TeamDictionary, term_id)
    if entry is None:
        return None
    if term is not None:
        entry.term = term.strip()
    if definition is not None:
        entry.definition = definition.strip()
    if scope is not None:
        entry.scope = scope.strip() if scope else None
    await session.flush()
    await session.refresh(entry)
    return entry


async def delete_team_dictionary_term(session: AsyncSession, term_id: int) -> bool:
    entry = await session.get(TeamDictionary, term_id)
    if entry is None:
        return False
    await session.delete(entry)
    return True


async def delete_pending_action(session: AsyncSession, action_id: int) -> None:
    pa = await session.get(PendingAction, action_id)
    if pa is not None:
        await session.delete(pa)


async def create_pending_team_task(
    session: AsyncSession,
    *,
    team_id: int,
    creator_telegram_id: int,
    assignee_telegram_id: int,
    title: str,
    description: str | None = None,
) -> PendingTeamTask:
    pt = PendingTeamTask(
        team_id=team_id,
        creator_telegram_id=creator_telegram_id,
        assignee_telegram_id=assignee_telegram_id,
        title=title,
        description=description,
    )
    session.add(pt)
    await session.flush()
    return pt


async def get_pending_team_task(
    session: AsyncSession,
    task_id: int,
) -> PendingTeamTask | None:
    return await session.get(PendingTeamTask, task_id)


async def confirm_pending_team_task(
    session: AsyncSession,
    task_id: int,
    assignee_telegram_id: int,
) -> PendingTeamTask | None:
    """Атомарно: pending → processing. Гарантирует идемпотентность при двойном клике.
    Если строка уже не 'pending', возвращает None."""
    result = await session.execute(
        sql_text("""
            UPDATE pending_team_tasks
            SET status = 'processing', updated_at = NOW()
            WHERE id = :tid AND status = 'pending' AND assignee_telegram_id = :aid
            RETURNING id
        """),
        {"tid": task_id, "aid": assignee_telegram_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    await session.expire_all()
    return await session.get(PendingTeamTask, row[0])


async def reject_pending_team_task(
    session: AsyncSession,
    task_id: int,
    assignee_telegram_id: int,
) -> PendingTeamTask | None:
    """Атомарно: pending → rejected. Идемпотентно."""
    result = await session.execute(
        sql_text("""
            UPDATE pending_team_tasks
            SET status = 'rejected', updated_at = NOW()
            WHERE id = :tid AND status = 'pending' AND assignee_telegram_id = :aid
            RETURNING id
        """),
        {"tid": task_id, "aid": assignee_telegram_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    await session.expire_all()
    return await session.get(PendingTeamTask, row[0])


async def mark_team_task_approved(
    session: AsyncSession,
    task_id: int,
    yougile_task_id: str,
) -> None:
    """processing → approved (YouGile создана)."""
    await session.execute(
        sql_text("""
            UPDATE pending_team_tasks
            SET status = 'approved', yougile_task_id = :yid, updated_at = NOW()
            WHERE id = :tid AND status = 'processing'
        """),
        {"tid": task_id, "yid": yougile_task_id},
    )


async def mark_team_task_failed(
    session: AsyncSession,
    task_id: int,
    error_message: str,
) -> None:
    """processing → pending (YouGile отказал, можно повторить)."""
    await session.execute(
        sql_text("""
            UPDATE pending_team_tasks
            SET status = 'pending', error_message = :err, updated_at = NOW()
            WHERE id = :tid AND status = 'processing'
        """),
        {"tid": task_id, "err": error_message},
    )


async def update_pending_task_status(
    session: AsyncSession,
    task_id: int,
    old_status: TaskStatus,
    new_status: TaskStatus,
) -> PendingTeamTask | None:
    """Атомарная смена статуса с guard на old_status.
    Возвращает обновлённую запись или None при race condition."""
    result = await session.execute(
        sql_text("""
            UPDATE pending_team_tasks
            SET status = :new_status, updated_at = NOW()
            WHERE id = :tid AND status = :old_status
            RETURNING id
        """),
        {"tid": task_id, "old_status": old_status.value, "new_status": new_status.value},
    )
    row = result.fetchone()
    if row is None:
        return None
    await session.expire_all()
    return await session.get(PendingTeamTask, row[0])


async def create_pending_task(
    session: AsyncSession,
    *,
    team_id: int,
    task_title: str,
    task_description: str | None = None,
) -> PendingTask:
    pt = PendingTask(
        team_id=team_id,
        task_title=task_title,
        task_description=task_description,
    )
    session.add(pt)
    await session.flush()
    return pt


async def get_pending_task(
    session: AsyncSession,
    task_id: int,
) -> PendingTask | None:
    return await session.get(PendingTask, task_id)


async def approve_pending_task(
    session: AsyncSession,
    task_id: int,
) -> PendingTask | None:
    result = await session.execute(
        sql_text("""
            UPDATE pending_tasks
            SET status = 'approved'
            WHERE id = :tid AND status = 'pending'
            RETURNING id
        """),
        {"tid": task_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    await session.expire_all()
    return await session.get(PendingTask, row[0])


async def list_pending_tasks(
    session: AsyncSession,
    team_id: int,
) -> list[PendingTask]:
    query = select(PendingTask).where(
        PendingTask.team_id == team_id,
        PendingTask.status == "pending",
    ).order_by(PendingTask.created_at.desc())
    result = await session.execute(query)
    return list(result.scalars().all())


async def list_news_topics(
    session: AsyncSession,
    user: User,
    *,
    only_enabled: bool = False,
) -> list[NewsTopic]:
    query = select(NewsTopic).where(NewsTopic.user_id == user.id).order_by(NewsTopic.created_at.asc())
    if only_enabled:
        query = query.where(NewsTopic.enabled.is_(True))
    result = await session.execute(query)
    return list(result.scalars().all())


async def add_news_topic(
    session: AsyncSession,
    user: User,
    topic: str,
    *,
    hours: int = 24,
) -> NewsTopic:
    nt = NewsTopic(user_id=user.id, topic=topic.strip(), hours=hours)
    session.add(nt)
    await session.flush()
    return nt


async def delete_news_topic(session: AsyncSession, user: User, topic_id: int) -> bool:
    nt = await session.get(NewsTopic, topic_id)
    if nt is None or nt.user_id != user.id:
        return False
    await session.delete(nt)
    return True


async def toggle_news_topic(session: AsyncSession, user: User, topic_id: int) -> bool | None:
    nt = await session.get(NewsTopic, topic_id)
    if nt is None or nt.user_id != user.id:
        return None
    nt.enabled = not nt.enabled
    return nt.enabled


async def create_team(
    session: AsyncSession,
    *,
    name: str,
    telegram_chat_id: int,
    owner_telegram_id: int,
) -> Team:
    team = Team(
        name=name,
        chat_id=telegram_chat_id,
        owner_telegram_id=owner_telegram_id,
    )
    session.add(team)
    await session.flush()
    await init_default_permissions(session, team.id)
    return team


async def get_team_by_chat(
    session: AsyncSession, chat_id: int
) -> Team | None:
    result = await session.execute(
        select(Team).where(Team.chat_id == chat_id)
    )
    return result.scalar_one_or_none()


async def update_team_chat_id(
    session: AsyncSession, old_chat_id: int, new_chat_id: int
) -> Team | None:
    """Обновляет chat_id команды при миграции группы → супергруппу.
    Также обновляет все activity_sessions со старым chat_id."""
    team = await get_team_by_chat(session, old_chat_id)
    if team is None:
        return None
    team.chat_id = new_chat_id
    await session.execute(
        sql_text("""
            UPDATE activity_sessions
            SET chat_id = :new_id
            WHERE chat_id = :old_id
        """),
        {"new_id": new_chat_id, "old_id": old_chat_id},
    )
    return team


async def get_team_by_owner(
    session: AsyncSession, owner_telegram_id: int
) -> Team | None:
    result = await session.execute(
        select(Team)
        .where(Team.owner_telegram_id == owner_telegram_id)
        .order_by(Team.id.desc())
        .limit(1)
    )
    return result.scalars().first()


async def get_team_by_id(session: AsyncSession, team_id: int) -> Team | None:
    return await session.get(Team, team_id)


async def save_name_alias(
    session: AsyncSession,
    team_id: int,
    alias: str,
    yougile_user_id: str,
    display_name: str,
) -> None:
    existing = await session.execute(
        select(YouGileUserAlias).where(
            YouGileUserAlias.team_id == team_id,
            YouGileUserAlias.alias == alias.lower(),
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        row.yougile_user_id = yougile_user_id
        row.display_name = display_name
    else:
        session.add(YouGileUserAlias(
            team_id=team_id,
            alias=alias.lower(),
            yougile_user_id=yougile_user_id,
            display_name=display_name,
        ))


async def resolve_alias(
    session: AsyncSession,
    team_id: int,
    name: str,
) -> str | None:
    """Ищет yougile_user_id по алиасу (частичное совпадение)."""
    result = await session.execute(
        select(YouGileUserAlias).where(
            YouGileUserAlias.team_id == team_id,
        )
    )
    aliases = result.scalars().all()
    name_lower = name.lower()
    # Точное совпадение
    for a in aliases:
        if a.alias == name_lower:
            return a.yougile_user_id
    # Частичное совпадение
    for a in aliases:
        if name_lower in a.alias or a.alias in name_lower:
            return a.yougile_user_id
    return None


async def add_team_member(
    session: AsyncSession,
    team_id: int,
    telegram_id: int,
    role: str = "member",
) -> TeamMember:
    member = TeamMember(team_id=team_id, telegram_id=telegram_id, role=role)
    session.add(member)
    await session.flush()
    return member


async def get_team_members(
    session: AsyncSession,
    team_id: int,
) -> list[TeamMember]:
    result = await session.execute(
        select(TeamMember).where(TeamMember.team_id == team_id)
    )
    return list(result.scalars().all())


# list_team_members — алиас под конвенцию группового роутера
async def list_team_members(
    session: AsyncSession,
    team_id: int,
) -> list[TeamMember]:
    return await get_team_members(session, team_id)


async def get_team_member(
    session: AsyncSession,
    team_id: int,
    telegram_id: int,
) -> TeamMember | None:
    result = await session.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.telegram_id == telegram_id,
        )
    )
    return result.scalar_one_or_none()


async def ensure_team_member(
    session: AsyncSession,
    team_id: int,
    telegram_id: int,
    display_name: str | None = None,
) -> TeamMember:
    """Возвращает участника команды, создавая запись при первом появлении в чате.
    display_name обновляется, если стало известно более точное имя."""
    member = await get_team_member(session, team_id, telegram_id)
    if member is None:
        # Директор команды получает роль admin автоматически.
        team = await session.get(Team, team_id)
        role = "admin" if team is not None and team.owner_telegram_id == telegram_id else "member"
        member = TeamMember(
            team_id=team_id,
            telegram_id=telegram_id,
            role=role,
            display_name=display_name,
        )
        session.add(member)
        await session.flush()
    elif display_name and member.display_name != display_name:
        member.display_name = display_name
        await session.flush()
    return member


async def find_team_member_by_name(
    session: AsyncSession,
    team_id: int,
    name: str,
) -> TeamMember | None:
    """Нечёткий поиск участника команды по display_name. Возвращает лучшее
    совпадение или None, если запрос пуст / совпадений нет."""
    name = (name or "").strip()
    if not name:
        return None
    members = await get_team_members(session, team_id)
    if not members:
        return None

    name_lower = name.lstrip("@").lower()

    # 1. Точное / подстрочное совпадение по имени.
    for m in members:
        if m.display_name and name_lower in m.display_name.lower():
            return m

    # 2. Нечёткий поиск (rapidfuzz), если доступен.
    try:
        from rapidfuzz import fuzz, process
    except Exception:
        return None

    choices = {m.id: (m.display_name or "") for m in members if m.display_name}
    if not choices:
        return None
    raw = process.extractOne(name, choices, scorer=fuzz.WRatio, score_cutoff=60)
    if not raw:
        return None
    member_id = raw[2]
    return next((m for m in members if m.id == member_id), None)


async def set_team_member_yougile_id(
    session: AsyncSession,
    team_id: int,
    telegram_id: int,
    yougile_user_id: str | None,
) -> TeamMember | None:
    result = await session.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.telegram_id == telegram_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        return None
    member.yougile_user_id = yougile_user_id
    return member


async def remove_team_member(
    session: AsyncSession,
    team_id: int,
    telegram_id: int,
) -> bool:
    result = await session.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.telegram_id == telegram_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        return False
    await session.delete(member)
    return True


async def get_user_teams(
    session: AsyncSession,
    telegram_id: int,
) -> list[Team]:
    result = await session.execute(
        select(Team).join(TeamMember).where(TeamMember.telegram_id == telegram_id)
    )
    return list(result.scalars().all())


async def update_team_kanban(
    session: AsyncSession,
    chat_id: int,
    token: str,
    board_id: str = "",
    provider: str = "yougile",
) -> Team:
    team = await get_team_by_chat(session, chat_id)
    if team is None:
        team = Team(chat_id=chat_id)
        session.add(team)
    team.kanban_token = token
    team.kanban_board_id = board_id
    team.kanban_provider = provider
    await session.commit()
    await session.refresh(team)
    return team


async def get_meeting_by_mtslink_id(
    session: AsyncSession, event_id: str
) -> Meeting | None:
    from sqlalchemy.orm import joinedload
    result = await session.execute(
        select(Meeting)
        .options(joinedload(Meeting.team))
        .where(Meeting.mtslink_event_id == event_id)
    )
    return result.unique().scalar_one_or_none()


async def get_meeting_by_record_id(
    session: AsyncSession, record_id: str
) -> Meeting | None:
    result = await session.execute(
        select(Meeting).where(Meeting.mtslink_record_id == record_id)
    )
    return result.scalar_one_or_none()


async def get_meeting_by_mtslink_session_id(
    session: AsyncSession, session_id: str
) -> Meeting | None:
    from sqlalchemy.orm import joinedload
    result = await session.execute(
        select(Meeting)
        .options(joinedload(Meeting.team))
        .where(Meeting.mtslink_session_id == session_id)
    )
    return result.unique().scalar_one_or_none()


async def create_meeting(
    session: AsyncSession,
    team_id: int,
    meeting_url: str,
    platform: str = "unknown",
    mtslink_event_id: str | None = None,
    mtslink_record_id: str | None = None,
    mtslink_session_id: str | None = None,
) -> Meeting:
    # Ищем активную встречу с таким же URL
    result = await session.execute(
        select(Meeting).where(
            Meeting.team_id == team_id,
            Meeting.meeting_url == meeting_url,
            Meeting.status.in_(["active", "recording"]),
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    meeting = Meeting(
        team_id=team_id,
        meeting_url=meeting_url,
        platform=platform,
        mtslink_event_id=mtslink_event_id,
        mtslink_record_id=mtslink_record_id,
        mtslink_session_id=mtslink_session_id,
        status="active",
    )
    session.add(meeting)
    await session.commit()
    await session.refresh(meeting)
    return meeting


async def update_meeting_status(
    session: AsyncSession,
    meeting_id: int,
    status: str,
) -> Meeting | None:
    meeting = await session.get(Meeting, meeting_id)
    if meeting:
        meeting.status = status
        await session.commit()
        await session.refresh(meeting)
    return meeting


async def update_meeting_transcript(
    session: AsyncSession,
    meeting_id: int,
    transcript: str,
    audio_path: str = "",
) -> Meeting | None:
    meeting = await session.get(Meeting, meeting_id)
    if meeting:
        meeting.transcript = transcript
        meeting.audio_path = audio_path
        meeting.status = "transcribed"
        await session.commit()
        await session.refresh(meeting)
    return meeting


async def update_meeting_summary(
    session: AsyncSession,
    meeting_id: int,
    summary: str,
) -> Meeting | None:
    meeting = await session.get(Meeting, meeting_id)
    if meeting:
        meeting.summary = summary
        meeting.status = "analyzed"
        await session.commit()
        await session.refresh(meeting)
    return meeting


async def update_meeting_llm_raw(
    session: AsyncSession,
    meeting_id: int,
    raw_output: str,
) -> Meeting | None:
    meeting = await session.get(Meeting, meeting_id)
    if meeting:
        meeting.raw_llm_output = raw_output
        await session.commit()
    return meeting


async def update_meeting_record_id(
    session: AsyncSession,
    meeting_id: int,
    record_id: str,
) -> Meeting | None:
    meeting = await session.get(Meeting, meeting_id)
    if meeting:
        meeting.mtslink_record_id = record_id
        await session.commit()
    return meeting


async def update_meeting_session_id(
    session: AsyncSession,
    meeting_id: int,
    session_id: str,
) -> Meeting | None:
    meeting = await session.get(Meeting, meeting_id)
    if meeting:
        meeting.mtslink_session_id = session_id
        await session.commit()
    return meeting


async def finish_meeting(
    session: AsyncSession,
    meeting_id: int,
    duration_sec: int | None = None,
) -> Meeting | None:
    from datetime import datetime, timezone

    meeting = await session.get(Meeting, meeting_id)
    if meeting:
        meeting.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
        meeting.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        meeting.status = "processed"
        if duration_sec is not None:
            meeting.duration_sec = duration_sec
        await session.commit()
        await session.refresh(meeting)
    return meeting


async def create_pending_invite(
    session: AsyncSession,
    team_id: int,
    username: str,
    invited_by: int,
) -> PendingInvite:
    username = username.lstrip("@").lower()
    invite = PendingInvite(
        team_id=team_id,
        username=username,
        invited_by=invited_by,
    )
    session.add(invite)
    await session.flush()
    return invite


async def get_pending_invite(
    session: AsyncSession,
    username: str,
) -> PendingInvite | None:
    username = username.lstrip("@").lower()
    result = await session.execute(
        select(PendingInvite).where(PendingInvite.username == username)
    )
    return result.scalar_one_or_none()


async def delete_pending_invite(
    session: AsyncSession,
    invite_id: int,
) -> None:
    result = await session.execute(
        select(PendingInvite).where(PendingInvite.id == invite_id)
    )
    invite = result.scalar_one_or_none()
    if invite:
        await session.delete(invite)


async def update_team_mtslink_token(
    session: AsyncSession,
    chat_id: int,
    token: str,
) -> None:
    team = await get_team_by_chat(session, chat_id)
    if team is None:
        return
    team.mtslink_token = token
    await session.commit()


async def set_active_board(
    session: AsyncSession,
    chat_id: int,
    board_id: str,
    board_name: str,
) -> None:
    team = await get_team_by_chat(session, chat_id)
    if team is None:
        return
    team.active_board_id = board_id
    team.active_board_name = board_name
    await session.commit()


# ── Standup ──────────────────────────────────────────────────────────────

async def create_or_update_standup(
    session: AsyncSession,
    *,
    team_id: int,
    user_id: int,
    display_name: str,
    date: datetime,
    done_today: str,
    plan_today: str,
    blockers: str,
    mood: str = "neutral",
) -> Standup:
    result = await session.execute(
        select(Standup).where(
            Standup.team_id == team_id,
            Standup.user_id == user_id,
            Standup.date == date,
        )
    )
    s = result.scalar_one_or_none()
    if s is None:
        s = Standup(
            team_id=team_id, user_id=user_id, display_name=display_name,
            date=date, done_today=done_today, plan_today=plan_today,
            blockers=blockers, mood=mood,
        )
        session.add(s)
    else:
        s.done_today = done_today
        s.plan_today = plan_today
        s.blockers = blockers
        s.mood = mood
    await session.flush()
    return s


async def get_standups_for_date(
    session: AsyncSession, team_id: int, date: datetime
) -> list[Standup]:
    result = await session.execute(
        select(Standup).where(
            Standup.team_id == team_id,
            Standup.date == date,
        )
    )
    return list(result.scalars().all())


# ── Blocker ───────────────────────────────────────────────────────────────

async def create_blocker(
    session: AsyncSession,
    *,
    team_id: int,
    reported_by: int,
    display_name: str,
    description: str,
    severity: str = "medium",
    standup_id: int | None = None,
    telegram_message_id: int | None = None,
) -> Blocker:
    b = Blocker(
        team_id=team_id,
        reported_by=reported_by,
        display_name=display_name,
        description=description,
        severity=severity,
        standup_id=standup_id,
        telegram_message_id=telegram_message_id,
    )
    session.add(b)
    await session.flush()
    return b


async def get_open_blockers(session: AsyncSession, team_id: int) -> list[Blocker]:
    result = await session.execute(
        select(Blocker).where(
            Blocker.team_id == team_id,
            Blocker.status == "open",
        ).order_by(Blocker.severity.desc(), Blocker.created_at)
    )
    return list(result.scalars().all())


async def resolve_blocker(session: AsyncSession, blocker_id: int) -> bool:
    result = await session.execute(select(Blocker).where(Blocker.id == blocker_id))
    b = result.scalar_one_or_none()
    if b:
        b.status = "resolved"
        b.resolved_at = datetime.utcnow()
        return True
    return False


async def dismiss_blocker(session: AsyncSession, blocker_id: int) -> bool:
    result = await session.execute(select(Blocker).where(Blocker.id == blocker_id))
    b = result.scalar_one_or_none()
    if b:
        b.status = "dismissed"
        return True
    return False


# ── TimeLog ───────────────────────────────────────────────────────────────

async def create_time_log(
    session: AsyncSession,
    *,
    team_id: int,
    user_id: int,
    source: str,
    minutes: int,
    description: str = "",
    source_id: str | None = None,
    date: datetime | None = None,
) -> TimeLog:
    tl = TimeLog(
        team_id=team_id,
        user_id=user_id,
        source=source,
        source_id=source_id,
        minutes=minutes,
        description=description,
        date=date or datetime.utcnow(),
    )
    session.add(tl)
    await session.flush()
    return tl


# ── Group activities (пульс-опросы, метафоры, квизы) ──────────────────────

async def create_activity_session(
    session: AsyncSession,
    *,
    team_id: int,
    activity_code: str,
    kind: str,
    is_anonymous: bool,
    chat_id: int,
    question: str,
) -> ActivitySession:
    s = ActivitySession(
        team_id=team_id,
        activity_code=activity_code,
        kind=kind,
        is_anonymous=is_anonymous,
        chat_id=chat_id,
        question=question,
    )
    session.add(s)
    await session.flush()
    return s


async def set_activity_message_id(
    session: AsyncSession, session_id: int, message_id: int
) -> None:
    s = await session.get(ActivitySession, session_id)
    if s is not None:
        s.telegram_message_id = message_id


async def get_activity_session(
    session: AsyncSession, session_id: int
) -> ActivitySession | None:
    return await session.get(ActivitySession, session_id)


async def list_open_activity_sessions(
    session: AsyncSession, team_id: int
) -> list[ActivitySession]:
    result = await session.execute(
        select(ActivitySession).where(
            ActivitySession.team_id == team_id,
            ActivitySession.status == "open",
        )
    )
    return list(result.scalars().all())


async def upsert_activity_response(
    session: AsyncSession,
    *,
    session_id: int,
    respondent_hash: str,
    user_id: int | None,
    answer_value: int | None = None,
    answer_text: str | None = None,
) -> bool:
    """Записывает/обновляет ответ. Возвращает True, если это был первый голос
    данного респондента в сессии (для аккуратной обратной связи). Дедупликация —
    по (session_id, respondent_hash)."""
    stmt = pg_insert(ActivityResponse).values(
        session_id=session_id,
        respondent_hash=respondent_hash,
        user_id=user_id,
        answer_value=answer_value,
        answer_text=answer_text,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["session_id", "respondent_hash"],
        set_={
            "answer_value": stmt.excluded.answer_value,
            "answer_text": stmt.excluded.answer_text,
        },
    ).returning(ActivityResponse.created_at)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def get_activity_responses(
    session: AsyncSession, session_id: int
) -> list[ActivityResponse]:
    result = await session.execute(
        select(ActivityResponse).where(ActivityResponse.session_id == session_id)
    )
    return list(result.scalars().all())


async def close_activity_session(session: AsyncSession, session_id: int) -> None:
    s = await session.get(ActivitySession, session_id)
    if s is not None and s.status == "open":
        s.status = "closed"
        s.closed_at = datetime.utcnow()


async def list_due_activity_sessions(
    session: AsyncSession,
) -> list[tuple[ActivitySession, Team]]:
    """Открытые сессии без опубликованных итогов, у которых истекло окно
    авто-закрытия (started_at + team.pulse_auto_close_minutes <= now UTC).
    Возвращает пары (сессия, команда) для постинга сводки в нужный чат."""
    from datetime import timedelta

    now = datetime.utcnow()
    result = await session.execute(
        select(ActivitySession, Team)
        .join(Team, Team.id == ActivitySession.team_id)
        .where(
            ActivitySession.status == "open",
            ActivitySession.summary_posted.is_(False),
        )
    )
    due: list[tuple[ActivitySession, Team]] = []
    for act, team in result.all():
        deadline = act.started_at + timedelta(minutes=team.pulse_auto_close_minutes)
        if deadline <= now:
            due.append((act, team))
    return due


async def mark_activity_summary_posted(session: AsyncSession, session_id: int) -> None:
    s = await session.get(ActivitySession, session_id)
    if s is not None:
        s.summary_posted = True
        if s.status == "open":
            s.status = "closed"
            s.closed_at = datetime.utcnow()


@dataclass
class PulseDayStat:
    day: datetime           # начало дня (UTC)
    count: int              # сколько голосов в этот день
    avg: float              # средний балл за день


@dataclass
class PulseAggregate:
    total_responses: int
    sessions: int
    avg: float                       # средний балл за весь период
    distribution: dict[int, int]     # {1: n1, ... 5: n5}
    by_day: list[PulseDayStat]       # хронологически по дням
    trend: str                       # "up" | "down" | "flat" | "n/a"


async def aggregate_pulse_responses(
    session: AsyncSession,
    team_id: int,
    *,
    days: int = 7,
) -> PulseAggregate:
    """Агрегирует анонимные пульс-голоса команды за последние N дней.

    Анонимность сохраняется: считаем только агрегаты (средний балл, распределение,
    динамика по дням), без привязки к respondent_hash/user_id в выдаче.
    """
    from datetime import timedelta

    since = datetime.utcnow() - timedelta(days=days)

    result = await session.execute(
        select(ActivityResponse.answer_value, ActivitySession.started_at)
        .join(ActivitySession, ActivitySession.id == ActivityResponse.session_id)
        .where(
            ActivitySession.team_id == team_id,
            ActivitySession.kind == "pulse",
            ActivitySession.started_at >= since,
            ActivityResponse.answer_value.isnot(None),
        )
    )
    rows = result.all()

    values: list[int] = [int(v) for v, _ in rows]
    distribution = {i: values.count(i) for i in range(1, 6)}
    total = len(values)
    avg = (sum(values) / total) if total else 0.0

    # Сколько уникальных сессий участвовало (по started_at-дню достаточно для тренда).
    sess_result = await session.execute(
        select(ActivitySession.id)
        .where(
            ActivitySession.team_id == team_id,
            ActivitySession.kind == "pulse",
            ActivitySession.started_at >= since,
        )
    )
    sessions = len(list(sess_result.scalars().all()))

    # Группировка по дню.
    day_buckets: dict[datetime, list[int]] = {}
    for v, started in rows:
        day = started.replace(hour=0, minute=0, second=0, microsecond=0)
        day_buckets.setdefault(day, []).append(int(v))

    by_day = [
        PulseDayStat(day=day, count=len(vals), avg=sum(vals) / len(vals))
        for day, vals in sorted(day_buckets.items())
    ]

    # Тренд: сравниваем средние первой и второй половины периода.
    if len(by_day) >= 2:
        mid = len(by_day) // 2
        first_avg = sum(d.avg for d in by_day[:mid]) / max(mid, 1)
        second_avg = sum(d.avg for d in by_day[mid:]) / max(len(by_day) - mid, 1)
        delta = second_avg - first_avg
        trend = "up" if delta >= 0.3 else "down" if delta <= -0.3 else "flat"
    else:
        trend = "n/a"

    return PulseAggregate(
        total_responses=total,
        sessions=sessions,
        avg=avg,
        distribution=distribution,
        by_day=by_day,
        trend=trend,
    )


@dataclass
class TeamSentimentAggregate:
    total: int
    positive: int
    negative: int
    neutral: int
    speech: int
    positive_pct: float
    negative_pct: float
    neutral_pct: float
    speech_pct: float


async def aggregate_team_sentiment(
    session: AsyncSession,
    team_id: int,
    *,
    days: int = 7,
    user_id: int | None = None,
) -> TeamSentimentAggregate:
    from datetime import timedelta

    since = datetime.utcnow() - timedelta(days=days)

    query = select(MessageSentiment).where(
        MessageSentiment.team_id == team_id,
        MessageSentiment.created_at >= since,
    )
    if user_id is not None:
        query = query.where(MessageSentiment.user_id == user_id)

    result = await session.execute(query)
    rows = list(result.scalars().all())

    total = len(rows)
    positive = sum(1 for r in rows if r.sentiment == "positive")
    negative = sum(1 for r in rows if r.sentiment == "negative")
    neutral = sum(1 for r in rows if r.sentiment == "neutral")
    speech = sum(1 for r in rows if r.sentiment == "speech")

    def pct(v: int) -> float:
        return round(v / total * 100, 1) if total else 0.0

    return TeamSentimentAggregate(
        total=total,
        positive=positive,
        negative=negative,
        neutral=neutral,
        speech=speech,
        positive_pct=pct(positive),
        negative_pct=pct(negative),
        neutral_pct=pct(neutral),
        speech_pct=pct(speech),
    )


async def save_message_sentiment(
    session: AsyncSession,
    team_id: int,
    user_id: int,
    display_name: str,
    sentiment: str,
) -> None:
    session.add(MessageSentiment(
        team_id=team_id,
        user_id=user_id,
        display_name=display_name,
        sentiment=sentiment,
    ))


async def save_message_risk(
    session: AsyncSession,
    team_id: int,
    user_id: int,
    display_name: str,
    message_text: str,
    risk_reason: str,
    yougile_task_id: str | None = None,
) -> MessageRisk:
    risk = MessageRisk(
        team_id=team_id,
        user_id=user_id,
        display_name=display_name,
        message_text=message_text[:500],
        risk_reason=risk_reason,
        yougile_task_id=yougile_task_id,
    )
    session.add(risk)
    await session.flush()
    return risk


async def get_recent_risks(
    session: AsyncSession,
    team_id: int,
    limit: int = 10,
) -> list[MessageRisk]:
    result = await session.execute(
        select(MessageRisk)
        .where(MessageRisk.team_id == team_id)
        .order_by(desc(MessageRisk.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_role_permissions(
    session: AsyncSession, team_id: int, role: str
) -> dict:
    try:
        result = await session.execute(
            select(RolePermission).where(
                RolePermission.team_id == team_id,
                RolePermission.role == role,
            )
        )
        rp = result.scalar_one_or_none()
    except Exception:
        logger.exception(
            "get_role_permissions: table access error for team=%s role=%s",
            team_id, role,
        )
        # Таблица role_permissions ещё не создана миграцией — разрешаем всё
        return {"allowed_intents": ["*"], "denied_intents": []}

    if rp is None:
        return {"allowed_intents": [], "denied_intents": []}
    return {
        "allowed_intents": rp.allowed_intents or [],
        "denied_intents": rp.denied_intents or [],
    }


async def init_default_permissions(session: AsyncSession, team_id: int) -> None:
    existing = await session.execute(
        select(RolePermission).where(RolePermission.team_id == team_id)
    )
    if existing.scalar_one_or_none() is not None:
        return

    session.add(RolePermission(
        team_id=team_id,
        role="admin",
        allowed_intents=["*"],
        denied_intents=[],
    ))
    session.add(RolePermission(
        team_id=team_id,
        role="member",
        allowed_intents=[
            "chat", "smalltalk", "search",
            "create_task_for", "show_my_tasks",
            "summarize_chat", "join_meeting",
        ],
        denied_intents=[
            "trash_task", "set_setting", "remove_news_topic",
            "add_news_topic", "transfer_deadline", "change_assignee",
            "close_task",
        ],
    ))
    await session.flush()
