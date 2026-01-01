from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from celery_worker import celery_app
from app.config import settings
from app.database.db import get_db_sync
from app.database.models import Source, Keyword

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# =========================
# Helpers: Celery triggers
# =========================

def _send_task(task_name: str) -> str:
    res = celery_app.send_task(task_name, queue="aibot")
    return str(res.id)


# =========================
# Keyboards
# =========================

def kb_main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🧩 Источники", callback_data="menu:sources")
    kb.button(text="🔑 Ключевые слова", callback_data="menu:keywords")
    kb.button(text="🚀 Parse", callback_data="task:parse")
    kb.button(text="🧠 Generate", callback_data="task:generate")
    kb.button(text="📣 Publish", callback_data="task:publish")
    kb.adjust(2, 3)
    return kb.as_markup()


def kb_sources(rows: list[Source]):
    kb = InlineKeyboardBuilder()
    for s in rows:
        mark = "✅" if s.enabled else "⛔️"
        kb.button(text=f"{mark} {s.name}", callback_data=f"src:toggle:{s.id}")
    kb.button(text="⬅️ Назад", callback_data="menu:home")
    kb.adjust(1)
    return kb.as_markup()


def kb_keywords(rows: list[Keyword]):
    kb = InlineKeyboardBuilder()
    # Только просмотр (CRUD через команды оставим на следующий шаг)
    kb.button(text="⬅️ Назад", callback_data="menu:home")
    kb.adjust(1)
    return kb.as_markup()


# =========================
# Text renderers
# =========================

def render_home_text() -> str:
    return (
        "🛠 **aibot admin**\n\n"
        "Выберите раздел.\n\n"
        "_Важно: меню не “уезжает”, потому что бот редактирует одно и то же сообщение._"
    )


def render_sources_text(rows: list[Source]) -> str:
    lines = ["🧩 **Источники**", "", "Нажимай на источник, чтобы включить/выключить:"]
    if not rows:
        lines.append("\n(источников нет)")
        return "\n".join(lines)

    for s in rows:
        mark = "✅" if s.enabled else "⛔️"
        lines.append(f"- {mark} **{s.name}** — `{s.type}` — `{s.url}`")
    return "\n".join(lines)


def render_keywords_text(rows: list[Keyword]) -> str:
    lines = ["🔑 **Ключевые слова**", ""]
    if not rows:
        lines.append("(пусто — фильтрация не применяется)")
        lines.append("")
        lines.append("Добавление/удаление сделаем следующим шагом.")
        return "\n".join(lines)

    lines.append("Текущий список:")
    for k in rows:
        lines.append(f"- `{k.word}` (`{k.id}`)")
    lines.append("")
    lines.append("Добавление/удаление сделаем следующим шагом.")
    return "\n".join(lines)


# =========================
# Menu actions (edit same message)
# =========================

async def show_home(message: Message):
    await message.answer(render_home_text(), reply_markup=kb_main_menu(), parse_mode="Markdown")


async def edit_to_home(cq: CallbackQuery):
    if not cq.message:
        await cq.answer()
        return
    await cq.message.edit_text(render_home_text(), reply_markup=kb_main_menu(), parse_mode="Markdown")
    await cq.answer()


async def edit_to_sources(cq: CallbackQuery):
    if not cq.message:
        await cq.answer()
        return
    with get_db_sync() as session:
        rows = session.query(Source).order_by(Source.created_at.desc()).all()

    await cq.message.edit_text(render_sources_text(rows), reply_markup=kb_sources(rows), parse_mode="Markdown")
    await cq.answer()


async def edit_to_keywords(cq: CallbackQuery):
    if not cq.message:
        await cq.answer()
        return
    with get_db_sync() as session:
        rows = session.query(Keyword).order_by(Keyword.word.asc()).all()

    await cq.message.edit_text(render_keywords_text(rows), reply_markup=kb_keywords(rows), parse_mode="Markdown")
    await cq.answer()


# =========================
# Command handlers
# =========================

async def cmd_start(msg: Message):
    # Меню показываем сразу при старте
    await show_home(msg)


async def cmd_menu(msg: Message):
    await show_home(msg)


# =========================
# Callback handlers
# =========================

async def cb_menu_router(cq: CallbackQuery):
    data = (cq.data or "")
    if data == "menu:home":
        await edit_to_home(cq)
        return
    if data == "menu:sources":
        await edit_to_sources(cq)
        return
    if data == "menu:keywords":
        await edit_to_keywords(cq)
        return
    await cq.answer()


async def cb_toggle_source(cq: CallbackQuery):
    """
    Переключаем enabled и ОБНОВЛЯЕМ то же сообщение (кнопки не “уезжают”).
    """
    if not cq.message:
        await cq.answer()
        return

    parts = (cq.data or "").split(":")
    # ожидаем "src:toggle:<id>"
    if len(parts) != 3:
        await cq.answer("bad callback", show_alert=False)
        return

    source_id = parts[2]

    with get_db_sync() as session:
        src = session.get(Source, source_id)
        if not src:
            await cq.answer("Источник не найден", show_alert=False)
            return
        src.enabled = not bool(src.enabled)
        session.commit()

        rows = session.query(Source).order_by(Source.created_at.desc()).all()

    # короткое “toast”-уведомление без нового сообщения
    await cq.answer("Обновлено", show_alert=False)

    # перерисовываем экран источников (на месте)
    await cq.message.edit_text(render_sources_text(rows), reply_markup=kb_sources(rows), parse_mode="Markdown")


async def cb_tasks(cq: CallbackQuery):
    """
    Запускаем Celery и даём toast через answer(), без сервисных сообщений.
    """
    data = (cq.data or "")
    mapping = {
        "task:parse": "app.tasks.parse_news",
        "task:generate": "app.tasks.generate_chain_post",
        "task:publish": "app.tasks.publish_latest_post",
    }
    task_name = mapping.get(data)
    if not task_name:
        await cq.answer()
        return

    task_id = _send_task(task_name)
    await cq.answer(f"Запущено: {task_name} ({task_id[:8]})", show_alert=False)

    # остаёмся на том же экране (кнопки не “уезжают”)
    # можно чуть обновить текст, но без необходимости — не трогаем cq.message


# =========================
# Main
# =========================

async def main():
    if not settings.TG_BOT_TOKEN:
        raise RuntimeError("TG_BOT_TOKEN is not set")

    bot = Bot(token=settings.TG_BOT_TOKEN)
    dp = Dispatcher()

    # commands
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_menu, Command("menu"))

    # callbacks
    dp.callback_query.register(cb_menu_router, lambda c: (c.data or "").startswith("menu:"))
    dp.callback_query.register(cb_toggle_source, lambda c: (c.data or "").startswith("src:toggle:"))
    dp.callback_query.register(cb_tasks, lambda c: (c.data or "").startswith("task:"))

    logger.info("Bot started. Username will be available after first getMe().")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())