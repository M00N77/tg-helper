"""Управление словарём терминов команды (/dict).

Точка входа — команда /dict в командном чате (или ЛС владельца привязанной команды).
"""
import logging
import re
import tempfile
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Document, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from docx import Document as DocxDocument
import openpyxl

from src.bot.filters import get_team_for_event, OwnerOnly
from src.bot.states import DictStates
from src.core.dictionary_cache import dictionary_cache
from src.db.repo import (
    add_team_dictionary_term,
    delete_team_dictionary_term,
    list_team_dictionary,
)
from src.db.session import get_session


logger = logging.getLogger(__name__)
router = Router(name="dictionary")

# Разделитель термина и определения: первое вхождение " — " или " - " или " : "
_SEPARATOR_RE = re.compile(r"\s+[—\-:]\s+")

# Валидация имени файла для массовой загрузки
_ALLOWED_EXTENSIONS = {".txt", ".docx", ".xlsx"}

_MAX_LIST = 50


def _dict_menu_kb() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="➕ Добавить термин", callback_data="dict:add"),
        InlineKeyboardButton(text="📂 Загрузить файлом", callback_data="dict:upload"),
    )
    kb.row(
        InlineKeyboardButton(text="📋 Список терминов", callback_data="dict:list"),
        InlineKeyboardButton(text="🗑 Очистить словарь", callback_data="dict:clear"),
    )
    return kb


def _confirm_clear_kb() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Да, очистить", callback_data="dict:clear:confirm"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="dict:clear:cancel"),
    )
    return kb


async def _resolve_team(message: Message) -> int | None:
    """Достаёт team_id из сообщения. Для ЛС — команда владельца, для группы — по chat_id."""
    async with get_session() as session:
        team = await get_team_for_event(session, message)
    return team.id if team else None


# ── Команда /dict ─────────────────────────────────────────────────────────


@router.message(Command("dict"))
async def cmd_dict(message: Message, state: FSMContext) -> None:
    await state.clear()
    team_id = await _resolve_team(message)
    if team_id is None:
        await message.answer(
            "❌ Команда не найдена.\n\n"
            "Сначала создайте команду: /team → Создать команду"
        )
        return

    await state.update_data(team_id=team_id)
    await message.answer(
        "📖 <b>Словарь терминов команды</b>\n\n"
        "Здесь можно добавить профессиональный сленг, сокращения и "
        "внутренние термины вашей команды.\n"
        "Когда участник напишет сообщение с таким термином, бот подскажет "
        "его значение LLM для более точного понимания контекста.",
        reply_markup=_dict_menu_kb().as_markup(),
    )


# ── Добавление одного термина ─────────────────────────────────────────────


@router.callback_query(F.data == "dict:add")
async def cb_dict_add(callback: CallbackQuery, state: FSMContext) -> None:
    team_id = (await state.get_data()).get("team_id")
    if not team_id:
        await callback.message.edit_text("❌ Сессия устарела. Введите /dict заново.")
        await callback.answer()
        return

    await state.set_state(DictStates.waiting_for_single_term)
    await callback.message.edit_text(
        "✏️ <b>Добавление термина</b>\n\n"
        "Введите термин и его определение в формате:\n"
        "<code>Термин — Определение</code>\n\n"
        "Пример:\n"
        "<code>CR — Code Review, проверка кода перед мержем</code>\n\n"
        "Или отправьте /cancel для отмены.",
    )
    await callback.answer()


@router.message(DictStates.waiting_for_single_term, F.text)
async def step_dict_single_term(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    team_id = data.get("team_id")
    if not team_id:
        await message.answer("❌ Сессия устарела. Введите /dict заново.")
        await state.clear()
        return

    text = (message.text or "").strip()

    if text.lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Отменено.")
        return

    match = _SEPARATOR_RE.search(text)
    if not match:
        await message.answer(
            "⚠️ Неверный формат. Используйте разделитель <code> — </code> или <code> - </code>:\n"
            "<code>Термин — Определение</code>"
        )
        return

    term = text[:match.start()].strip()
    definition = text[match.end():].strip()

    if not term or not definition:
        await message.answer("⚠️ Термин и определение не могут быть пустыми.")
        return

    if len(term) > 256:
        await message.answer("⚠️ Термин слишком длинный (максимум 256 символов).")
        return

    async with get_session() as session:
        entry = await add_team_dictionary_term(
            session, team_id=team_id, term=term, definition=definition,
        )

    dictionary_cache.invalidate_team_cache(team_id)

    await state.clear()
    await message.answer(
        f"✅ Термин <b>{entry.term}</b> добавлен!\n\n"
        f"{entry.term} — {entry.definition}"
    )


@router.message(DictStates.waiting_for_single_term)
async def step_dict_single_term_invalid(message: Message) -> None:
    await message.answer("⚠️ Пожалуйста, отправьте текст в формате <code>Термин — Определение</code>")


# ── Массовая загрузка файлом ──────────────────────────────────────────────


@router.callback_query(F.data == "dict:upload")
async def cb_dict_upload(callback: CallbackQuery, state: FSMContext) -> None:
    team_id = (await state.get_data()).get("team_id")
    if not team_id:
        await callback.message.edit_text("❌ Сессия устарела. Введите /dict заново.")
        await callback.answer()
        return

    await state.set_state(DictStates.waiting_for_file)
    await callback.message.edit_text(
        "📂 <b>Массовая загрузка терминов</b>\n\n"
        "Пришлите файл в формате <b>.txt</b>, <b>.docx</b> или <b>.xlsx</b>.\n\n"
        "<b>.txt / .docx</b> — каждая строка один термин:\n"
        "<code>Термин — Определение</code>\n\n"
        "<b>.xlsx</b> — два столбца: <b>A</b> = термин, <b>B</b> = определение.\n\n"
        "Пример содержимого .txt:\n"
        "<code>CR — Code Review, проверка кода\n"
        "ASAP — As Soon As Possible, срочно\n"
        "RCA — Root Cause Analysis, анализ первопричин</code>",
    )
    await callback.answer()


@router.message(DictStates.waiting_for_file, F.document)
async def step_dict_file(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    team_id = data.get("team_id")
    if not team_id:
        await message.answer("❌ Сессия устарела. Введите /dict заново.")
        await state.clear()
        return

    doc: Document = message.document
    ext = Path(doc.file_name or "").suffix.lower() if doc.file_name else ""

    if ext not in _ALLOWED_EXTENSIONS:
        await message.answer(
            f"⚠️ Неподдерживаемый формат: {ext}. "
            f"Допустимы: {', '.join(sorted(_ALLOWED_EXTENSIONS))}"
        )
        return

    # Скачиваем файл
    try:
        suffix = ext or ".tmp"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
        await message.bot.download(file=doc.file_id, destination=tmp_path)
    except Exception:
        logger.exception("dictionary: file download failed")
        await message.answer("❌ Ошибка при скачивании файла. Попробуйте ещё раз.")
        return

    # Парсим файл
    parsed: list[dict] = []
    skipped = 0
    try:
        if ext == ".xlsx":
            wb = openpyxl.load_workbook(tmp_path, read_only=True)
            ws = wb.active
            for row in ws.iter_rows(values_only=True):
                if not row or not row[0] or not row[1]:
                    skipped += 1
                    continue
                term = str(row[0]).strip()
                definition = str(row[1]).strip()
                if not term or not definition or len(term) > 256:
                    skipped += 1
                    continue
                parsed.append({"term": term, "definition": definition})
            wb.close()
        else:
            raw_lines: list[str] = []
            if ext == ".txt":
                content = Path(tmp_path).read_text(encoding="utf-8-sig")
                raw_lines = content.splitlines()
            elif ext == ".docx":
                docx = DocxDocument(tmp_path)
                raw_lines = [p.text for p in docx.paragraphs if p.text.strip()]

            for line in raw_lines:
                line = line.strip()
                if not line:
                    continue
                m = _SEPARATOR_RE.search(line)
                if not m:
                    skipped += 1
                    continue
                term = line[:m.start()].strip()
                definition = line[m.end():].strip()
                if not term or not definition or len(term) > 256:
                    skipped += 1
                    continue
                parsed.append({"term": term, "definition": definition})
    except Exception:
        logger.exception("dictionary: file parse failed")
        await message.answer("❌ Ошибка при чтении файла. Проверьте его содержимое.")
        return
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass

    if not parsed:
        fmt_hint = (
            "для .txt/.docx используйте <code>Термин — Определение</code>, "
            "для .xlsx — два столбца (A = термин, B = значение)"
        )
        await message.answer(
            f"⚠️ Не удалось распознать ни одного термина.\n{fmt_hint}\n"
            f"Пропущено строк: {skipped}."
        )
        await state.clear()
        return

    # Bulk-insert
    async with get_session() as session:
        for item in parsed:
            await add_team_dictionary_term(
                session, team_id=team_id, term=item["term"], definition=item["definition"],
            )

    dictionary_cache.invalidate_team_cache(team_id)

    added = len(parsed)
    await state.clear()
    await message.answer(
        f"✅ <b>Готово!</b>\n\n"
        f"• Добавлено терминов: <b>{added}</b>\n"
        f"• Пропущено (неверный формат): <b>{skipped}</b>",
    )


@router.message(DictStates.waiting_for_file)
async def step_dict_file_invalid(message: Message) -> None:
    logger.warning(
        "dictionary: unexpected content in waiting_for_file state. "
        "chat_type=%s content_type=%s text=%s",
        message.chat.type,
        message.content_type,
        (message.text or "")[:100],
    )
    await message.answer(
        "⚠️ Пожалуйста, пришлите файл в формате <b>.txt</b>, <b>.docx</b> или <b>.xlsx</b>.\n"
        "Или /cancel для отмены."
    )


# ── Список терминов ───────────────────────────────────────────────────────


@router.callback_query(F.data == "dict:list")
async def cb_dict_list(callback: CallbackQuery, state: FSMContext) -> None:
    team_id = (await state.get_data()).get("team_id")
    if not team_id:
        team_id = await _resolve_team(callback.message)
    if not team_id:
        await callback.message.edit_text("❌ Команда не найдена.")
        await callback.answer()
        return

    async with get_session() as session:
        terms = await list_team_dictionary(session, team_id)

    if not terms:
        await callback.message.edit_text(
            "📭 Словарь пуст.\n\n"
            "Используйте /dict, чтобы добавить термины.",
        )
        await callback.answer()
        return

    total = len(terms)
    show = terms[:_MAX_LIST]
    lines = [f"📖 <b>Словарь терминов</b> (всего: {total})"]
    for i, t in enumerate(show, 1):
        lines.append(f"{i}. <b>{t.term}</b> — {t.definition[:120]}")

    if total > _MAX_LIST:
        lines.append(f"\n… и ещё {total - _MAX_LIST}")

    # Разбиваем на части, если сообщение слишком длинное
    full = "\n\n".join(lines) if len(lines) <= 2 else "\n".join(lines)
    if len(full) > 4000:
        # Отправляем файлом
        content = "\n".join(f"{t.term} — {t.definition}" for t in terms)
        from aiogram.types import BufferedInputFile
        await callback.message.answer(
            f"📖 <b>Словарь терминов</b> — всего {total} записей (отправлено файлом):"
        )
        await callback.message.answer_document(
            BufferedInputFile(content.encode("utf-8"), filename="team_dictionary.txt"),
        )
    else:
        await callback.message.edit_text(full, parse_mode="HTML")

    await callback.answer()


# ── Очистка словаря ───────────────────────────────────────────────────────


@router.callback_query(F.data == "dict:clear")
async def cb_dict_clear_confirm(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🗑 <b>Вы уверены, что хотите очистить весь словарь?</b>\n"
        "Все термины будут безвозвратно удалены.",
        reply_markup=_confirm_clear_kb().as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "dict:clear:confirm")
async def cb_dict_clear_execute(callback: CallbackQuery, state: FSMContext) -> None:
    team_id = (await state.get_data()).get("team_id")
    if not team_id:
        team_id = await _resolve_team(callback.message)
    if not team_id:
        await callback.message.edit_text("❌ Команда не найдена.")
        await callback.answer()
        return

    async with get_session() as session:
        terms = await list_team_dictionary(session, team_id)
        count = len(terms)
        for t in terms:
            await session.delete(t)

    dictionary_cache.invalidate_team_cache(team_id)

    await callback.message.edit_text(
        f"🗑 <b>Словарь очищен.</b>\n"
        f"Удалено терминов: {count}",
    )
    await callback.answer()


@router.callback_query(F.data == "dict:clear:cancel")
async def cb_dict_clear_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("❌ Очистка отменена.")
    await callback.answer()
