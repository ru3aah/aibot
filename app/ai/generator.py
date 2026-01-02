import json
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.database.models import NewsItem
from app.ai.openai_client import OpenAIClient

logger = logging.getLogger(__name__)


@dataclass
class GenResult:
    text: str
    status: str
    error: Optional[str]
    input_news_ids_json: str
    input_key: str


def _fallback_digest(news: List[NewsItem], note: Optional[str] = None) -> str:
    """Фоллбек без OpenAI — всегда генерит дайджест из оригинальных текстов/заголовков."""
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
    lang = (language or "ru").strip().lower()
    if lang not in ("ru", "en", "es", "de"):
        lang = "ru"
    return lang


def _openai_generate(news: List[NewsItem], language: str) -> str:
    """
    Генерация через OpenAI (через наш OpenAIClient без SDK).
    Итоговый текст обязан быть на выбранном языке независимо от языка входных новостей.
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


def generate_chain_post(news: List[NewsItem], language: str = "ru") -> Tuple[str, str, Optional[str], str, str]:
    """
    Возвращает:
      (text, status, error, input_news_ids_json, input_key)

    ВАЖНО: tasks.py должен распаковывать tuple, а не писать его в БД как строку.
    """
    ids = [n.id for n in news if getattr(n, "id", None)]
    input_news_ids_json = json.dumps(ids, ensure_ascii=False)

    # legacy input_key (короткий ключ)
    input_key = "-".join([i[:6] for i in ids]) if ids else "none"

    # нормализуем язык заранее (чтобы "de" точно проходил дальше)
    lang = _normalize_lang(language)

    try:
        text = _openai_generate(news, language=lang)
        if not text:
            text = _fallback_digest(news, note="⚠️ OpenAI вернул пустой ответ — публикация на языке оригинала.")
        return str(text), "GENERATED", None, input_news_ids_json, input_key

    except Exception as e:
        err = str(e)
        logger.warning("OpenAI generation failed (%s) -> fallback digest", err)
        # Требование: при недоступности ИИ — добавляем сообщение и публикуем на языке оригинала
        text = _fallback_digest(news, note="⚠️ ИИ недоступен, публикация на языке оригинала")
        return str(text), "GENERATED", err, input_news_ids_json, input_key
