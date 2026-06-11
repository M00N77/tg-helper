import logging
import re

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy import select

from src.config import settings as app_settings
from src.core.agent import route_group_intent
from src.core.timeutil import now_in_tz
from src.db.models import TeamMember
from src.db.repo import (
    create_pending_action,
    delete_pending_action,
    find_team_member_by_name,
    get_or_create_user,
    get_pending_action,
    get_team_by_chat,
    get_team_member,
    list_team_members,
    ensure_team_member,
)
from src.db.session import get_session
from src.group_bot.filters import GroupOnly
from src.group_bot.permissions import is_admin
from src.bot.handlers.yougile import YouGileClient
from src.llm.router import build_provider

logger = logging.getLogger(__name__)
router = Router(name="group_free_text")
router.message.filter(GroupOnly())


async def _create_kanban_task(
    message: Message, team, title: str, description: str, deadline: str | None, target_member,
) -> None:
    if not team.kanban_token or not team.kanban_board_id:
        await message.reply("📊 Канбан команды не настроен. Обратитесь к директору.")
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)
    try:
        columns = await client.get_columns()
        if not columns:
            await message.reply("❌ На доске нет колонок.")
            return
        column_id = columns[0]["id"]

        assignee_ids = []
        if target_member.yougile_user_id:
            assignee_ids = [target_member.yougile_user_id]

        await client.create_card(
            title, description, column_id,
            assignee_ids=assignee_ids if assignee_ids else None,
            deadline=deadline,
        )
    except Exception as e:
        logger.exception("create_card failed")
        await message.reply(f"❌ Ошибка при создании задачи: {e}")
        return

    who = "себе" if target_member.telegram_id == message.from_user.id else f"<b>{target_member.display_name or target_member.telegram_id}</b>"
    tail = f"\n📅 Срок: {deadline[:10]}" if deadline else ""
    await message.reply(f"✅ Задача «{title}» создана для {who}{tail}")


async def _request_approval(
    message: Message, team, title: str, description: str, deadline: str | None,
    target_member, author,
) -> None:
    async with get_session() as session:
        admins = [m for m in await list_team_members(session, team.id) if m.role == "admin"]

    if not admins:
        await message.reply(
            "⚠️ В команде нет назначенного директора, согласование невозможно. "
            "Используйте /i_am_director."
        )
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, author.telegram_id)
        action = await create_pending_action(
            session,
            user_id=owner.id,
            kind="group_task_approval",
            payload={
                "chat_id": message.chat.id,
                "title": title,
                "description": description,
                "deadline": deadline,
                "target_telegram_id": target_member.telegram_id,
                "requested_by": author.telegram_id,
                "team_id": team.id,
            },
        )

    tags = " ".join(
        f'<a href="tg://user?id={a.telegram_id}">@{a.display_name or a.telegram_id}</a>'
        for a in admins
    )
    tail = f"\n📅 Срок: {deadline[:10]}" if deadline else ""
    desc_line = f"\n📝 {description}" if description else ""

    await message.reply(
        f"⏳ <b>{author.display_name or author.telegram_id}</b> просит создать задачу для "
        f"<b>{target_member.display_name or target_member.telegram_id}</b>:\n\n"
        f"📋 {title}{desc_line}{tail}\n\n"
        f"(заявка #{action.id})\n"
        f"{tags}, требуется согласование. Ответьте «да» на это сообщение чтобы подтвердить."
    )


async def _handle_create_task_for(message: Message, team, member, intent: dict) -> None:
    title = (intent.get("title") or "").strip()
    if not title:
        await message.reply("❓ Не понял, какую задачу создать. Уточни название.")
        return

    description = (intent.get("description") or "").strip()
    deadline = intent.get("deadline")
    assignee_query = (intent.get("assignee") or "себя").strip().lower()

    async with get_session() as session:
        members = await list_team_members(session, team.id)

        if assignee_query in ("себя", "мне", "меня", "я"):
            target_member = member
        else:
            target_member = await find_team_member_by_name(
                session, team.id, intent.get("assignee", ""),
            )
            if target_member is None:
                await message.reply(
                    f"❓ Не нашёл «{intent.get('assignee')}» среди участников команды."
                )
                return

    is_self = target_member.telegram_id == member.telegram_id
    author_is_admin = member.role == "admin"

    if is_self or author_is_admin:
        await _create_kanban_task(message, team, title, description, deadline, target_member)
    else:
        await _request_approval(message, team, title, description, deadline, target_member, member)


async def _handle_show_my_tasks(message: Message, team, member) -> None:
    if not team.kanban_token or not team.kanban_board_id:
        await message.reply("📊 Канбан команды не настроен. Обратитесь к директору.")
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)
    try:
        columns = await client.get_columns()
    except Exception as e:
        await message.reply(f"❌ Ошибка при получении колонок: {e}")
        return

    parts = [f"📋 <b>Задачи для {member.display_name or 'вас'}</b>:\n"]
    found = 0
    for col in columns:
        try:
            cards = await client.get_cards_in_column(col["id"], limit=50)
        except Exception:
            continue
        for card in cards:
            assigned = card.get("assigned") or []
            if member.yougile_user_id and member.yougile_user_id in assigned:
                parts.append(f"  • {card.get('title', '?')[:50]}")
                found += 1
    if found == 0:
        await message.reply("✅ У вас нет активных задач на доске.")
        return
    await message.reply("\n".join(parts))


@router.message(F.reply_to_message & F.text.lower().in_(["да", "ok", "ок", "подтверждаю"]))
async def confirm_task_approval(message: Message) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await is_admin(chat_id, user_id):
        return

    replied_text = message.reply_to_message.text or ""
    m = re.search(r"заявка #(\d+)", replied_text)
    if not m:
        return
    action_id = int(m.group(1))

    async with get_session() as session:
        action = await get_pending_action(session, action_id)
        if action is None or action.kind != "group_task_approval":
            return
        payload = action.payload
        await delete_pending_action(session, action_id)
        team = await get_team_by_chat(session, chat_id)
        if team is None:
            return
        target_member = await get_team_member(session, team.id, payload["target_telegram_id"])
        if target_member is None:
            await message.reply("❌ Целевой участник больше не в команде.")
            return

    await _create_kanban_task(
        message, team, payload["title"], payload["description"],
        payload.get("deadline"), target_member,
    )


async def _find_task_by_title(client: YouGileClient, title_hint: str) -> dict | None:
    try:
        matches = await client.find_task_by_title(title_hint)
    except Exception:
        return None
    if not matches:
        return None
    return matches[0]


async def _is_task_owner(member, task: dict) -> bool:
    if not member.yougile_user_id:
        return False
    assigned = task.get("assigned") or []
    return member.yougile_user_id in assigned


async def _handle_edit_task(message: Message, team, member, intent: dict) -> None:
    title_hint = (intent.get("task_title_hint") or "").strip()
    if not title_hint:
        await message.reply("❓ Не понял, какую задачу редактировать. Уточни название.")
        return

    new_title = (intent.get("new_title") or "").strip()
    new_description = (intent.get("new_description") or "").strip()
    if not new_title and not new_description:
        await message.reply("❓ Укажи, что изменить: новое название или описание.")
        return

    if not team.kanban_token or not team.kanban_board_id:
        await message.reply("📊 Канбан команды не настроен.")
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)
    task = await _find_task_by_title(client, title_hint)
    if not task:
        await message.reply(f"❓ Не нашёл задачу «{title_hint}» на доске.")
        return

    if member.role != "admin" and not await _is_task_owner(member, task):
        await message.reply("⛔ Только автор задачи или руководитель может её редактировать.")
        return

    payload = {}
    if new_title:
        payload["title"] = new_title
    if new_description:
        payload["description"] = new_description

    try:
        await client.update_card(task["id"], **payload)
    except Exception as e:
        logger.exception("edit_task failed")
        await message.reply(f"❌ Ошибка при обновлении задачи: {e}")
        return

    parts = []
    if new_title:
        parts.append(f"название → «{new_title}»")
    if new_description:
        parts.append("описание обновлено")
    await message.reply(f"✅ Задача «{task.get('title', '?')}» обновлена: {', '.join(parts)}.")


async def _handle_transfer_deadline(message: Message, team, member, intent: dict) -> None:
    title_hint = (intent.get("task_title_hint") or "").strip()
    new_deadline = intent.get("new_deadline")

    if not title_hint:
        await message.reply("❓ Не понял, у какой задачи перенести срок. Уточни название.")
        return
    if not new_deadline:
        await message.reply("❓ Не понял, на какой срок перенести. Уточни дату.")
        return

    if not team.kanban_token or not team.kanban_board_id:
        await message.reply("📊 Канбан команды не настроен.")
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)
    task = await _find_task_by_title(client, title_hint)
    if not task:
        await message.reply(f"❓ Не нашёл задачу «{title_hint}» на доске.")
        return

    if member.role != "admin" and not await _is_task_owner(member, task):
        await message.reply("⛔ Только автор задачи или руководитель может менять срок.")
        return

    try:
        from src.bot.handlers.yougile import _parse_deadline
        deadline_payload = _parse_deadline(new_deadline)
        await client.update_card(task["id"], **deadline_payload)
    except Exception as e:
        logger.exception("transfer_deadline failed")
        await message.reply(f"❌ Ошибка при обновлении срока: {e}")
        return

    tail = new_deadline[:10] if len(new_deadline) > 10 else new_deadline
    await message.reply(f"✅ Срок задачи «{task.get('title', '?')}» перенесён на {tail}.")


async def _handle_change_assignee(message: Message, team, member, intent: dict) -> None:
    title_hint = (intent.get("task_title_hint") or "").strip()
    new_assignee_name = (intent.get("new_assignee_name") or "").strip()

    if not title_hint:
        await message.reply("❓ Не понял, у какой задачи сменить ответственного. Уточни название.")
        return
    if not new_assignee_name:
        await message.reply("❓ Не понял, на кого переназначить. Уточни имя.")
        return

    if not team.kanban_token or not team.kanban_board_id:
        await message.reply("📊 Канбан команды не настроен.")
        return

    async with get_session() as session:
        target_member = await find_team_member_by_name(session, team.id, new_assignee_name)
    if not target_member:
        await message.reply(f"❓ Не нашёл участника «{new_assignee_name}» в команде.")
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)
    task = await _find_task_by_title(client, title_hint)
    if not task:
        await message.reply(f"❓ Не нашёл задачу «{title_hint}» на доске.")
        return

    if member.role != "admin":
        await _request_approval(
            message, team,
            title=task.get("title", title_hint),
            description=f"Смена ответственного на {new_assignee_name}",
            deadline=None,
            target_member=target_member,
            author=member,
        )
        return

    if not target_member.yougile_user_id:
        await message.reply(
            f"❌ У {new_assignee_name} не привязан YouGile-аккаунт. "
            "Используйте /link_yougile чтобы привязать."
        )
        return

    try:
        await client.update_card(task["id"], assigned=[target_member.yougile_user_id])
    except Exception as e:
        logger.exception("change_assignee failed")
        await message.reply(f"❌ Ошибка при смене ответственного: {e}")
        return

    await message.reply(
        f"✅ Задача «{task.get('title', '?')}» переназначена на <b>{target_member.display_name or new_assignee_name}</b>."
    )


async def _handle_close_task(message: Message, team, member, intent: dict) -> None:
    title_hint = (intent.get("task_title_hint") or "").strip()
    if not title_hint:
        await message.reply("❓ Не понял, какую задачу закрыть. Уточни название.")
        return

    if member.role != "admin":
        await message.reply("⛔ Только руководитель может закрыть задачу.")
        return

    if not team.kanban_token or not team.kanban_board_id:
        await message.reply("📊 Канбан команды не настроен.")
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)
    task = await _find_task_by_title(client, title_hint)
    if not task:
        await message.reply(f"❓ Не нашёл задачу «{title_hint}» на доске.")
        return

    try:
        columns = await client.get_columns()
        done_col = None
        for col in columns:
            title_lower = col.get("title", "").lower()
            if title_lower in ("done", "готово", "closed", "завершено", "выполнено"):
                done_col = col
                break
        if done_col:
            await client.move_card(task["id"], done_col["id"])
        else:
            await client.update_card(task["id"], completed=True)
    except Exception as e:
        logger.exception("close_task failed")
        await message.reply(f"❌ Ошибка при закрытии задачи: {e}")
        return

    await message.reply(f"✅ Задача «{task.get('title', '?')}» закрыта.")


async def _handle_comment_task(message: Message, team, member, intent: dict) -> None:
    title_hint = (intent.get("task_title_hint") or "").strip()
    comment = (intent.get("comment") or "").strip()

    if not title_hint:
        await message.reply("❓ Не понял, к какой задаче комментарий. Уточни название.")
        return
    if not comment:
        await message.reply("❓ Пустой комментарий. Напиши текст заметки.")
        return

    if not team.kanban_token or not team.kanban_board_id:
        await message.reply("📊 Канбан команды не настроен.")
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)
    task = await _find_task_by_title(client, title_hint)
    if not task:
        await message.reply(f"❓ Не нашёл задачу «{title_hint}» на доске.")
        return

    # Комментировать может автор задачи или руководитель.
    if member.role != "admin" and not await _is_task_owner(member, task):
        await message.reply("⛔ Только автор задачи или руководитель может её комментировать.")
        return

    author = member.display_name or str(member.telegram_id)
    body = f"💬 {author}: {comment}"
    try:
        await client.add_comment(task["id"], body)
    except Exception as e:
        logger.exception("comment_task failed")
        await message.reply(f"❌ Ошибка при добавлении комментария: {e}")
        return

    await message.reply(f"✅ Комментарий добавлен к задаче «{task.get('title', '?')}».")


@router.message(F.text & ~F.text.startswith("/"))
async def group_free_text(message: Message) -> None:
    chat_id = message.chat.id
    user_id = message.from_user.id
    display_name = message.from_user.full_name

    async with get_session() as session:
        team = await get_team_by_chat(session, chat_id)
        if team is None:
            return

        member = await ensure_team_member(session, team.id, user_id, display_name)
        owner_for_llm = await get_or_create_user(session, app_settings.owner_telegram_id)
        provider = await build_provider(session, owner_for_llm)

    if provider is None:
        return

    now_local_str = now_in_tz(owner_for_llm.settings.timezone).strftime("%Y-%m-%d %H:%M")

    try:
        intent = await route_group_intent(
            provider, message.text, now_local=now_local_str, tz_name=owner_for_llm.settings.timezone,
        )
    except Exception:
        logger.exception("group agent failed")
        return

    kind = intent.get("intent")

    if kind == "chat":
        reply = intent.get("reply", "")
        if reply:
            await message.reply(reply)
        return

    if kind == "show_my_tasks":
        await _handle_show_my_tasks(message, team, member)
        return

    if kind == "create_task_for":
        await _handle_create_task_for(message, team, member, intent)
        return

    if kind == "edit_task":
        await _handle_edit_task(message, team, member, intent)
        return

    if kind == "transfer_deadline":
        await _handle_transfer_deadline(message, team, member, intent)
        return

    if kind == "change_assignee":
        await _handle_change_assignee(message, team, member, intent)
        return

    if kind == "close_task":
        await _handle_close_task(message, team, member, intent)
        return

    if kind == "comment_task":
        await _handle_comment_task(message, team, member, intent)
        return
