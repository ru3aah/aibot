from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import List, Tuple, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from sqlalchemy import text

from celery_worker import celery_app
from app.config import settings
from app.database.db import get_db_sync
from app.database.models import Source, Keyword, FilterSettings
from app.database.data_types import SourceType

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

MAX_ACTIVE_KEYWORDS = 5


MENU_TEXT = (
    "🛠️ aibot — панель управления\n\n"
    "Выбирай раздел кнопками ниже.\n"
)


class KWAdd(StatesGroup):
    waiting_word = State()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _send_task(task_name: str) -> str:
    res = celery_app.send_task(task_name, queue="aibot")
    return str(res.id)


def _cb(data: str) -> str:
    return data


def _kb(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


# =========================
# FILTER SETTINGS (LANGUAGE)
# =========================

def _load_filter_settings_row(session) -> dict:
    row = session.execute(
        text(
            """
            SELECT id, language, updated_at, active_keywords_json
            FROM filter_settings
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
    ).fetchone()

    if not row:
        session.execute(
            text(
                """
                INSERT INTO filter_settings (id, language, updated_at, active_keywords_json)
                VALUES (:id, :language, :updated_at, :active_keywords_json)
                """
            ),
            {
                "id": "default",
                "language": "ru",
                "updated_at": now_utc(),
                "active_keywords_json": json.dumps([], ensure_ascii=False),
            },
        )
        session.commit()
        return {
            "id": "default",
            "language": "ru",
            "updated_at": now_utc(),
            "active_keywords_json": json.dumps([]),
        }

    return {
        "id": row[0],
        "language": row[1],
        "updated_at": row[2],
        "active_keywords_json": row[3],
    }


def _set_language(session, lang: str) -> None:
    fs = _load_filter_settings_row(session)
    session.execute(
        text(
            """
            UPDATE filter_settings
            SET language=:language, updated_at=:updated_at
            WHERE id=:id
            """
        ),
        {
            "language": lang,
            "updated_at": now_utc(),
            "id": fs["id"],
        },
    )
    session.commit()


# =========================
# KEYBOARDS
# =========================

def _menu_kb() -> InlineKeyboardMarkup:
    return _kb(
        [
            [
                InlineKeyboardButton(text="📰 Источники", callback_data=_cb("menu:sources")),
                InlineKeyboardButton(text="🔑 Ключевые слова", callback_data=_cb("menu:keywords")),
            ],
            [
                InlineKeyboardButton(text="🌐 Язык", callback_data=_cb("menu:language")),
            ],
            [
                InlineKeyboardButton(text="🚀 Parse", callback_data=_cb("task:parse")),
                InlineKeyboardButton(text="🧠 Generate", callback_data=_cb("task:generate")),
                InlineKeyboardButton(text="📣 Publish", callback_data=_cb("task:publish")),
            ],
        ]
    )


def _language_kb(current: str) -> InlineKeyboardMarkup:
    langs = [
        ("ru", "🇷🇺 Русский"),
        ("en", "🇬🇧 English"),
        ("es", "🇪🇸 Español"),
        ("de", "🇩🇪 Deutsch"),
    ]

    rows: List[List[InlineKeyboardButton]] = []

    for code, title in langs:
        mark = "✅ " if code == current else ""
        rows.append(
            [InlineKeyboardButton(text=f"{mark}{title}", callback_data=f"lang:set:{code}")]
        )

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:root")])
    return _kb(rows)


# =========================
# MENU HANDLERS
# =========================

async def _show_menu(msg: Message) -> None:
    await msg.answer(MENU_TEXT, reply_markup=_menu_kb())


async def _edit_menu(call: CallbackQuery) -> None:
    await call.message.edit_text(MENU_TEXT, reply_markup=_menu_kb())


async def cmd_start(msg: Message):
    await _show_menu(msg)


async def cmd_menu(msg: Message):
    await _show_menu(msg)


async def cb_menu_root(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await _edit_menu(call)
    await call.answer()


# =========================
# LANGUAGE
# =========================

async def cb_menu_language(call: CallbackQuery, state: FSMContext):
    await state.clear()
    with get_db_sync() as session:
        fs = _load_filter_settings_row(session)
        current = (fs.get("language") or "ru").lower()

    await call.message.edit_text(
        "🌐 Язык публикации\n\n"
        "Выбранный язык будет использоваться при генерации постов.",
        reply_markup=_language_kb(current),
    )
    await call.answer()


async def cb_language_set(call: CallbackQuery):
    parts = (call.data or "").split(":")
    if len(parts) != 3:
        await call.answer("bad callback")
        return

    lang = parts[2]

    with get_db_sync() as session:
        _set_language(session, lang)

    await call.answer("язык сохранён")
    await cb_menu_language(call, FSMContext)


# =========================
# TASK BUTTONS
# =========================

async def cb_task_parse(call: CallbackQuery):
    tid = _send_task("app.tasks.parse_news")
    await call.answer("parse отправлен")
    await call.message.edit_text(
        MENU_TEXT + f"\n✅ parse_news отправлен. task_id={tid}",
        reply_markup=_menu_kb(),
    )


async def cb_task_generate(call: CallbackQuery):
    tid = _send_task("app.tasks.generate_chain_post")
    await call.answer("generate отправлен")
    await call.message.edit_text(
        MENU_TEXT + f"\n✅ generate_chain_post отправлен. task_id={tid}",
        reply_markup=_menu_kb(),
    )


async def cb_task_publish(call: CallbackQuery):
    tid = _send_task("app.tasks.publish_latest_post")
    await call.answer("publish отправлен")
    await call.message.edit_text(
        MENU_TEXT + f"\n✅ publish_latest_post отправлен. task_id={tid}",
        reply_markup=_menu_kb(),
    )


# =========================
# BOOTSTRAP
# =========================

async def main():
    if not settings.TG_BOT_TOKEN:
        raise RuntimeError("TG_BOT_TOKEN is not set")

    bot = Bot(token=settings.TG_BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_menu, Command("menu"))

    dp.callback_query.register(cb_menu_root, F.data == "menu:root")
    dp.callback_query.register(cb_menu_language, F.data == "menu:language")
    dp.callback_query.register(cb_language_set, F.data.startswith("lang:set:"))

    dp.callback_query.register(cb_task_parse, F.data == "task:parse")
    dp.callback_query.register(cb_task_generate, F.data == "task:generate")
    dp.callback_query.register(cb_task_publish, F.data == "task:publish")

    logger.info("Bot started.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())