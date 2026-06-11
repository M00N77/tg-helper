import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.handlers.yougile import YouGileClient
from src.db.repo import get_team_by_chat, update_team_kanban
from src.db.session import get_session
from src.group_bot.filters import GroupOnly
from src.group_bot.permissions import is_admin, get_role

logger = logging.getLogger(__name__)
router = Router(name="group_setup_kanban")


class KanbanLoginStates(StatesGroup):
    waiting_email = State()
    waiting_password = State()


def _cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="setup_kanban:cancel")
    return builder.as_markup()


@router.message(Command("setup_kanban"), GroupOnly())
async def cmd_setup_kanban(message: Message, state: FSMContext):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await is_admin(chat_id, user_id):
        await message.answer("⛔ Только руководитель команды может настраивать канбан.")
        return

    async with get_session() as session:
        team = await get_team_by_chat(session, chat_id)

    if team is None:
        await message.answer("Команда не найдена. Используйте /i_am_director.")
        return

    if team.kanban_token:
        await message.answer("📊 Канбан уже подключён. Используйте /settings для просмотра статуса.")
        return

    await state.set_state(KanbanLoginStates.waiting_email)
    await message.answer(
        "📧 Введите email от аккаунта YouGile:",
        reply_markup=_cancel_keyboard(),
    )


@router.message(KanbanLoginStates.waiting_email)
async def step_email(message: Message, state: FSMContext):
    email = (message.text or "").strip()
    if "@" not in email:
        await message.answer(
            "❌ Введите корректный email (например, user@example.com).",
            reply_markup=_cancel_keyboard(),
        )
        return

    await state.update_data(email=email)
    await state.set_state(KanbanLoginStates.waiting_password)
    await message.answer(
        "🔑 Введите пароль от аккаунта YouGile:",
        reply_markup=_cancel_keyboard(),
    )


@router.message(KanbanLoginStates.waiting_password)
async def step_password(message: Message, state: FSMContext):
    password = (message.text or "").strip()
    if not password:
        await message.answer(
            "❌ Пароль не может быть пустым.",
            reply_markup=_cancel_keyboard(),
        )
        return

    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    email = data["email"]

    wait_msg = await message.answer("⏳ Получаю токен...")

    client = YouGileClient(api_token="", board_id="")
    try:
        token = await client.generate_token(email, password, "")
    except Exception as e:
        await state.clear()
        await wait_msg.edit_text(f"❌ Ошибка авторизации: {e}")
        return
    finally:
        await client.close()

    chat_id = message.chat.id

    async with get_session() as session:
        await update_team_kanban(session, chat_id, token, None, "yougile")

    await state.clear()
    await wait_msg.edit_text(
        f"✅ Авторизация успешна!\n"
        f"Токен сохранён.\n\n"
        f"Теперь укажите ID доски командой:\n"
        f"<code>/kanban_board ID_ДОСКИ</code>\n\n"
        f"ID доски можно скопировать из URL вашей доски в YouGile."
    )


@router.message(Command("kanban_board"))
async def cmd_kanban_board(message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if not await is_admin(chat_id, user_id):
        await message.answer("⛔ Только руководитель команды может менять доску.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("❌ Укажите ID доски: <code>/kanban_board ID_ДОСКИ</code>")
        return

    board_id = parts[1].strip()
    if not board_id:
        await message.answer("❌ ID доски не может быть пустым.")
        return

    async with get_session() as session:
        team = await get_team_by_chat(session, chat_id)

    if team is None or not team.kanban_token:
        await message.answer("❌ Сначала выполните /setup_kanban для получения токена.")
        return

    client = YouGileClient(team.kanban_token, board_id)
    try:
        columns = await client.get_columns()
    except Exception as e:
        await message.answer(f"❌ Ошибка при проверке доски: {e}")
        return
    finally:
        await client.close()

    if not columns:
        await message.answer("❌ Доска не содержит колонок. Проверьте ID.")
        return

    async with get_session() as session:
        await update_team_kanban(session, chat_id, team.kanban_token, board_id, "yougile")

    await message.answer("✅ Доска подключена! Теперь участники могут использовать бота.")


@router.callback_query(F.data == "setup_kanban:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Отменено.")
    await callback.answer()
