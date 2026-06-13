import logging

from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.db.repo import approve_pending_task, get_pending_task
from src.db.session import get_session

logger = logging.getLogger(__name__)
router = Router(name="task_approval")


class TaskApprovalCallback(CallbackData, prefix="task_approve"):
    task_id: int
    action: str


def approval_keyboard(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardBuilder([
        [
            InlineKeyboardButton(
                text="✅ ОК (В канбан)",
                callback_data=TaskApprovalCallback(task_id=task_id, action="ok").pack(),
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=TaskApprovalCallback(task_id=task_id, action="reject").pack(),
            ),
        ]
    ]).as_markup()


async def _mock_create_task(title: str, description: str | None) -> dict:
    """Мок-функция создания задачи в канбане. Заменить на реальный вызов YouGile."""
    logger.info("mock_create_task: title=%s", title)
    return {"id": "mock_task_id"}


@router.callback_query(TaskApprovalCallback.filter(F.action == "ok"))
async def cb_task_approve(callback: CallbackQuery, callback_data: TaskApprovalCallback) -> None:
    task_id = callback_data.task_id

    async with get_session() as session:
        task = await approve_pending_task(session, task_id)
        if task is None:
            await callback.answer("Задача уже обработана", show_alert=True)
            return

        try:
            result = await _mock_create_task(task.task_title, task.task_description)
            logger.info("Task %d approved, yougile_id=%s", task_id, result.get("id"))
        except Exception as e:
            logger.exception("Failed to create task in kanban for task_id=%d", task_id)
            await callback.answer("Ошибка создания задачи в канбане", show_alert=True)
            return

    await callback.message.edit_text(
        f"✅ Задача подтверждена и отправлена в канбан!\n\n"
        f"📋 <b>{task.task_title}</b>\n"
        f"{'🔹 ' + task.task_description if task.task_description else ''}"
    )
    await callback.answer()


@router.callback_query(TaskApprovalCallback.filter(F.action == "reject"))
async def cb_task_reject(callback: CallbackQuery, callback_data: TaskApprovalCallback) -> None:
    async with get_session() as session:
        task = await get_pending_task(session, callback_data.task_id)
        if task is None or task.status != "pending":
            await callback.answer("Задача уже обработана", show_alert=True)
            return

    await callback.message.edit_text(f"❌ Задача отклонена.\n\n📋 <b>{task.task_title}</b>")
    await callback.answer()
