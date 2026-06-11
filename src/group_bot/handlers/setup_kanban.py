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

    role = await get_role(chat_id, user_id)
    if role != "admin":
        logger.info("setup_kanban denied: chat=%s user=%s role=%s", chat_id, user_id, role)
        if role == "none":
            await message.answer(
                "⛔ Команда не найдена в этом чате. Сначала выполните /i_am_director."
            )
        else:
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
        "📧 Введите email от аккаунта YouGile:\n\n"
        "💡 Если авторизация по email/паролю не сработает, используйте прямой ввод токена:\n"
        "<code>/kanban_token ВАШ_ТОКЕН ID_ДОСКИ</code>\n"
        "(токен: YouGile → Настройки → API → создать ключ)",
        reply_markup=_cancel_keyboard(),
    )


@router.message(Command("kanban_token"), GroupOnly())
async def cmd_kanban_token(message: Message, state: FSMContext):
    """Прямой ввод API-токена YouGile (без логина/пароля).

    Надёжный путь для группового чата: команда с '/'-префиксом не
    перехватывается catch-all хендлером group_free_text, а отсутствие FSM
    исключает конфликт состояний. Формат:
        /kanban_token TOKEN ID_ДОСКИ
        /kanban_token TOKEN:ID_ДОСКИ
        /kanban_token TOKEN        (доску задать потом через /kanban_board)
    """
    chat_id = message.chat.id
    user_id = message.from_user.id

    role = await get_role(chat_id, user_id)
    if role != "admin":
        logger.info("kanban_token denied: chat=%s user=%s role=%s", chat_id, user_id, role)
        if role == "none":
            await message.answer(
                "⛔ Команда не найдена в этом чате. Сначала выполните /i_am_director."
            )
        else:
            await message.answer("⛔ Только руководитель команды может настраивать канбан.")
        return

    # На всякий случай сбрасываем повисшее FSM-состояние от /setup_kanban.
    await state.clear()

    raw = (message.text or "").split(maxsplit=1)
    if len(raw) != 2 or not raw[1].strip():
        await message.answer(
            "❌ Укажите токен: <code>/kanban_token ТОКЕН ID_ДОСКИ</code>\n\n"
            "Токен: YouGile → Настройки → API → создать ключ.\n"
            "ID доски можно скопировать из URL доски (или задать позже /kanban_board)."
        )
        return

    args = raw[1].strip()
    # Поддерживаем разделители: пробел или двоеточие.
    if ":" in args and " " not in args:
        token, _, board_id = args.partition(":")
    else:
        parts = args.split(maxsplit=1)
        token = parts[0]
        board_id = parts[1].strip() if len(parts) > 1 else ""
    token = token.strip()
    board_id = board_id.strip()

    async with get_session() as session:
        team = await get_team_by_chat(session, chat_id)
    if team is None:
        await message.answer("Команда не найдена. Используйте /i_am_director.")
        return

    # Проверяем токен через API.
    client = YouGileClient(token, board_id or None)
    try:
        if board_id:
            columns = await client.get_columns()
            if not columns:
                await message.answer(
                    "⚠️ Токен принят, но на доске нет колонок. Проверьте ID доски."
                )
        else:
            boards = await client.get_boards()
            if not boards:
                await message.answer(
                    "⚠️ Токен принят, но доступных досок не найдено. "
                    "Проверьте права токена или укажите ID доски."
                )
    except Exception as e:
        logger.warning("kanban_token validation failed: %s", e)
        await message.answer(f"❌ Токен не прошёл проверку: {e}")
        return
    finally:
        await client.close()

    async with get_session() as session:
        await update_team_kanban(session, chat_id, token, board_id, "yougile")

    if board_id:
        await message.answer("✅ Канбан подключён! Токен и доска сохранены.")
    else:
        await message.answer(
            "✅ Токен сохранён. Теперь укажите доску:\n"
            "<code>/kanban_board ID_ДОСКИ</code>"
        )


@router.message(KanbanLoginStates.waiting_email, GroupOnly())
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


@router.message(KanbanLoginStates.waiting_password, GroupOnly())
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


@router.message(Command("kanban_board"), GroupOnly())
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


@router.message(Command("kanban_login"), GroupOnly())
async def cmd_kanban_login_group_hint(message: Message):
    """В группе авторизация по логину/паролю небезопасна (пароль виден всем).
    Перенаправляем на безопасные пути."""
    await message.answer(
        "🔒 В групповом чате вводить логин/пароль небезопасно.\n\n"
        "Используйте один из вариантов:\n"
        "• <code>/kanban_token ТОКЕН ID_ДОСКИ</code> — прямой ввод API-ключа\n"
        "• <code>/setup_kanban</code> — пошаговая настройка\n\n"
        "Либо авторизуйтесь в личке с ботом командой /kanban_login."
    )


@router.callback_query(F.data == "setup_kanban:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Отменено.")
    await callback.answer()
