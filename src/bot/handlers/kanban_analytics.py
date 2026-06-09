"""Аналитика сроков YouGile-доски — /kanban_analytics."""
import json
import time
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.handlers.yougile import YouGileClient
from src.db.repo import get_team_by_chat, get_or_create_user
from src.db.session import get_session
from src.llm.base import ChatMessage
from src.llm.router import get_provider_chain, llm_with_fallback

router = Router(name="kanban_analytics")

NOW_MS = lambda: int(time.time() * 1000)

SENTIMENT_PROMPT = (
    "Оцени общее настроение команды по названиям задач. "
    "Ответь ТОЛЬКО одной строкой: emoji + 2-4 слова "
    "(например: '😊 Команда в тонусе' или '😰 Много проблем').\n\n"
    "Задачи:\n{sample}"
)

ASSIGN_PROMPT = """\
Ты — помощник по управлению задачами. Определи наиболее вероятного исполнителя \
для каждой задачи из списка участников команды.

Участники команды (YouGile):
{users}

Задачи:
{tasks}

Верни ТОЛЬКО валидный JSON без пояснений и без markdown-блоков, строго в формате:
[
  {{"card_id": "...", "assignee_id": "...", "confidence": 0.85}},
  ...
]
Правила:
- assignee_id — это id из списка участников выше
- confidence от 0.0 до 1.0
- Если уверенность < 0.6 — ставь assignee_id: null
- Верни запись для КАЖДОЙ задачи из списка
"""


async def _analyze_sentiment(card_titles: list[str], message: Message) -> str:
    if not card_titles:
        return "😐 Недостаточно данных"
    sample = "\n".join(f"- {t}" for t in card_titles[:30])
    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            providers = await get_provider_chain(session, owner)
        if not providers:
            return "😐 Анализ недоступен (нет LLM-ключа)"
        reply = await llm_with_fallback(providers, [
            ChatMessage(role="user", content=SENTIMENT_PROMPT.format(sample=sample)),
        ], heavy=False, notify_bot=message.bot, notify_chat_id=message.chat.id)
        return reply.strip() or "😐 Анализ недоступен"
    except Exception:
        return "😐 Анализ недоступен"


async def _assign_tasks_by_ai(
    client: YouGileClient,
    cards: list[dict],
    message: Message,
) -> tuple[int, int]:
    """
    Запрашивает у LLM назначение исполнителей и применяет через YouGile API.
    Возвращает (назначено, пропущено).
    """
    if not cards:
        return 0, 0

    # 1. Получаем список YouGile-пользователей
    try:
        users = await client.get_users()
    except Exception:
        users = []

    if not users:
        return 0, 0

    # 2. Строим строки для промпта
    users_text = "\n".join(
        f"- id={u.get('id', '?')} name={u.get('name', '?')}"
        for u in users
    )
    tasks_text = "\n".join(
        f"- card_id={c.get('id', '?')} title={c.get('title', '?')}"
        for c in cards[:40]  # не больше 40 задач за раз
    )

    # 3. Запрашиваем LLM
    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            providers = await get_provider_chain(session, owner)
        if not providers:
            return 0, 0

        raw = await llm_with_fallback(providers, [
            ChatMessage(
                role="user",
                content=ASSIGN_PROMPT.format(users=users_text, tasks=tasks_text),
            )
        ], heavy=False, notify_bot=message.bot, notify_chat_id=message.chat.id)
    except Exception:
        return 0, 0

    # 4. Парсим JSON
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        assignments = json.loads(raw)
    except Exception:
        return 0, 0

    # 5. Применяем назначения через API
    assigned = 0
    skipped = 0
    for item in assignments:
        card_id = item.get("card_id")
        assignee_id = item.get("assignee_id")
        confidence = item.get("confidence", 0.0)

        if not card_id or not assignee_id or confidence < 0.6:
            skipped += 1
            continue
        try:
            await client.update_card(card_id, assigned=[assignee_id])
            assigned += 1
        except Exception:
            skipped += 1

    return assigned, skipped


@router.message(Command("kanban_analytics"))
async def cmd_kanban_analytics(message: Message) -> None:
    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)

    if not team or not team.kanban_token:
        await message.answer("❌ Сначала выполни /kanban_login")
        return
    if not team.kanban_board_id:
        await message.answer("❌ Сначала выбери доску /kanban_board")
        return

    client = YouGileClient(team.kanban_token, team.kanban_board_id)
    try:
        columns = await client.get_columns()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        return

    now = NOW_MS()
    lines = ["📊 <b>Аналитика доски</b>\n"]

    total_cards = 0
    risk_cards = []
    all_cards: list[dict] = []

    for col in columns:
        col_id = col["id"]
        col_title = col.get("title", "?")
        try:
            cards = await client.get_cards_in_column(col_id)
        except Exception:
            cards = []

        all_cards.extend(cards)

        if not cards:
            lines.append(f"<b>{col_title}</b>: пусто")
            continue

        ages = []
        for card in cards:
            ts = card.get("timestamp")
            if ts:
                age_days = (now - ts) / (1000 * 86400)
                ages.append((age_days, card.get("title", "?"), card.get("id")))

        total_cards += len(cards)

        if ages:
            avg_days = sum(a for a, _, _ in ages) / len(ages)
            lines.append(
                f"<b>{col_title}</b>: {len(cards)} задач, "
                f"среднее время {avg_days:.1f} дн."
            )
            threshold = avg_days * 2 if avg_days > 1 else 7
            for age_days, title, card_id in ages:
                if age_days >= threshold:
                    risk_cards.append((col_title, title, age_days))
        else:
            lines.append(f"<b>{col_title}</b>: {len(cards)} задач")

    lines.append(f"\n<b>Всего задач:</b> {total_cards}")

    if risk_cards:
        lines.append("\n⚠️ <b>Задачи под риском</b> (висят дольше нормы):")
        for col_title, title, age_days in risk_cards[:10]:
            lines.append(f"  • [{col_title}] {title[:50]} — {age_days:.0f} дн.")
    else:
        lines.append("\n✅ Все задачи в норме")

    # Настроение команды
    all_titles = [c.get("title", "") for c in all_cards if c.get("title")]
    sentiment = await _analyze_sentiment(all_titles, message)
    lines.append(f"\n🧠 <b>Настроение команды:</b> {sentiment}")

    # AI-назначение исполнителей
    unassigned = [
        c for c in all_cards
        if not c.get("assigned") and c.get("id") and c.get("title")
    ]
    if unassigned:
        lines.append(f"\n🤖 <b>Назначаю исполнителей</b> ({len(unassigned)} без назначения)...")
        await message.answer("\n".join(lines), parse_mode="HTML")

        assigned_count, skipped_count = await _assign_tasks_by_ai(client, unassigned, message)

        result_lines = [""]
        if assigned_count:
            result_lines.append(f"✅ Назначено: {assigned_count} задач")
        if skipped_count:
            result_lines.append(f"⏭ Пропущено (низкая уверенность): {skipped_count}")
        if not assigned_count and not skipped_count:
            result_lines.append("⚠️ Участники YouGile не найдены или нет LLM-ключа")
        await message.answer("\n".join(result_lines), parse_mode="HTML")
    else:
        lines.append("\n✅ Все задачи уже имеют исполнителей")
        await message.answer("\n".join(lines), parse_mode="HTML")
