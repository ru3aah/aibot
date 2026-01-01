from __future__ import annotations

import asyncio
import json
import logging
import uuid
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

from celery_worker import celery_app
from app.config import settings
from app.database.db import get_db_sync
from app.database.models import Source, Keyword
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


def _now_utc_naive() -> datetime:
    # sqlite friendly naive UTC
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _send_task(task_name: str) -> str:
    res = celery_app.send_task(task_name, queue="aibot")
    return str(res.id)


def _norm(s: str) -> str:
    return (s or "").strip()


def _cb(data: str) -> str:
    return data


def _kb(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _menu_kb() -> InlineKeyboardMarkup:
    return _kb(
        [
            [
                InlineKeyboardButton(text="📰 Источники", callback_data=_cb("menu:sources")),
                InlineKeyboardButton(text="🔑 Ключевые слова", callback_data=_cb("menu:keywords")),
            ],
            [
                InlineKeyboardButton(text="🚀 Parse", callback_data=_cb("task:parse")),
                InlineKeyboardButton(text="🧠 Generate", callback_data=_cb("task:generate")),
                InlineKeyboardButton(text="📣 Publish", callback_data=_cb("task:publish")),
            ],
        ]
    )


def _sources_kb(sources: List[Source]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for s in sources:
        mark = "✅" if s.enabled else "⛔️"
        stype = "SITE" if s.type == SourceType.SITE else "TG"
        title = f"{mark} {stype} · {s.name}"
        rows.append([InlineKeyboardButton(text=title, callback_data=_cb(f"src:toggle:{s.id}"))])

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=_cb("menu:root"))])
    return _kb(rows)


def _parse_keywords_json(raw: Optional[str]) -> List[str]:
    """
    Храним в JSON именно слова (уже приведённые к casefold).
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            out: List[str] = []
            for x in data:
                if isinstance(x, str) and x.strip():
                    out.append(x.strip())
            return out
    except Exception:
        pass
    return []


def _dump_keywords_json(words: List[str]) -> str:
    return json.dumps(words, ensure_ascii=False)


def _load_filter_settings_row(session) -> dict:
    """
    Возвращает dict: {id, language, updated_at, active_keywords_json}
    Raw SQL — чтобы не зависеть от ORM/миграций.
    """
    row = session.execute(
        """
        SELECT id, language, updated_at, active_keywords_json
        FROM filter_settings
        ORDER BY updated_at DESC
        LIMIT 1
        """
    ).fetchone()

    if not row:
        fs_id = str(uuid.uuid4())
        session.execute(
            """
            INSERT INTO filter_settings (id, language, created_at, updated_at, active_keywords_json)
            VALUES (:id, :language, :created_at, :updated_at, :active_keywords_json)
            """,
            {
                "id": fs_id,
                "language": "ru",
                "created_at": _now_utc_naive(),
                "updated_at": _now_utc_naive(),
                "active_keywords_json": _dump_keywords_json([]),
            },
        )
        session.commit()
        return {
            "id": fs_id,
            "language": "ru",
            "updated_at": _now_utc_naive(),
            "active_keywords_json": _dump_keywords_json([]),
        }

    return {
        "id": row[0],
        "language": row[1],
        "updated_at": row[2],
        "active_keywords_json": row[3],
    }


def _get_active_keywords_words(session) -> List[str]:
    fs = _load_filter_settings_row(session)
    words = _parse_keywords_json(fs.get("active_keywords_json"))
    # Держим только нормализованный вариант (casefold)
    out: List[str] = []
    for w in words:
        ww = w.strip()
        if ww:
            out.append(ww.casefold())
    return out


def _set_active_keywords_words(session, words_casefold: List[str]) -> None:
    """
    words_casefold: список слов в нижнем регистре (casefold),
    порядок важен (для вытеснения старого).
    """
    fs = _load_filter_settings_row(session)

    # дедуп с сохранением порядка
    seen = set()
    ordered: List[str] = []
    for w in words_casefold:
        ww = (w or "").strip().casefold()
        if not ww or ww in seen:
            continue
        seen.add(ww)
        ordered.append(ww)

    # лимит
    if len(ordered) > MAX_ACTIVE_KEYWORDS:
        ordered = ordered[-MAX_ACTIVE_KEYWORDS:]

    session.execute(
        """
        UPDATE filter_settings
        SET active_keywords_json=:kw, updated_at=:updated_at
        WHERE id=:id
        """,
        {
            "kw": _dump_keywords_json(ordered),
            "updated_at": _now_utc_naive(),
            "id": fs["id"],
        },
    )
    session.commit()


def _keywords_screen(session) -> Tuple[str, InlineKeyboardMarkup]:
    all_kw: List[Keyword] = session.query(Keyword).order_by(Keyword.word.asc()).all()

    active_words = _get_active_keywords_words(session)
    active_set = set(active_words)

    # для красивого отображения возьмём оригинальные слова из БД
    by_cf = {(_norm(k.word).casefold()): _norm(k.word) for k in all_kw if _norm(k.word)}
    if active_words:
        active_line = ", ".join([by_cf.get(w, w) for w in active_words]) or "—"
    else:
        active_line = "—"

    text = (
        "🔑 Ключевые слова\n\n"
        f"Активные (до {MAX_ACTIVE_KEYWORDS}): {active_line}\n\n"
        "Нажимай на слово чтобы включить/выключить.\n"
        "Если включишь 6-е — оно заменит самое старое из активных.\n"
    )

    rows: List[List[InlineKeyboardButton]] = []

    if not all_kw:
        rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data=_cb("kw:add"))])
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=_cb("menu:root"))])
        return text + "\nКлючевых слов пока нет.", _kb(rows)

    for k in all_kw:
        w = _norm(k.word)
        if not w:
            continue
        is_on = w.casefold() in active_set
        mark = "✅" if is_on else "➕"
        rows.append([InlineKeyboardButton(text=f"{mark} {w}", callback_data=_cb(f"kw:toggle:{k.id}"))])

    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data=_cb("kw:add"))])
    rows.append([InlineKeyboardButton(text="🧹 Сбросить активные", callback_data=_cb("kw:clear"))])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=_cb("menu:root"))])

    return text, _kb(rows)


async def _show_menu(msg: Message) -> None:
    await msg.answer(MENU_TEXT, reply_markup=_menu_kb())


async def _edit_menu(call: CallbackQuery) -> None:
    await call.message.edit_text(MENU_TEXT, reply_markup=_menu_kb(), parse_mode=None)


async def cmd_start(msg: Message):
    await _show_menu(msg)


async def cmd_menu(msg: Message):
    await _show_menu(msg)


async def cb_menu_root(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await _edit_menu(call)
    await call.answer()


async def cb_menu_sources(call: CallbackQuery, state: FSMContext):
    await state.clear()
    with get_db_sync() as session:
        rows = session.query(Source).order_by(Source.created_at.desc()).all()

    if not rows:
        await call.message.edit_text(
            "📰 Источники\n\nИсточников нет.",
            reply_markup=_kb([[InlineKeyboardButton(text="⬅️ Назад", callback_data=_cb("menu:root"))]]),
            parse_mode=None,
        )
        await call.answer()
        return

    await call.message.edit_text(
        "📰 Источники\n\nНажимай чтобы включить/выключить источник.",
        reply_markup=_sources_kb(rows),
        parse_mode=None,
    )
    await call.answer()


async def cb_menu_keywords(call: CallbackQuery, state: FSMContext):
    await state.clear()
    with get_db_sync() as session:
        text, kb = _keywords_screen(session)

    await call.message.edit_text(text, reply_markup=kb, parse_mode=None)
    await call.answer()


async def cb_task_parse(call: CallbackQuery):
    tid = _send_task("app.tasks.parse_news")
    await call.answer("parse отправлен")
    await call.message.edit_text(
        MENU_TEXT + f"\n✅ parse_news отправлен. task_id={tid}",
        reply_markup=_menu_kb(),
        parse_mode=None,
    )


async def cb_task_generate(call: CallbackQuery):
    tid = _send_task("app.tasks.generate_chain_post")
    await call.answer("generate отправлен")
    await call.message.edit_text(
        MENU_TEXT + f"\n✅ generate_chain_post отправлен. task_id={tid}",
        reply_markup=_menu_kb(),
        parse_mode=None,
    )


async def cb_task_publish(call: CallbackQuery):
    tid = _send_task("app.tasks.publish_latest_post")
    await call.answer("publish отправлен")
    await call.message.edit_text(
        MENU_TEXT + f"\n✅ publish_latest_post отправлен. task_id={tid}",
        reply_markup=_menu_kb(),
        parse_mode=None,
    )


async def cb_source_toggle(call: CallbackQuery):
    parts = (call.data or "").split(":")
    if len(parts) != 3:
        await call.answer("bad callback")
        return
    source_id = parts[2]

    with get_db_sync() as session:
        src = session.get(Source, source_id)
        if not src:
            await call.answer("не найдено")
            return
        src.enabled = not bool(src.enabled)
        session.commit()
        rows = session.query(Source).order_by(Source.created_at.desc()).all()

    await call.message.edit_reply_markup(reply_markup=_sources_kb(rows))
    await call.answer("ok")


async def cb_kw_toggle(call: CallbackQuery):
    """
    В toggle приходит id Keyword, но активные храним как слова (casefold).
    """
    parts = (call.data or "").split(":")
    if len(parts) != 3:
        await call.answer("bad callback")
        return
    kw_id = parts[2]

    with get_db_sync() as session:
        kw = session.get(Keyword, kw_id)
        if not kw:
            await call.answer("не найдено")
            return

        word_cf = _norm(kw.word).casefold()
        if not word_cf:
            await call.answer("пустое слово")
            return

        active = _get_active_keywords_words(session)

        if word_cf in active:
            active = [x for x in active if x != word_cf]
        else:
            active.append(word_cf)
            if len(active) > MAX_ACTIVE_KEYWORDS:
                active = active[-MAX_ACTIVE_KEYWORDS:]

        _set_active_keywords_words(session, active)
        text, kb = _keywords_screen(session)

    await call.message.edit_text(text, reply_markup=kb, parse_mode=None)
    await call.answer("ok")


async def cb_kw_clear(call: CallbackQuery):
    with get_db_sync() as session:
        _set_active_keywords_words(session, [])
        text, kb = _keywords_screen(session)

    await call.message.edit_text(text, reply_markup=kb, parse_mode=None)
    await call.answer("сброшено")


async def cb_kw_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(KWAdd.waiting_word)
    await call.answer()
    await call.message.edit_text(
        "➕ Добавление ключевого слова\n\n"
        "Отправь слово (минимум 2 символа).\n"
        "Отмена: /menu",
        reply_markup=None,
        parse_mode=None,
    )


async def msg_kw_add_word(msg: Message, state: FSMContext):
    word = _norm(msg.text or "")
    if len(word) < 2:
        await msg.answer("Слишком коротко. Минимум 2 символа. Попробуй ещё раз.")
        return

    with get_db_sync() as session:
        exists = session.query(Keyword).filter(Keyword.word == word).first()
        if exists:
            await state.clear()
            await msg.answer("Такое ключевое слово уже есть. Открываю меню.")
            await _show_menu(msg)
            return

        k = Keyword(word=word)
        session.add(k)
        session.commit()
        session.refresh(k)

    await state.clear()
    await msg.answer(f"✅ Добавил keyword: {word}")
    await _show_menu(msg)


async def main():
    if not settings.TG_BOT_TOKEN:
        raise RuntimeError("TG_BOT_TOKEN is not set")

    bot = Bot(token=settings.TG_BOT_TOKEN)
    dp = Dispatcher()

    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_menu, Command("menu"))

    dp.callback_query.register(cb_menu_root, F.data == "menu:root")
    dp.callback_query.register(cb_menu_sources, F.data == "menu:sources")
    dp.callback_query.register(cb_menu_keywords, F.data == "menu:keywords")

    dp.callback_query.register(cb_task_parse, F.data == "task:parse")
    dp.callback_query.register(cb_task_generate, F.data == "task:generate")
    dp.callback_query.register(cb_task_publish, F.data == "task:publish")

    dp.callback_query.register(cb_source_toggle, F.data.startswith("src:toggle:"))

    dp.callback_query.register(cb_kw_toggle, F.data.startswith("kw:toggle:"))
    dp.callback_query.register(cb_kw_clear, F.data == "kw:clear")
    dp.callback_query.register(cb_kw_add, F.data == "kw:add")

    dp.message.register(msg_kw_add_word, KWAdd.waiting_word)

    logger.info("Bot started.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())