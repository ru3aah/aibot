import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database.db import get_db_sync
from app.database.models import Keyword, Source

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# ----------------------------
# Telegram safe helpers
# ----------------------------
async def _safe_answer(call: CallbackQuery, text: Optional[str] = None, *, show_alert: bool = False) -> None:
    """
    CallbackQuery надо подтверждать быстро. Если апдейт пришёл поздно из-за сети,
    Telegram отдаёт 'query is too old...' — это нормальная ситуация, её игнорируем.
    """
    try:
        if text is None:
            await call.answer()
        else:
            await call.answer(text, show_alert=show_alert)
    except TelegramBadRequest as e:
        msg = str(e)
        if (
            "query is too old" in msg
            or "response timeout expired" in msg
            or "query ID is invalid" in msg
        ):
            return
        raise


async def _safe_edit_text(call: CallbackQuery, text_msg: str, reply_markup: InlineKeyboardMarkup) -> None:
    """
    edit_text может падать, если текст/клавиатура не изменились:
    'message is not modified' — игнорируем.
    """
    try:
        if call.message:
            await call.message.edit_text(text_msg, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        msg = str(e)
        if "message is not modified" in msg:
            return
        raise


# ----------------------------
# FilterSettings helpers (raw SQL)
# ----------------------------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_filter_settings_row(session: Session) -> Optional[Dict[str, Any]]:
    row = session.execute(
        text(
            """
            SELECT id, language, updated_at, active_keywords_json
            FROM filter_settings
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
    ).mappings().first()
    return dict(row) if row else None


def _ensure_filter_settings(session: Session) -> None:
    """
    Create initial row if table exists but empty.
    If table doesn't exist yet, we do nothing (bot still works for sources/keywords).
    """
    try:
        row = _get_filter_settings_row(session)
        if row:
            return

        # models.py: FilterSettings.id default="main"
        session.execute(
            text(
                """
                INSERT INTO filter_settings (id, language, updated_at, active_keywords_json)
                VALUES (:id, :language, :updated_at, :active_keywords_json)
                """
            ),
            {
                "id": "main",
                "language": "ru",
                "updated_at": _utcnow(),
                "active_keywords_json": "[]",
            },
        )
        session.commit()
    except Exception:
        session.rollback()


def _load_selected_language(session: Session, default: str = "ru") -> str:
    fs = _get_filter_settings_row(session)
    if not fs:
        return default
    lang = (fs.get("language") or default).strip().lower()
    return lang or default


def _set_selected_language(session: Session, language: str) -> None:
    lang = (language or "ru").strip().lower()
    if lang not in ("ru", "en", "es", "de"):
        lang = "ru"

    try:
        _ensure_filter_settings(session)
        session.execute(
            text(
                """
                UPDATE filter_settings
                SET language = :language, updated_at = :updated_at
                WHERE id = (SELECT id FROM filter_settings ORDER BY updated_at DESC LIMIT 1)
                """
            ),
            {"language": lang, "updated_at": _utcnow()},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise


def _load_active_keywords(session: Session) -> List[str]:
    fs = _get_filter_settings_row(session)
    if not fs:
        return []
    raw = fs.get("active_keywords_json") or ""
    try:
        data = json.loads(raw) if raw else []
        if not isinstance(data, list):
            return []
        out: List[str] = []
        seen = set()
        for x in data:
            if not isinstance(x, str):
                continue
            s = x.strip().lower()
            if not s:
                continue
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out
    except Exception:
        return []


def _set_active_keywords(session: Session, keywords: List[str]) -> None:
    kws: List[str] = []
    seen = set()
    for k in keywords or []:
        if not isinstance(k, str):
            continue
        s = k.strip().lower()
        if not s or s in seen:
            continue
        seen.add(s)
        kws.append(s)

    payload = json.dumps(kws, ensure_ascii=False)

    try:
        _ensure_filter_settings(session)
        session.execute(
            text(
                """
                UPDATE filter_settings
                SET active_keywords_json = :active_keywords_json, updated_at = :updated_at
                WHERE id = (SELECT id FROM filter_settings ORDER BY updated_at DESC LIMIT 1)
                """
            ),
            {"active_keywords_json": payload, "updated_at": _utcnow()},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise


# ----------------------------
# Keyboards
# ----------------------------
def _main_menu_kb() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="Источники", callback_data="menu:sources"),
            InlineKeyboardButton(text="Ключевые слова", callback_data="menu:keywords"),
        ],
        [
            InlineKeyboardButton(text="Язык", callback_data="menu:language"),
        ],
        [
            InlineKeyboardButton(text="Parse", callback_data="task:parse"),
            InlineKeyboardButton(text="Generate", callback_data="task:generate"),
            InlineKeyboardButton(text="Publish", callback_data="task:publish"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _sources_kb(sources: List[Source]) -> InlineKeyboardMarkup:
    rows = []
    for s in sources:
        state = "✅" if s.enabled else "⛔"
        rows.append([InlineKeyboardButton(text=f"{state} {s.name}", callback_data=f"src:toggle:{s.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _keywords_kb(all_keywords: List[Keyword], active_keywords_lc: List[str]) -> InlineKeyboardMarkup:
    rows = []
    active_set = set(active_keywords_lc or [])
    for k in all_keywords:
        word = (k.word or "").strip()
        if not word:
            continue
        state = "✅" if word.strip().lower() in active_set else "⛔"
        rows.append([InlineKeyboardButton(text=f"{state} {word}", callback_data=f"kw:toggle:{k.id}")])
    rows.append(
        [
            InlineKeyboardButton(text="➕ Добавить", callback_data="kw:add"),
            InlineKeyboardButton(text="🧹 Очистить", callback_data="kw:clear"),
        ]
    )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _language_kb(current: str) -> InlineKeyboardMarkup:
    cur = (current or "ru").strip().lower()

    def b(code: str, label: str) -> InlineKeyboardButton:
        mark = "✅" if code == cur else "⛔"
        return InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"lang:set:{code}")

    rows = [
        [b("ru", "Русский"), b("en", "English")],
        [b("es", "Español"), b("de", "Deutsch")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ----------------------------
# UI renderers
# ----------------------------
async def _show_menu(message: Message) -> None:
    await message.answer("Меню:", reply_markup=_main_menu_kb())


async def _show_sources(target: Message | CallbackQuery) -> None:
    with get_db_sync() as session:
        sources = session.query(Source).order_by(Source.created_at.asc()).all()

    text_msg = "Источники (нажми чтобы включить/выключить):"
    kb = _sources_kb(sources)
    if isinstance(target, CallbackQuery):
        await _safe_edit_text(target, text_msg, kb)
    else:
        await target.answer(text_msg, reply_markup=kb)


async def _show_keywords(target: Message | CallbackQuery) -> None:
    with get_db_sync() as session:
        _ensure_filter_settings(session)
        all_keywords = session.query(Keyword).order_by(Keyword.word.asc()).all()
        active = _load_active_keywords(session)

    text_msg = "Ключевые слова (нажми чтобы активировать/деактивировать):"
    kb = _keywords_kb(all_keywords, active)
    if isinstance(target, CallbackQuery):
        await _safe_edit_text(target, text_msg, kb)
    else:
        await target.answer(text_msg, reply_markup=kb)


async def _show_language(target: Message | CallbackQuery) -> None:
    with get_db_sync() as session:
        _ensure_filter_settings(session)
        cur = _load_selected_language(session, default="ru")

    text_msg = f"Выбор языка поста (текущий: {cur}):"
    kb = _language_kb(cur)
    if isinstance(target, CallbackQuery):
        await _safe_edit_text(target, text_msg, kb)
    else:
        await target.answer(text_msg, reply_markup=kb)


# ----------------------------
# Bot setup
# ----------------------------
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await _show_menu(message)


@dp.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    await _show_menu(message)


@dp.callback_query(F.data == "menu:back")
async def cb_back(call: CallbackQuery) -> None:
    await _safe_answer(call)  # ACK first
    await _safe_edit_text(call, "Меню:", _main_menu_kb())


@dp.callback_query(F.data == "menu:sources")
async def cb_sources(call: CallbackQuery) -> None:
    await _safe_answer(call)  # ACK first
    await _show_sources(call)


@dp.callback_query(F.data == "menu:keywords")
async def cb_keywords(call: CallbackQuery) -> None:
    await _safe_answer(call)  # ACK first
    await _show_keywords(call)


@dp.callback_query(F.data == "menu:language")
async def cb_language(call: CallbackQuery) -> None:
    await _safe_answer(call)  # ACK first
    await _show_language(call)


@dp.callback_query(F.data.startswith("lang:set:"))
async def cb_lang_set(call: CallbackQuery) -> None:
    await _safe_answer(call)  # ACK first

    parts = (call.data or "").split(":")
    lang = parts[-1] if parts else "ru"

    try:
        with get_db_sync() as session:
            _set_selected_language(session, lang)
        await _show_language(call)
    except Exception as e:
        await _safe_answer(call, f"Ошибка: {e}", show_alert=True)


@dp.callback_query(F.data.startswith("src:toggle:"))
async def cb_src_toggle(call: CallbackQuery) -> None:
    await _safe_answer(call)  # ACK first

    source_id = (call.data or "").split(":")[-1]
    with get_db_sync() as session:
        s: Optional[Source] = session.query(Source).filter(Source.id == source_id).first()
        if not s:
            await _safe_answer(call, "Источник не найден", show_alert=True)
            return
        s.enabled = not bool(s.enabled)
        session.add(s)
        session.commit()

    await _show_sources(call)


@dp.callback_query(F.data.startswith("kw:toggle:"))
async def cb_kw_toggle(call: CallbackQuery) -> None:
    await _safe_answer(call)  # ACK first

    kw_id = (call.data or "").split(":")[-1]
    with get_db_sync() as session:
        _ensure_filter_settings(session)
        kw: Optional[Keyword] = session.query(Keyword).filter(Keyword.id == kw_id).first()
        if not kw or not (kw.word or "").strip():
            await _safe_answer(call, "Ключевое слово не найдено", show_alert=True)
            return

        active = _load_active_keywords(session)
        word_lc = kw.word.strip().lower()

        if word_lc in set(active):
            active = [x for x in active if x != word_lc]
        else:
            active.append(word_lc)

        _set_active_keywords(session, active)

    await _show_keywords(call)


@dp.callback_query(F.data == "kw:clear")
async def cb_kw_clear(call: CallbackQuery) -> None:
    await _safe_answer(call)  # ACK first
    with get_db_sync() as session:
        _ensure_filter_settings(session)
        _set_active_keywords(session, [])
    await _show_keywords(call)


@dp.callback_query(F.data == "kw:add")
async def cb_kw_add(call: CallbackQuery) -> None:
    await _safe_answer(call, "Пришли новое ключевое слово сообщением", show_alert=True)


@dp.message()
async def on_any_message(message: Message) -> None:
    text_msg = (message.text or "").strip()
    if not text_msg or text_msg.startswith("/"):
        return

    with get_db_sync() as session:
        existing = session.query(Keyword).filter(Keyword.word.ilike(text_msg)).first()
        if not existing:
            kw = Keyword(id=str(uuid.uuid4()), word=text_msg)
            session.add(kw)
            session.commit()

        _ensure_filter_settings(session)
        active = _load_active_keywords(session)
        word_lc = text_msg.strip().lower()
        if word_lc not in set(active):
            active.append(word_lc)
            _set_active_keywords(session, active)

    await message.answer(f"Добавлено: {text_msg}")
    await _show_keywords(message)


@dp.callback_query(F.data == "task:parse")
async def cb_task_parse(call: CallbackQuery) -> None:
    await _safe_answer(call)  # ACK first
    from app.tasks import parse_news
    parse_news.delay()


@dp.callback_query(F.data == "task:generate")
async def cb_task_generate(call: CallbackQuery) -> None:
    await _safe_answer(call)  # ACK first
    from app.tasks import generate_chain_post_task
    generate_chain_post_task.delay()


@dp.callback_query(F.data == "task:publish")
async def cb_task_publish(call: CallbackQuery) -> None:
    await _safe_answer(call)  # ACK first
    from app.tasks import publish_latest_post
    publish_latest_post.delay()


async def main() -> None:
    token = getattr(settings, "TG_BOT_TOKEN", None) or getattr(settings, "BOT_TOKEN", None)
    if not token:
        raise RuntimeError("TG_BOT_TOKEN is not set")

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    await bot.delete_webhook(drop_pending_updates=True)

    me = await bot.get_me()
    logger.info("Bot started: @%s (id=%s). Polling...", me.username, me.id)

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())