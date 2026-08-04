"""Утилиты для работы с FSM: проверка ввода, ветки входа в стейт, навигация.

Паттерны:
- require_text: проверка, что message содержит текст; если нет — мягкий отказ без
  сброса стейта.
- enter_state: безопасная смена FSM-состояния через callback; блокирует навигацию,
  если активен чужой стейт (защита от перехвата стейт-инпута старыми кнопками).
"""
from __future__ import annotations

import logging
from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)


# Текст, который показываем пользователю при отправке не-текста в текстовый state.
NON_TEXT_HINT = (
    "⚠️ Пожалуйста, отправьте текст.\n"
    "Для отмены процесса используйте /cancel."
)


async def require_text(message: Message) -> str | None:
    """Возвращает текст сообщения или None.

    Если текст отсутствует — отвечает подсказкой и возвращает None (стейт НЕ сбрасывается).
    Использовать в начале каждого FSM-consumer'а.
    """
    if message.text is None:
        await message.answer(NON_TEXT_HINT)
        return None
    return message.text


async def enter_state(
    target_state: Any,
    state: FSMContext,
    event: Message | CallbackQuery,
    *,
    on_busy: str = "alert",
    extra_data: dict[str, Any] | None = None,
) -> bool:
    """Безопасно переходит в новое FSM-состояние.

    Если пользователь уже находится в другом стейте — не перезаписывает его:
    - on_busy="alert" (для CallbackQuery) — показывает alert "Сначала завершите
      текущий процесс" и возвращает False.
    - on_busy="answer" (для Message) — отправляет аналогичный ответ и возвращает False.

    При пустом стейте — устанавливает target_state, раскладывает extra_data в data
    и возвращает True.
    """
    current = await state.get_state()
    if current is not None:
        notice = "Сначала завершите текущий процесс (или отмените через /cancel)."
        if isinstance(event, CallbackQuery):
            try:
                await event.answer(notice, show_alert=True)
            except Exception:
                logger.exception("enter_state: failed to alert on busy state")
        else:
            try:
                await event.answer(notice)
            except Exception:
                logger.exception("enter_state: failed to answer on busy state")
        return False

    await state.set_state(target_state)
    if extra_data:
        await state.update_data(**extra_data)
    return True
