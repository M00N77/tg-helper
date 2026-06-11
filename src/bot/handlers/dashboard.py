"""PM Dashboard — /pm_dashboard + старый /dashboard."""
import time
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.filters import OwnerOrTeamMember
from src.bot.handlers.yougile import YouGileClient
from src.db.repo import (
    get_open_blockers,
    get_or_create_user,
    get_standups_for_date,
    get_team_by_chat,
    get_team_members,
    list_open_commitments,
)
from src.db.session import get_session
from src.llm.base import ChatMessage
from src.llm.router import build_provider

router = Router(name="dashboard")
router.message.filter(OwnerOrTeamMember())

NOW_MS = lambda: int(time.time() * 1000)
TODAY = lambda: datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

MOOD_PROMPT = (
    "Оцени общий тон команды по последним сообщениям и стендапам. "
    "Выдели главные риски (1-2 фразы). "
    "Ответь СТРОГО в формате:\n"
    "MOOD: позитивное|нейтральное|напряжённое\n"
    "RISKS: <текст>\n\n"
    "Данные:\n{data}"
)


@router.message(Command("pm_dashboard"))
async def cmd_pm_dashboard(message: Message) -> None:
    if not message.from_user:
        return

    wait = await message.answer("⏳ Собираю PM-дашборд...")
    today = TODAY()
    week_ago = today - timedelta(days=7)

    async with get_session() as session:
        team = await get_team_by_chat(session, message.chat.id)
        if not team:
            await wait.edit_text("❌ Команда не найдена. /team для создания.")
            return

        members = await get_team_members(session, team.id)
        open_blockers = await get_open_blockers(session, team.id)
        standups_today = await get_standups_for_date(session, team.id, today)
        owner = await get_or_create_user(session, message.from_user.id)
        commitments = await list_open_commitments(session, owner)
        provider = await build_provider(session, owner)

    lines = [
        f"📋 <b>Product Manager Dashboard</b>",
        f"🏢 {team.name or 'Команда'} · {today.strftime('%d.%m.%Y')}\n",
    ]

    # Стендап
    answered = len(standups_today)
    total_m = len(members)
    moods = [s.mood for s in standups_today]
    mood_emoji = "🟢" if moods.count("positive") > len(moods) // 2 else (
        "🔴" if moods.count("negative") > len(moods) // 2 else "🟡"
    )
    lines.append(f"☀ <b>Стендап сегодня:</b> {answered}/{total_m} ответили {mood_emoji}")

    # Блокеры
    critical = [b for b in open_blockers if b.severity == "critical"]
    high = [b for b in open_blockers if b.severity == "high"]
    medium = [b for b in open_blockers if b.severity == "medium"]
    if open_blockers:
        lines.append(
            f"\n🚧 <b>Блокеры: {len(open_blockers)}</b>"
            f"  🔴{len(critical)} 🟠{len(high)} 🟡{len(medium)}"
        )
        for b in open_blockers[:3]:
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(b.severity, "⚠️")
            lines.append(f"  {icon} {b.display_name}: {b.description[:80]}")
    else:
        lines.append("\n✅ <b>Блокеров нет</b>")

    # Обязательства
    mine = [c for c in commitments if c.direction == "mine"]
    lines.append(f"\n📌 <b>Обязательства:</b> {len(mine)} открытых")

    # Канбан
    total_cards = 0
    risk_cards = 0
    cycle_times = []
    throughput = 0
    if team.kanban_token and team.kanban_board_id:
        try:
            client = YouGileClient(team.kanban_token, team.kanban_board_id)
            columns = await client.get_columns()
            now_ms = NOW_MS()
            week_ms = int(week_ago.timestamp() * 1000)
            done_col_keywords = ("готово", "done", "завершено", "closed")
            for col in columns:
                try:
                    cards = await client.get_cards_in_column(col["id"], limit=100)
                except Exception:
                    cards = []
                total_cards += len(cards)
                is_done_col = any(k in col.get("title", "").lower() for k in done_col_keywords)
                for c in cards:
                    ts = c.get("timestamp", 0)
                    if ts:
                        age_d = (now_ms - ts) / 86400000
                        cycle_times.append(age_d)
                    if is_done_col and ts and ts >= week_ms:
                        throughput += 1
                ages = [(now_ms - c["timestamp"]) / 86400000 for c in cards if c.get("timestamp")]
                avg = sum(ages) / len(ages) if ages else 0
                threshold = avg * 2 if avg > 1 else 7
                risk_cards += sum(1 for a in ages if a >= threshold)
            avg_cycle = sum(cycle_times) / len(cycle_times) if cycle_times else 0
            lines.append(
                f"\n📊 <b>Канбан:</b> {total_cards} задач · {risk_cards} под риском"
                f"\n📈 <b>Velocity:</b> cycle {avg_cycle:.1f}д · throughput {throughput}/нед"
            )
        except Exception as e:
            lines.append(f"\n📊 Канбан: ошибка ({e})")
    else:
        lines.append("\n📊 Канбан: не подключён")

    # LLM-анализ рисков
    if provider:
        try:
            standup_texts = "\n".join(
                f"{s.display_name}: {s.done_today} / план: {s.plan_today}"
                + (f" / блокер: {s.blockers}" if s.blockers else "")
                for s in standups_today[:10]
            )
            blocker_texts = "\n".join(
                f"{b.severity}: {b.description[:100]}" for b in open_blockers[:5]
            )
            data = f"Стендапы:\n{standup_texts}\n\nБлокеры:\n{blocker_texts}"
            raw = await provider.chat(
                [ChatMessage(role="user", content=MOOD_PROMPT.format(data=data))],
                heavy=False,
            )
            lines.append(f"\n💡 <b>Анализ:</b>\n{raw.strip()[:400]}")
        except Exception:
            pass

    await wait.edit_text("\n".join(lines), parse_mode="HTML")


@router.message(Command("dashboard"))
async def cmd_dashboard(message: Message) -> None:
    if message.from_user is None:
        return

    wait = await message.answer("⏳ Собираю данные...")
    lines = ["📋 <b>Manager Dashboard</b>\n"]

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        commitments = await list_open_commitments(session, owner)
        team = await get_team_by_chat(session, message.chat.id)

    mine = [c for c in commitments if c.direction == "mine"]
    theirs = [c for c in commitments if c.direction == "theirs"]
    lines.append("📌 <b>Обязательства</b>")
    lines.append(f"  Мои открытые: {len(mine)}")
    lines.append(f"  Ждут от других: {len(theirs)}\n")

    if team and team.kanban_token and team.kanban_board_id:
        try:
            client = YouGileClient(team.kanban_token, team.kanban_board_id)
            columns = await client.get_columns()
            now = NOW_MS()
            total = 0
            risk = 0
            col_lines = []
            for col in columns:
                try:
                    cards = await client.get_cards_in_column(col["id"], limit=100)
                except Exception:
                    cards = []
                count = len(cards)
                total += count
                ages = [(now - c["timestamp"]) / 86400000 for c in cards if c.get("timestamp")]
                avg = sum(ages) / len(ages) if ages else 0
                threshold = avg * 2 if avg > 1 else 7
                col_risk = sum(1 for a in ages if a >= threshold)
                risk += col_risk
                flag = " ⚠️" if col_risk else ""
                col_lines.append(
                    f"  {col.get('title','?')}: {count} задач"
                    + (f", avg {avg:.1f}дн{flag}" if avg else "")
                )
            lines.append("📊 <b>Канбан-доска</b>")
            lines.extend(col_lines)
            lines.append(f"  Всего: {total} | Под риском: {risk}\n")
        except Exception as e:
            lines.append(f"📊 Канбан: ошибка ({e})\n")
    else:
        lines.append("📊 Канбан: не подключён\n")

    total_issues = len(mine) + (risk if team and team.kanban_token else 0)
    if total_issues == 0:
        lines.append("✅ <b>Всё в порядке — рисков нет</b>")
    elif total_issues <= 3:
        lines.append(f"🟡 <b>Требует внимания:</b> {total_issues} пункт(а)")
    else:
        lines.append(f"🔴 <b>Критично:</b> {total_issues} пунктов требуют внимания")

    await wait.edit_text("\n".join(lines), parse_mode="HTML")
