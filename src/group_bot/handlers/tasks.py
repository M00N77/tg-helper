import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.handlers.yougile import YouGileClient
from src.db.repo import (
    confirm_pending_team_task,
    get_pending_team_task,
    get_team_by_chat,
    get_team_member,
    mark_team_task_approved,
    mark_team_task_failed,
    reject_pending_team_task,
)
from src.db.session import get_session

logger = logging.getLogger(__name__)
router = Router(name="group_tasks")


def _confirm_kb(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardBuilder(
        [
            [
                InlineKeyboardButton(
                    text="✅ Да, беру",
                    callback_data=f"task_confirm:{task_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Нет",
                    callback_data=f"task_reject:{task_id}",
                ),
            ]
        ]
    ).as_markup()


@router.callback_query(F.data.startswith("task_confirm:"))
async def cb_task_confirm(callback: CallbackQuery) -> None:
    task_id = int(callback.data.split(":", 1)[1])
    assignee_id = callback.from_user.id

    async with get_session() as session:
        team = await get_team_by_chat(session, callback.message.chat.id)
        if not team or not team.kanban_token:
            await callback.answer("Канбан не настроен", show_alert=True)
            return

        task = await confirm_pending_team_task(session, task_id, assignee_id)
        if task is None:
            await callback.answer("Задача уже обработана", show_alert=True)
            return

        assignee = await get_team_member(session, team.id, assignee_id)
        if not assignee or not assignee.yougile_user_id:
            await callback.message.edit_text(
                "❌ У вас не привязан YouGile-аккаунт.\n"
                "Используйте /team или настройки доски.",
            )
            await callback.answer()
            return

        columns = []
        client = YouGileClient(team.kanban_token, team.kanban_board_id)
        try:
            columns = await client.get_columns()
        except Exception as e:
            logger.exception("get_columns failed")
            await _revert_and_notify(session, task_id, f"Ошибка YouGile: {e}", callback)
            return

        if not columns:
            await _revert_and_notify(session, task_id, "На доске нет колонок", callback)
            return

        try:
            result = await client.create_card(
                title=task.title,
                description=task.description or "",
                column_id=columns[0]["id"],
                assignee_ids=[assignee.yougile_user_id],
            )
        except Exception as e:
            logger.exception("create_card failed")
            await _revert_and_notify(session, task_id, f"Ошибка YouGile: {e}", callback)
            return

        await mark_team_task_approved(session, task_id, result.get("id", ""))

    await callback.message.edit_text(
        f"✅ Задача принята!\n\n"
        f"📋 <b>{task.title}</b>\n"
        f"{(task.description or '') + chr(10) if task.description else ''}"
        f"👤 Исполнитель: {callback.from_user.full_name}",
    )
    await callback.answer()


async def _revert_and_notify(
    session, task_id: int, error: str, callback: CallbackQuery,
) -> None:
    await mark_team_task_failed(session, task_id, error)
    await callback.message.edit_text(
        f"⚠️ <b>Ошибка создания задачи в YouGile</b>\n\n"
        f"{error}\n\n"
        f"Статус возвращён в «ожидание» — можно попробовать ещё раз.",
        reply_markup=_confirm_kb(task_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task_reject:"))
async def cb_task_reject(callback: CallbackQuery) -> None:
    task_id = int(callback.data.split(":", 1)[1])
    assignee_id = callback.from_user.id

    async with get_session() as session:
        task = await reject_pending_team_task(session, task_id, assignee_id)
        if task is None:
            await callback.answer("Задача уже обработана", show_alert=True)
            return

    await callback.message.edit_text(
        f"❌ Задача отклонена.\n\n"
        f"📋 <b>{task.title}</b>",
    )
    await callback.answer()
