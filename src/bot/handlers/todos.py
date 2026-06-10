from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import OwnerOnly
from src.core.timeutil import fmt_local
from src.db.repo import (
    get_or_create_user,
    hard_delete_expired_trash,
    list_open_commitments,
    list_trashed_commitments,
    restore_commitment,
    trash_commitment,
    update_commitment_status,
)
from src.db.session import get_session


router = Router(name="todos")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())


def _format(c, tz_name: str) -> str:
    who = "Я" if c.direction == "mine" else (c.peer_name or "Они")
    deadline = fmt_local(c.deadline_at, tz_name)
    return f"<b>{who}</b> · {c.text} (до {deadline})"


def _kb(commitment_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Выполнено", callback_data=f"todo:done:{commitment_id}"),
        InlineKeyboardButton(text="🗑 В корзину", callback_data=f"todo:trash:{commitment_id}"),
    )
    return kb.as_markup()


@router.message(Command("todos"))
async def cmd_todos(message: Message) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        items = await list_open_commitments(session, owner)
        tz_name = owner.settings.timezone

    if not items:
        await message.answer("Открытых обязательств нет 🎉")
        return

    await message.answer(f"📋 Открытых обязательств: <b>{len(items)}</b>")
    for c in items[:30]:
        await message.answer(_format(c, tz_name), reply_markup=_kb(c.id))


@router.message(Command("trash"))
async def cmd_trash(message: Message) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        items = await list_trashed_commitments(session, owner)
        tz_name = owner.settings.timezone

    if not items:
        await message.answer("Корзина пуста 🗑")
        return

    await message.answer(f"🗑 Корзина: <b>{len(items)}</b> обязательств")
    for c in items[:30]:
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(
            text="♻ Восстановить", callback_data=f"todo:restore:{c.id}",
        ))
        await message.answer(
            _format(c, tz_name) + f"\n\n<i>Удалено: {fmt_local(c.deleted_at, tz_name)}</i>",
            reply_markup=kb.as_markup(),
        )


@router.message(Command("restore"))
async def cmd_restore(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer("Укажи ID обязательства: /restore 42")
        return
    try:
        cid = int(args.split()[0])
    except ValueError:
        await message.answer("ID должно быть числом")
        return
    async with get_session() as session:
        ok = await restore_commitment(session, cid)
    if ok:
        await message.answer(f"♻ Обязательство <b>#{cid}</b> восстановлено из корзины")
    else:
        await message.answer(f"Обязательство <b>#{cid}</b> не найдено в корзине")


@router.callback_query(F.data.startswith("todo:done:"))
async def cb_done(callback: CallbackQuery) -> None:
    cid = int(callback.data.split(":")[2])
    async with get_session() as session:
        await update_commitment_status(session, cid, "done")
    if callback.message:
        await callback.message.edit_text(callback.message.html_text + "\n\n✅ Готово")
    await callback.answer()


@router.callback_query(F.data.startswith("todo:trash:"))
async def cb_trash(callback: CallbackQuery) -> None:
    cid = int(callback.data.split(":")[2])
    async with get_session() as session:
        ok = await trash_commitment(session, cid)
    if ok:
        if callback.message:
            await callback.message.edit_text(callback.message.html_text + "\n\n🗑 В корзине")
    else:
        await callback.answer("Уже в корзине", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data.startswith("todo:restore:"))
async def cb_restore(callback: CallbackQuery) -> None:
    cid = int(callback.data.split(":")[2])
    async with get_session() as session:
        ok = await restore_commitment(session, cid)
    if ok:
        if callback.message:
            await callback.message.edit_text(callback.message.html_text + "\n\n♻ Восстановлено")
    else:
        await callback.answer("Не найдено в корзине", show_alert=True)
        return
    await callback.answer()
