import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.handlers.yougile import YouGileClient
from src.bot.states import YouGileSetupStates
from src.db.repo import get_team_by_chat, update_team_kanban
from src.db.session import get_session
from src.group_bot.permissions import get_role

logger = logging.getLogger(__name__)
router = Router(name="setup_yougile")


@router.message(YouGileSetupStates.waiting_token)
async def step_token(message: Message, state: FSMContext):
    token = (message.text or "").strip()
    if not token:
        await message.answer("❌ Токен не может быть пустым.")
        return

    data = await state.get_data()
    chat_id = data.get("setup_chat_id")
    if not chat_id:
        await state.clear()
        await message.answer("❌ Сессия истекла. Начните заново из группы.")
        return

    role = await get_role(chat_id, message.from_user.id)
    if role != "admin":
        await state.clear()
        await message.answer("⛔ Только руководитель команды может настраивать канбан.")
        return

    await message.answer("⏳ Проверяю токен и получаю список досок...")

    client = YouGileClient(token)
    try:
        boards = await client.get_boards()
    except Exception as e:
        logger.warning("YouGile token validation failed: %s", e)
        await message.answer(f"❌ Токен не прошёл проверку: {e}")
        return
    finally:
        await client.close()

    if len(boards) == 0:
        await message.answer("❌ В аккаунте YouGile нет досок. Создайте доску в YouGile и повторите.")
        await state.clear()
        return

    await state.update_data(kanban_token=token, boards=boards)

    if len(boards) == 1:
        board = boards[0]
        async with get_session() as session:
            await update_team_kanban(session, chat_id, token, board["id"], "yougile")
        await state.clear()
        await message.answer(
            f"✅ Найдена одна доска «{board['title']}» — автоматически привязал её.\n"
            f"Канбан настроен!"
        )
        return

    kb = InlineKeyboardBuilder()
    for i, b in enumerate(boards):
        kb.row(InlineKeyboardButton(
            text=f"{b['title']}",
            callback_data=f"yg_setup:board:{i}"
        ))
    kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="yg_setup:cancel"))

    await state.set_state(YouGileSetupStates.choosing_board)
    await message.answer(
        "📋 Найдено несколько досок. Выберите нужную:",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(YouGileSetupStates.choosing_board, F.data.startswith("yg_setup:board:"))
async def cb_choose_board(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[2])
    data = await state.get_data()
    boards = data.get("boards", [])
    token = data.get("kanban_token", "")
    chat_id = data.get("setup_chat_id")

    if idx < 0 or idx >= len(boards) or not chat_id:
        await callback.answer("Ошибка: доска не найдена", show_alert=True)
        return

    board = boards[idx]
    async with get_session() as session:
        await update_team_kanban(session, chat_id, token, board["id"], "yougile")

    await state.clear()
    await callback.message.edit_text(
        f"✅ Доска «{board['title']}» привязана!\n"
        f"Канбан настроен."
    )
    await callback.answer()


@router.callback_query(YouGileSetupStates.choosing_board, F.data == "yg_setup:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Отменено.")
    await callback.answer()
