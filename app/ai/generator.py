import json
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.database.models import NewsItem
from app.ai.openai_client import OpenAIClient

logger = logging.getLogger(__name__)


@dataclass
class GenResult:
    """
    Represents a generated result with associated metadata.

    The `GenResult` class acts as a data structure to encapsulate the outcome of a
    text generation process, including the generated text, its status, any related
    error message, and the initial input keys and identifiers for provenance and
    tracking.

    :ivar text: The generated text result from the process.
    :type text: str
    :ivar status: The status of the generation process (e.g., 'success', 'failure').
    :type status: str
    :ivar error: An optional error message detailing an issue, if any occurred during
        the generation process.
    :type error: Optional[str]
    :ivar input_news_ids_json: JSON string representing input news identifiers used in
        the generation process.
    :type input_news_ids_json: str
    :ivar input_key: An identifier key associated with the specific generation request
        for reference.
    :type input_key: str
    """
    text: str
    status: str
    error: Optional[str]
    input_news_ids_json: str
    input_key: str


def _fallback_digest(news: List[NewsItem], note: Optional[str] = None) -> str:
    """
    Constructs a fallback digest of news items in the form of a string.

    This function formats a list of news items into a human-readable text-based digest.
    Each news item's title and URL are presented as list entries in the digest.
    If a `note` is provided, it is included at the beginning of the digest.
    If there are no valid news items and a `note` is provided, the function
    returns the `note` along with a fallback message indicating the absence of data.
    If there are neither news items nor a note, only the fallback message is returned.

    :param news: List of news items to include in the digest. Each item should contain
        a title and optionally a URL.
        - List elements must support the attributes `title` (str) and `url` (str or None).
    :param note: Optional note to be included at the top of the digest.
    :return: A formatted string that summarizes the provided news items, with optional
        additional context from `note`. Returns a fallback message when no data is available.
    """
    lines: List[str] = []

    if note:
        lines.append(note)
        lines.append("")  # пустая строка

    for n in news:
        title = (n.title or "").strip()
        url = (n.url or "").strip()
        if title and url:
            lines.append(f"- {title} ({url})")
        elif title:
            lines.append(f"- {title}")

    if not lines or (note and len(lines) <= 2):
        # если новостей нет (или есть только note + пустая строка)
        if note:
            return f"{note}\n\n⚠️ Нет данных для поста."
        return "⚠️ Нет данных для поста."

    return "\n".join(lines).strip()


def _normalize_lang(language: str) -> str:
    """
    Normalizes the input language code to ensure it is valid and supported. If the input
    language is empty, not supported, or not one of the expected language codes (ru, en,
    es, de), it defaults to "ru".

    :param language: The language code to be normalized. Expected values are "ru",
                     "en", "es", "de".
    :type language: str
    :return: A normalized and valid language code. Defaults to "ru" if the input code
             is invalid or unsupported.
    :rtype: str
    """
    lang = (language or "ru").strip().lower()
    if lang not in ("ru", "en", "es", "de"):
        lang = "ru"
    return lang


def _openai_generate(news: List[NewsItem], language: str) -> str:
    """
    Generates a short Telegram channel post based on a list of news items. The function processes
    the input news items, extracts relevant details like title, summary, raw text, and URL, and
    combines them into a structured list. It then formats the content into a specific language
    defined by the user, adhering to the Telegram channel style guide. The resulting content
    includes a title, 3-6 bullet points, and the URLs at the end, all in the specified language.

    :param news: List of news items to process. The items should contain attributes for title,
                 summary, raw text, and URL.
    :type news: List[NewsItem]
    :param language: Target language for the final output post. Supported languages include
                     English ("en"), Spanish ("es"), German ("de"), and Russian (default).
    :type language: str
    :return: A short formatted Telegram post in the specified language, based on the input news list.
    :rtype: str
    :raises RuntimeError: If an error occurs during text generation by the AI client.
    """
    # компактный контекст
    bullets: List[str] = []
    for n in news:
        title = (n.title or "").strip()
        summary = (n.summary or "").strip()
        url = (n.url or "").strip()
        raw = (n.raw_text or "").strip()

        chunk = title
        if summary:
            chunk += f"\n{summary}"
        elif raw:
            chunk += f"\n{raw[:800]}"
        if url:
            chunk += f"\nURL: {url}"
        bullets.append(chunk)

    lang = _normalize_lang(language)

    system = (
        "Ты редактор Telegram-канала. Сгенерируй один короткий пост по списку новостей. "
        "Формат: заголовок + 3-6 буллетов + ссылка(и) в конце. "
        "Без воды. Без упоминания 'я ИИ'. "
        "ВАЖНО: итоговый текст должен быть на выбранном языке, независимо от языка входных новостей; "
        "если входные новости на другом языке — переведи смысл на выбранный язык."
    )

    if lang == "en":
        system += " Write in English."
    elif lang == "es":
        system += " Escribe en español."
    elif lang == "de":
        system += " Schreibe auf Deutsch."
    else:
        system += " Пиши по-русски."

    user = "Новости:\n\n" + "\n\n---\n\n".join(bullets)

    client = OpenAIClient()
    res = client.chat(system=system, user=user, timeout_s=45)

    if res.error:
        raise RuntimeError(res.error)

    return (res.text or "").strip()


def generate_chain_post(news: List[NewsItem], language: str = "ru") -> Tuple[
    str, str, Optional[str], str, str]:
    """
    Generates a digest of the supplied news items, leveraging an AI-based generation method.
    If the AI-based generation fails, a fallback digest is generated and additional metadata
    is returned, including potential error details.

    :param news: A list of news items containing information to be processed.
    :type news: List[NewsItem]
    :param language: The preferred language for the AI-generated output. Defaults to "ru".
    :type language: str
    :return: A tuple containing the following components:
        - The generated text or fallback text if AI generation fails.
        - A status indicator for the type of generation ("GENERATED").
        - An optional error message (contains the error string if a fallback was triggered; otherwise, None).
        - A JSON string of news item IDs used as input.
        - A legacy short input key derived from the news item IDs.
    :rtype: Tuple[str, str, Optional[str], str, str]
    """
    ids = [n.id for n in news if getattr(n, "id", None)]
    input_news_ids_json = json.dumps(ids, ensure_ascii=False)

    # legacy input_key (короткий ключ)
    input_key = "-".join([i[:6] for i in ids]) if ids else "none"

    lang = _normalize_lang(language)

    try:
        text = _openai_generate(news, language=lang)
        if not text:
            text = _fallback_digest(news,
                                    note="⚠️ OpenAI вернул пустой ответ — публикация на языке оригинала.")
        return str(text), "GENERATED", None, input_news_ids_json, input_key

    except Exception as e:
        err = str(e)
        logger.warning("OpenAI generation failed (%s) -> fallback digest",
                       err)
        # при недоступности ИИ — добавляем сообщение и публикуем на языке оригинала
        text = _fallback_digest(news,
                                note="⚠️ ИИ недоступен, публикация на языке оригинала")
        return str(text), "GENERATED", err, input_news_ids_json, input_key
