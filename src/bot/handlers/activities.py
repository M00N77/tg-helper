"""Групповые HR-активности: управление пульс-опросами и приём псевдонимных голосов (HMAC).

Команды (в командном чате):
  /activities_on        — включить расписание пульс-опросов
  /activities_off       — выключить
  /pulse_time HH:MM     — задать время ежедневного опроса (TZ Europe/Moscow)
  /pulse_close_after N  — через сколько минут авто-подводить итоги (N>=1)
  /pulse                — запустить пульс-опрос прямо сейчас (для теста/демо)
  /pulse_results        — подвести итоги последнего открытого опроса и закрыть его

Приём ответов — через inline-кнопки (callback_query), НЕ через reply, чтобы не
конфликтовать со статус-опрос хендлером и обеспечить псевдонимизацию: для анонимной сессии
в БД пишется только HMAC-хеш респондента, без telegram_id.
"""
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from src.bot.filters import OwnerOrTeamMember
from src.crypto import respondent_hash
from src.db.repo import (
    get_activity_session,
    get_team_by_chat,
    list_open_activity_sessions,
    upsert_activity_response,
)
from src.db.session import get_session
from src.group_bot.activities.registry import CALLBACK_PREFIX

logger = logging.getLogger(__name__)
router = Router(name="activities")
router.message.filter(OwnerOrTeamMember())


def _is_valid_hm(value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 2:
        return False
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return 0 <= h <= 23 and 0 <= m <= 59


@router.message(Command("activities_on"))
async def cmd_activities_on(message: Message) -> None:
    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team:
            await message.answer("❌ Это не командный чат. Сначала /team → Создать.")
            return
        team.activities_enabled = True
        pulse_time = team.pulse_time
    await message.answer(
        f"✅ Пульс-опросы включены. Ежедневно в <b>{pulse_time}</b> (МСК), по будням.\n"
        f"Изменить время: /pulse_time ЧЧ:ММ · запустить сейчас: /pulse",
        parse_mode="HTML",
    )


@router.message(Command("activities_off"))
async def cmd_activities_off(message: Message) -> None:
    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team:
            await message.answer("❌ Команда не найдена.")
            return
        team.activities_enabled = False
    await message.answer("🛑 Пульс-опросы по расписанию выключены.")


@router.message(Command("pulse_time"))
async def cmd_pulse_time(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not _is_valid_hm(arg):
        await message.answer("Укажите время в формате ЧЧ:ММ, например: /pulse_time 17:30")
        return
    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team:
            await message.answer("❌ Команда не найдена.")
            return
        team.pulse_time = arg
    await message.answer(f"✅ Время пульс-опроса: <b>{arg}</b> (МСК).", parse_mode="HTML")


@router.message(Command("pulse_close_after"))
async def cmd_pulse_close_after(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    try:
        minutes = int(arg)
    except ValueError:
        await message.answer("Укажите число минут, например: /pulse_close_after 60")
        return
    if minutes < 1:
        await message.answer("Минут должно быть не меньше 1.")
        return
    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team:
            await message.answer("❌ Команда не найдена.")
            return
        team.pulse_auto_close_minutes = minutes
    await message.answer(
        f"✅ Итоги опроса будут подводиться автоматически через <b>{minutes}</b> мин после старта.",
        parse_mode="HTML",
    )


@router.message(Command("pulse"))
async def cmd_pulse_now(message: Message) -> None:
    """Запускает пульс-опрос немедленно (демо/тест)."""
    from src.bot.app import get_bot
    from src.group_bot.activities.scheduler import _launch_pulse

    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
    if not team:
        await message.answer("❌ Это не командный чат. Сначала /team → Создать.")
        return
    bot = get_bot()
    if bot:
        await _launch_pulse(bot, team)


@router.message(Command("pulse_results"))
async def cmd_pulse_results(message: Message) -> None:
    """Подводит итоги последнего открытого опроса команды и закрывает его."""
    from src.bot.app import get_bot
    from src.group_bot.activities.scheduler import post_activity_summary

    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team:
            await message.answer("❌ Команда не найдена.")
            return
        open_sessions = await list_open_activity_sessions(session, team.id)

    if not open_sessions:
        await message.answer("Нет активных опросов. Запустить: /pulse")
        return

    act = max(open_sessions, key=lambda s: s.started_at)
    bot = get_bot()
    if bot:
        ok = await post_activity_summary(bot, act)
        if not ok:
            await message.answer("❌ Не удалось подвести итоги.")


@router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:"))
async def on_activity_answer(cb: CallbackQuery) -> None:
    """Приём анонимного голоса по inline-кнопке."""
    if not cb.from_user or not cb.data:
        return

    parts = cb.data.split(":")
    if len(parts) != 3:
        await cb.answer()
        return
    try:
        session_id = int(parts[1])
        value = int(parts[2])
    except ValueError:
        await cb.answer()
        return

    async with get_session() as session:
        act = await get_activity_session(session, session_id)
        if act is None or act.status != "open":
            await cb.answer("Этот опрос уже завершён.", show_alert=False)
            return

        # Анонимность: стабильный хеш респондента в рамках сессии, без user_id.
        rhash = respondent_hash(cb.from_user.id, session_id)
        stored_user_id = None if act.is_anonymous else cb.from_user.id

        await upsert_activity_response(
            session,
            session_id=session_id,
            respondent_hash=rhash,
            user_id=stored_user_id,
            answer_value=value,
        )

    # Тихий ответ только голосующему — без спама в чат.
    await cb.answer("Голос учтён 🤍", show_alert=False)
