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
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database.db import get_db_sync
from app.database.models import Keyword, Source

from app.database.db import init_engines_sync, sync_engine
from app.database.models import Base

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# ----------------------------
# Telegram safe helpers
# ----------------------------
async def _safe_answer(call: CallbackQuery, text: Optional[str] = None, *,
                       show_alert: bool = False) -> None:
    """
    Answers a callback query safely by handling potential exceptions.

    This function attempts to send an answer to a given callback query. If a
    text message is provided, it will be sent as part of the answer. An optional
    alert flag can also be used to display the message as an alert. If certain
    Telegram-related exceptions occur, such as when the query is too old,
    response timeout has expired, or the query ID is invalid, these will be
    caught and handled silently. Other exceptions will be raised further.

    :param call: The callback query to respond to.
    :type call: CallbackQuery
    :param text: The text message to include in the response. Defaults to None.
    :type text: Optional[str]
    :param show_alert: Whether to show the response as an alert. Defaults to False.
    :type show_alert: bool
    :return: This function does not return any value.
    :rtype: None
    :raises TelegramBadRequest: If an unexpected Telegram API error occurs.
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


async def _safe_edit_text(call: CallbackQuery, text_msg: str, reply_markup:
InlineKeyboardMarkup) -> None:
    """
    Edits the text of a Telegram message safely within a callback query context.

    This function attempts to edit the text of a message referenced by a callback
    query. If the message is not modified (i.e., no actual changes to the text or
    markup), the function will handle the specific exception silently without raising
    an error. Any other exceptions will be raised further for handling.

    :param call: The callback query object, which contains the message to be edited.
                 Must include a message attribute to edit.
    :type call: CallbackQuery
    :param text_msg: The new text message content to set in the message being edited.
    :type text_msg: str
    :param reply_markup: Inline keyboard markup to set for the edited message, if
                         applicable.
    :type reply_markup: InlineKeyboardMarkup
    :return: This is an asynchronous function and does not return any value.
    :rtype: None
    :raises TelegramBadRequest: If the message editing fails for reasons other
                                than "message is not modified".
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
    """
    Get the current UTC date and time.

    This function returns the current date and time in Coordinated Universal
    Time (UTC) as a timezone-aware `datetime` object. It's commonly used for
    timestamps or when working with timezones to ensure consistency with UTC time.

    :return: The current UTC date and time.
    :rtype: datetime
    """
    return datetime.now(timezone.utc)


def _get_filter_settings_row(session: Session) -> Optional[Dict[str, Any]]:
    """
    Fetches the most recently updated filter settings row from the `filter_settings` table.
    This function retrieves the latest filter settings by ordering the rows based on
    the `updated_at` column in descending order and limiting the selection to one row.

    :param session: A SQLAlchemy session used to execute the database query.
    :type session: Session
    :return: A dictionary containing the filter settings row if available, otherwise None.
    :rtype: Optional[Dict[str, Any]]
    """
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
    Ensures the presence of a default row in the `filter_settings` table. If no row
    exists, inserts a default row with predefined settings. This function provides
    consistency for the database settings by ensuring the table always has a
    record with default values.

    :param session: A SQLAlchemy session object used to interact with the database.
    :type session: Session
    :return: Nothing is returned as the function modifies the database directly.
    :rtype: None
    :raises Exception: If an exception occurs during the operation, the session
        is rolled back to its previous state to prevent uncommitted changes.
    """
    try:
        row = _get_filter_settings_row(session)
        if row:
            return

        # models.py: FilterSettings.id default="main"
        session.execute(
            text(
                """
                INSERT INTO filter_settings (id, language, updated_at, 
                                             active_keywords_json) 
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
    """
    Load the selected language from the filter settings. If no language is
    specified in the filter settings, the default language will be returned.
    The language value is stripped of leading/trailing whitespace and converted
    to lowercase before returning.

    :param session: Database session used to fetch filter settings
    :type session: Session
    :param default: Default language to return if no language is specified
    :type default: str
    :return: Selected language or the default language if none is specified
    :rtype: str
    """
    fs = _get_filter_settings_row(session)
    if not fs:
        return default
    lang = (fs.get("language") or default).strip().lower()
    return lang or default


def _set_selected_language(session: Session, language: str) -> None:
    """
    Updates the selected language in the filter settings stored in the database.
    The language is normalized by converting it to lowercase, stripping leading
    and trailing whitespaces, and defaulting to 'ru' if it is not one of the
    allowed values ('ru', 'en', 'es', 'de'). The most recently updated
    filter_settings record is updated with the new language and current timestamp.

    If an exception occurs during the process, the transaction is rolled back, and
    the exception is propagated.

    :param session: A database session used for executing and committing the
        language update SQL query.
    :type session: Session
    :param language: The language to be set. If `language` is None or invalid,
        defaults to 'ru'.
    :type language: str
    :return: None
    :rtype: None
    """
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
                WHERE id = (SELECT id FROM filter_settings ORDER BY 
                    updated_at DESC LIMIT 1) 
                """
            ),
            {"language": lang, "updated_at": _utcnow()},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise


def _load_active_keywords(session: Session) -> List[str]:
    """
    Load active keywords from the database session.

    This function retrieves a JSON-encoded list of active keywords from the database
    via a session. It parses the JSON data, ensuring it represents a list of strings,
    and returns the unique and cleaned keywords. Duplicate and invalid values are
    filtered out, and all keywords are returned in lowercase.

    :param session: The SQLAlchemy Session used to interact with the database.
    :type session: Session
    :return: A list of unique, cleaned, and lowercased active keywords.
    :rtype: List[str]
    """
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
    """
    Sets the provided keywords as active keywords within a session by filtering,
    normalizing, and ensuring they are distinct. The method updates the database
    record with these keywords in JSON format.

    :param session: The database session used for executing queries.
    :type session: Session
    :param keywords: List of keywords to be processed and set as active.
                     Non-string and duplicate values are filtered out.
    :type keywords: List[str]
    :return: None
    :rtype: None
    """
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
                SET active_keywords_json = :active_keywords_json, updated_at 
                                         = :updated_at 
                WHERE id = (SELECT id FROM filter_settings ORDER BY 
                    updated_at DESC LIMIT 1) 
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
    """
    Creates the main menu keyboard with inline buttons for various menu and task
    actions. The keyboard is structured into three rows, each containing different
    buttons corresponding to specific callback data.

    :return: The main menu inline keyboard containing buttons for user interaction.
    :rtype: InlineKeyboardMarkup
    """
    buttons = [
        [
            InlineKeyboardButton(text="Источники",
                                 callback_data="menu:sources"),
            InlineKeyboardButton(text="Ключевые слова",
                                 callback_data="menu:keywords"),
        ],
        [
            InlineKeyboardButton(text="Язык", callback_data="menu:language"),
        ],
        [
            InlineKeyboardButton(text="Parse", callback_data="task:parse"),
            InlineKeyboardButton(text="Generate",
                                 callback_data="task:generate"),
            InlineKeyboardButton(text="Publish", callback_data="task:publish"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _sources_kb(sources: List[Source]) -> InlineKeyboardMarkup:
    """
    Generate an InlineKeyboardMarkup containing buttons for toggling source states
    and a button for navigating back to the menu.

    The keyboard displays a series of rows where each row contains a button
    representing a source. Each button indicates the name and current state of the
    source (enabled or disabled). An additional row contains a "Back" button for
    navigation purposes.

    :param sources: A list of Source objects, each containing information about its
                    current state and identifier. The `enabled` attribute of a source
                    specifies whether it is active.
    :type sources: List[Source]
    :return: An InlineKeyboardMarkup instance including buttons to toggle sources
             and a navigation "Back" button.
    :rtype: InlineKeyboardMarkup
    """
    rows = []
    for s in sources:
        state = "✅" if s.enabled else "⛔"
        rows.append([InlineKeyboardButton(text=f"{state} {s.name}",
                                          callback_data=f"src:toggle:{s.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад",
                                      callback_data="menu:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _keywords_kb(all_keywords: List[Keyword], active_keywords_lc: List[str]) \
        -> InlineKeyboardMarkup:
    """
    Generates an inline keyboard markup for keyword management.

    The function creates an InlineKeyboardMarkup populated with buttons to manage keywords.
    Each keyword is displayed along with its current status (active or inactive), represented
    with appropriate icons ("✅" for active, "⛔" for inactive). Additional options, like adding
    a new keyword, clearing keywords, and navigating back, are also included.

    :param all_keywords: A list of available keywords to display on the keyboard.
        Each keyword should be an instance of a Keyword object with a `word` attribute and
        a unique `id`.
    :param active_keywords_lc: A list of lowercase strings representing the currently active
        keywords.
    :return: An InlineKeyboardMarkup object containing the inline keyboard for keyword
        management.
    :rtype: InlineKeyboardMarkup
    """
    rows = []
    active_set = set(active_keywords_lc or [])
    for k in all_keywords:
        word = (k.word or "").strip()
        if not word:
            continue
        state = "✅" if word.strip().lower() in active_set else "⛔"
        rows.append([InlineKeyboardButton(text=f"{state} {word}",
                                          callback_data=f"kw:toggle:{k.id}")])
    rows.append(
        [
            InlineKeyboardButton(text="➕ Добавить", callback_data="kw:add"),
            InlineKeyboardButton(text="🧹 Очистить", callback_data="kw:clear"),
        ]
    )
    rows.append([InlineKeyboardButton(text="⬅️ Назад",
                                      callback_data="menu:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _language_kb(current: str) -> InlineKeyboardMarkup:
    """
    Generates an inline keyboard markup for language selection based on the current
    language setting. Each language option includes a checkmark indicating whether
    it is the currently selected language. A button for returning to the previous
    menu is also included.

    :param current: The current language code as a string. If not provided or is
        invalid, defaults to 'ru'.
    :type current: str
    :return: An instance of InlineKeyboardMarkup containing the buttons for
        language selection and a "Back" button.
    :rtype: InlineKeyboardMarkup
    """
    cur = (current or "ru").strip().lower()

    def b(code: str, label: str) -> InlineKeyboardButton:
        mark = "✅" if code == cur else "⛔"
        return InlineKeyboardButton(text=f"{mark} {label}",
                                    callback_data=f"lang:set:{code}")

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
    """
    Sends a menu message with an attached keyboard to the user. This function is asynchronous
    and designed to interact with a user by responding to their input message.

    :param message: The incoming message instance containing details about the user's request.
    :type message: Message

    :return: None, this function does not return a value.
    :rtype: None
    """
    await message.answer("Меню:", reply_markup=_main_menu_kb())


async def _show_sources(target: Message | CallbackQuery) -> None:
    """
    Asynchronously displays the sources available in the system. Depending on the
    type of the target, the function will either edit the provided callback query's
    content or respond to a message. The function retrieves all sources from the
    database, orders them by creation date in ascending order, and constructs a
    message with an inline keyboard for toggling activation states.

    :param target: The target to which the sources should be displayed. It can be
                   either a Message or a CallbackQuery instance. If it is a
                   CallbackQuery, the callback content will be edited. If it is a
                   Message, the function responds directly to the message.
    :type target: Message | CallbackQuery

    :return: This function does not return any value.
    :rtype: None
    """
    with get_db_sync() as session:
        sources = session.query(Source).order_by(Source.created_at.asc()).all()

    text_msg = "Источники (нажми чтобы включить/выключить):"
    kb = _sources_kb(sources)
    if isinstance(target, CallbackQuery):
        await _safe_edit_text(target, text_msg, kb)
    else:
        await target.answer(text_msg, reply_markup=kb)


async def _show_keywords(target: Message | CallbackQuery) -> None:
    """
    Asynchronously displays a list of keywords to the target user or interface.
    This method fetches keywords from the database, constructs an interactive
    message including the ability to activate or deactivate keywords, and sends
    or edits the message for the target.

    :param target: Either a `Message` or a `CallbackQuery` object where the
        keyword list will be displayed and interacted with.
    :type target: Union[Message, CallbackQuery]
    :return: This function has no return value.
    :rtype: None
    """
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
    """
    Handles the display of the language selection interface, determining the current
    language from the database and interacting with the appropriate Telegram API
    endpoint to update or send a message.

    :param target: The message or callback query object representing the Telegram
        interaction context, which is used to send or edit the language selection
        message.
    :type target: Message | CallbackQuery
    :return: None

    """
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
    """
    Handles the /start command sent by the user and initiates the process of showing
    a menu to the user.

    This function is triggered when the /start command is received, and it ensures
    that the user is presented with an appropriate interface or menu in response.

    :param message: The Message object representing the user's /start command.
    :type message: Message
    :return: None
    :rtype: None
    """
    await _show_menu(message)


@dp.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    """
    Handles the `/menu` command issued by a user and orchestrates the display of the menu.

    :param message: Incoming message, which contains user command and context details.
    :type message: Message
    :return: None
    """
    await _show_menu(message)


@dp.callback_query(F.data == "menu:back")
async def cb_back(call: CallbackQuery) -> None:
    """
    Handles the 'menu:back' callback query to navigate back to the main menu.

    :param call: The callback query triggered by the user, containing information about the
                 interaction.
    :type call: CallbackQuery
    :return: None
    """
    await _safe_answer(call)  # ACK first
    await _safe_edit_text(call, "Меню:", _main_menu_kb())


@dp.callback_query(F.data == "menu:sources")
async def cb_sources(call: CallbackQuery) -> None:
    """
    Handles the callback query for "menu:sources".

    :param call: CallbackQuery object triggered by the user interaction.
    :type call: CallbackQuery
    :return: None
    :rtype: None
    """
    await _safe_answer(call)  # ACK first
    await _show_sources(call)


@dp.callback_query(F.data == "menu:keywords")
async def cb_keywords(call: CallbackQuery) -> None:
    """
    Handles the callback query for the "menu:keywords" action. This function is triggered
    when a callback query with the data "menu:keywords" is received. It first acknowledges
    the callback query to ensure timely response handling and then proceeds to display
    the keywords menu to the user.

    :param call: The callback query object containing user interaction data.
    :type call: CallbackQuery
    :return: This function does not return any value.
    :rtype: None
    """
    await _safe_answer(call)  # ACK first
    await _show_keywords(call)


@dp.callback_query(F.data == "menu:language")
async def cb_language(call: CallbackQuery) -> None:
    """
    Handles the callback query when the "menu:language" option is triggered.

    This function is executed when a callback query with data "menu:language"
    is received. It first acknowledges the callback query to ensure proper
    communication with the Telegram server. Following that, it displays the
    language selection menu to the user.

    :param call: Telegram callback query associated with the "menu:language" option.
    :type call: CallbackQuery
    :return: This function does not return any value.
    :rtype: None
    """
    await _safe_answer(call)  # ACK first
    await _show_language(call)


@dp.callback_query(F.data.startswith("lang:set:"))
async def cb_lang_set(call: CallbackQuery) -> None:
    """
    Handles a callback query for setting the user's language preference.

    This function is a callback query handler designed to process language
    selection changes. It interprets the callback data, extracts the selected
    language, and updates the language setting in the database. If successful,
    it displays the updated language to the user. In case of errors, it sends
    an alert with the error details.

    :param call: The callback query object containing the user's input.
    :type call: CallbackQuery
    :return: None
    :raises Exception: If an error occurs when attempting to update the
        language in the database or interacting with the callback query.
    """
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
    """
    Handles the callback query to toggle the enabled state of a specific source.

    This function listens for callback queries with data starting with
    "src:toggle:". It updates the `enabled` state of the source specified by the
    ID found in the callback data. The updated state is stored in the database.
    After toggling the state, the available sources are refreshed and displayed.

    :param call: The callback query object representing an incoming callback
        query from the user.
    :type call: CallbackQuery
    :return: This function does not return anything.
    """
    await _safe_answer(call)  # ACK first

    source_id = (call.data or "").split(":")[-1]
    with get_db_sync() as session:
        s: Optional[Source] = session.query(Source).filter(Source.id ==
                                                           source_id).first()
        if not s:
            await _safe_answer(call, "Источник не найден", show_alert=True)
            return
        s.enabled = not bool(s.enabled)
        session.add(s)
        session.commit()

    await _show_sources(call)


@dp.callback_query(F.data.startswith("kw:toggle:"))
async def cb_kw_toggle(call: CallbackQuery) -> None:
    """
    Handles the callback query for toggling the status of a keyword.

    This function is triggered when a callback query with specific data matches the
    defined filter. It toggles the active status of a keyword in the database by either
    adding or removing it from the active keywords list. If the keyword is not found
    or invalid, an alert is shown to the user.

    :param call: The callback query object containing the query data and other
        information about the incoming callback.
    :return: None
    """
    await _safe_answer(call)  # ACK first

    kw_id = (call.data or "").split(":")[-1]
    with get_db_sync() as session:
        _ensure_filter_settings(session)
        kw: Optional[Keyword] = session.query(Keyword).filter(Keyword.id ==
                                                              kw_id).first()
        if not kw or not (kw.word or "").strip():
            await _safe_answer(call, "Ключевое слово не найдено",
                               show_alert=True)
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
    """
    Handles the callback query to clear the active keywords in the system by resetting
    the keyword filter settings. It processes the interaction in steps: acknowledging
    the callback, performing database operations to modify the settings, and refreshing
    the view for the user with updated data.

    :param call: The callback query object representing the user's callback interaction.
    :type call: CallbackQuery
    :return: None
    """
    await _safe_answer(call)  # ACK first
    with get_db_sync() as session:
        _ensure_filter_settings(session)
        _set_active_keywords(session, [])
    await _show_keywords(call)


@dp.callback_query(F.data == "kw:add")
async def cb_kw_add(call: CallbackQuery) -> None:
    """
    Handles the callback query event when the user chooses to add a new
    keyword. This function provides a user prompt specifying how to
    proceed with adding a new keyword.

    :param call: The callback query instance containing the details
        of the interaction event.
    :type call: CallbackQuery
    :return: None
    """
    await _safe_answer(call, "Пришли новое ключевое слово сообщением",
                       show_alert=True)


@dp.message()
async def on_any_message(message: Message) -> None:
    """
    Handles any incoming message that is not a command, processes it, and interacts
    with a database to manage keywords. Specifically, the function adds the text
    from the message to the database if it doesn't already exist and integrates it
    into active keywords settings.

    :param message: Message object received from the user.
    :type message: Message
    :return: None
    :rtype: None
    """
    text_msg = (message.text or "").strip()
    if not text_msg or text_msg.startswith("/"):
        return

    with get_db_sync() as session:
        existing = session.query(Keyword).filter(Keyword.word.ilike(
            text_msg)).first()
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
    """
    Handles the callback query for parsing tasks.

    This function is triggered when a callback query matches the specified filter.
    It first sends an acknowledgment of receiving the query, then invokes a
    background task to parse news in an asynchronous manner.

    :param call: The callback query object containing the data and context
        of the query.
    :type call: CallbackQuery
    :return: None
    :rtype: NoneType
    """
    await _safe_answer(call)  # ACK first
    from app.tasks import parse_news
    parse_news.delay()


@dp.callback_query(F.data == "task:generate")
async def cb_task_generate(call: CallbackQuery) -> None:
    """
    Handles the callback query for generating a chain post task. This function
    safely acknowledges the incoming callback query before invoking an external
    task to generate the chain post asynchronously.

    :param call: The callback query object containing data and metadata about
                 the user interaction.
    :type call: CallbackQuery

    """
    await _safe_answer(call)  # ACK first
    from app.tasks import generate_chain_post_task
    generate_chain_post_task.delay()


@dp.callback_query(F.data == "task:publish")
async def cb_task_publish(call: CallbackQuery) -> None:
    """
    Handles the callback query for publishing the latest task.

    This function is triggered when the specified callback query data matches
    "task:publish". It ensures the callback query is properly acknowledged before
    proceeding to execute the task. The task is executed in an asynchronous manner.

    :param call: The callback query object containing the context of the user
        interaction within Telegram.
    :type call: CallbackQuery

    :return: This function does not return a value.
    :rtype: None
    """
    await _safe_answer(call)  # ACK first
    from app.tasks import publish_latest_post
    publish_latest_post.delay()


async def main() -> None:
    """
    This is the main entry point for initializing and running the Telegram bot. It performs
    necessary setup operations, such as retrieving the bot token from settings, initializing
    the bot instance, and configuring polling options. This function will also delete any
    existing webhook, fetch the bot's information, and start the polling process.

    The bot token is fetched from the settings under the attributes 'TG_BOT_TOKEN' or
    'BOT_TOKEN'. If the token is not configured, a RuntimeError is raised.

    :raises RuntimeError: If neither 'TG_BOT_TOKEN' nor 'BOT_TOKEN' is found in the settings.

    :rtype: None
    :return: This function does not return any value.
    """
    init_engines_sync()
    Base.metadata.create_all(bind=sync_engine)

    token = getattr(settings, "TG_BOT_TOKEN", None) or getattr(settings,
                                                               "BOT_TOKEN", None)
    if not token:
        raise RuntimeError("TG_BOT_TOKEN is not set")

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    await bot.delete_webhook(drop_pending_updates=True)

    me = await bot.get_me()
    logger.info("Bot started: @%s (id=%s). Polling...",
                me.username, me.id)

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
