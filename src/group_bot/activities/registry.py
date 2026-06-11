"""Реестр групповых HR-активностей (проективные методики).

Паттерн Strategy/Plugin: каждая механика описывает себя сама (вопрос, клавиатуру,
как агрегировать ответы). Шедулер и хендлеры не знают про конкретные опросы —
работают через общий протокол ActivityPlugin. Чтобы добавить новую механику
(метафора, квиз, ice-breaker), достаточно создать класс и зарегистрировать его
в REGISTRY — трогать scheduler.py/handlers.py не нужно.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Protocol

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


CALLBACK_PREFIX = "act"  # формат callback_data: act:<session_id>:<value>


class ActivityPlugin(Protocol):
    code: str
    kind: str          # pulse | metaphor | quiz | icebreaker
    is_anonymous: bool

    def build_question(self) -> str: ...

    def build_keyboard(self, session_id: int) -> InlineKeyboardMarkup | None: ...

    def summarize(self, values: list[int], texts: list[str]) -> str: ...


@dataclass
class _Option:
    value: int
    label: str


class PulsePoll:
    """Анонимный пульс-опрос настроения по шкале 1..5 (проективная диагностика).

    Вопрос-метафора выбирается случайно из набора, чтобы опрос не приедался.
    Ответы анонимны: считаем средний балл и распределение, не раскрывая, кто как
    проголосовал.
    """

    code = "pulse_mood"
    kind = "pulse"
    is_anonymous = True

    _QUESTIONS = [
        "Если бы ваше рабочее состояние сегодня было погодой — какая это погода?",
        "На сколько «заряжена ваша батарейка» к концу дня?",
        "Каким был ваш сегодняшний рабочий ритм?",
        "Насколько комфортно вам работалось в команде сегодня?",
    ]

    _OPTIONS = [
        _Option(1, "1 · 🌧 тяжело"),
        _Option(2, "2 · 🌥 так себе"),
        _Option(3, "3 · ⛅️ нормально"),
        _Option(4, "4 · 🌤 хорошо"),
        _Option(5, "5 · ☀️ отлично"),
    ]

    def build_question(self) -> str:
        q = random.choice(self._QUESTIONS)
        return (
            "🫶 <b>Пульс команды</b>\n\n"
            f"{q}\n\n"
            "<i>Ответ анонимный — никто, включая бота, не увидит, кто как ответил. "
            "Можно переголосовать, засчитается последний вариант.</i>"
        )

    def build_keyboard(self, session_id: int) -> InlineKeyboardMarkup:
        kb = InlineKeyboardBuilder()
        for opt in self._OPTIONS:
            kb.button(
                text=opt.label,
                callback_data=f"{CALLBACK_PREFIX}:{session_id}:{opt.value}",
            )
        kb.adjust(1)
        return kb.as_markup()

    def summarize(self, values: list[int], texts: list[str]) -> str:
        if not values:
            return "🫶 <b>Пульс команды</b>\n\nНикто не успел проголосовать 🙈"
        avg = sum(values) / len(values)
        dist = {i: values.count(i) for i in range(1, 6)}
        peak = max(dist.values())
        bars = "\n".join(
            f"{i}: {'▰' * dist[i]}{'▱' * (peak - dist[i])} ({dist[i]})"
            for i in range(1, 6)
        )
        mood = (
            "☀️ команда в тонусе" if avg >= 4
            else "⛅️ рабочее состояние" if avg >= 3
            else "🌧 стоит обратить внимание"
        )
        return (
            "🫶 <b>Пульс команды — итоги</b>\n\n"
            f"Проголосовало: <b>{len(values)}</b>\n"
            f"Средний балл: <b>{avg:.1f}/5</b> · {mood}\n\n"
            f"{bars}"
        )


# Реестр доступных механик по коду.
REGISTRY: dict[str, ActivityPlugin] = {
    PulsePoll.code: PulsePoll(),
}

# Активность, запускаемая шедулером по расписанию (для MVP — одна).
DEFAULT_SCHEDULED_ACTIVITY = PulsePoll.code


def get_activity(code: str) -> ActivityPlugin | None:
    return REGISTRY.get(code)
