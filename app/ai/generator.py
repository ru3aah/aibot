from __future__ import annotations

import json
import logging
import re
from typing import List, Tuple, Optional

from app.config import settings
from app.database.data_types import PostStatus
from app.database.models import NewsItem

logger = logging.getLogger(__name__)


def _make_input_ids_and_key(news: List[NewsItem]) -> tuple[str, str]:
    ids = [str(n.id) for n in news]
    ids_json = json.dumps(ids, ensure_ascii=False)
    key = "-".join(i[:6] for i in ids)
    return ids_json, key


def _clean_md(text: str) -> str:
    """Лёгкая чистка markdown: убираем лишние пробелы, двойные пустые строки и т.п."""
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _fallback_digest(news: List[NewsItem], note: Optional[str] = None) -> str:
    """Фоллбек без OpenAI — всегда генерит дайджест."""
    lines: List[str] = []
    lines.append("🧾 **IT-дайджест (авто-режим, без LLM)**")
    lines.append("")
    for idx, n in enumerate(news, 1):
        title = (n.title or "").strip() or "Без заголовка"
        url = (n.url or "").strip()
        src_name = ""
        try:
            # relationship может быть не загружен — ок, просто пропустим
            if getattr(n, "source", None) and getattr(n.source, "name", None):
                src_name = str(n.source.name).strip()
        except Exception:
            src_name = ""

        if url:
            item = f"{idx}. [{title}]({url})"
        else:
            item = f"{idx}. {title}"

        if src_name:
            item += f" — _{src_name}_"

        lines.append(item)

    lines.append("")
    if note:
        lines.append(f"⚙️ _Примечание: {note}_")
    else:
        lines.append("⚙️ _Примечание: OpenAI недоступен — использован безопасный фоллбек._")

    return "\n".join(lines)


def _openai_generate(news: List[NewsItem]) -> str:
    """
    Генерация через OpenAI.
    Поддерживаем разные варианты окружения:
    - openai>=1.x: from openai import OpenAI
    - openai<1.x: import openai
    """
    model = getattr(settings, "OPENAI_MODEL", None) or "gpt-4o-mini"
    api_key = getattr(settings, "OPENAI_API_KEY", None)

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    # Собираем контекст
    items = []
    for n in news:
        title = (n.title or "").strip()
        summary = (n.summary or "").strip()
        url = (n.url or "").strip()
        src = ""
        try:
            if getattr(n, "source", None) and getattr(n.source, "name", None):
                src = str(n.source.name).strip()
        except Exception:
            src = ""

        chunk = f"- Title: {title}\n  Source: {src}\n  URL: {url}\n  Summary: {summary}".strip()
        items.append(chunk)

    user_prompt = (
        "Сгенерируй один пост для IT-новостного Telegram-канала на русском.\n"
        "Требования:\n"
        "- 6–10 коротких пунктов\n"
        "- в начале 1 строка-заголовок\n"
        "- стиль живой, но без кликбейта\n"
        "- ссылки оставь как есть\n\n"
        "Новости:\n" + "\n\n".join(items)
    )

    # Пытаемся openai>=1
    try:
        from openai import OpenAI  # type: ignore

        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Ты редактор IT-новостного Telegram-канала."},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
        )
        text = resp.choices[0].message.content or ""
        return _clean_md(text)
    except Exception as e_new:
        # Пытаемся legacy openai<1
        try:
            import openai  # type: ignore

            openai.api_key = api_key
            resp = openai.ChatCompletion.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Ты редактор IT-новостного Telegram-канала."},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.6,
            )
            text = resp["choices"][0]["message"]["content"] or ""
            return _clean_md(text)
        except Exception:
            # если оба варианта не сработали — отдаём исходную ошибку (new sdk),
            # а снаружи уже решим: фоллбек или FAILED
            raise e_new


def generate_chain_post(
    news: List[NewsItem],
) -> Tuple[Optional[str], PostStatus, Optional[str], str, str]:
    """
    Возвращает:
      text, status, error, input_news_ids_json, input_key

    Логика:
    - Всегда сначала пытаемся OpenAI.
    - Если OpenAI недоступен/квота/ключ/429/401/... -> фоллбек и status=GENERATED
    - Если OpenAI упал по другой причине -> тоже фоллбек, но error сохраняем (status=GENERATED),
      чтобы дальше можно было публиковать обходным путём.
    """
    ids_json, key = _make_input_ids_and_key(news)

    try:
        text = _openai_generate(news)
        if not text:
            text = _fallback_digest(news, note="OpenAI вернул пустой ответ — использован фоллбек.")
        return text, PostStatus.GENERATED, None, ids_json, key

    except Exception as e:
        err = str(e)

        quota_like = any(
            s in err.lower()
            for s in [
                "insufficient_quota",
                "exceeded your current quota",
                "quota",
                "api_key",
                "unauthorized",
                "authentication",
                "invalid api key",
                "401",
                "403",
                "429",
            ]
        )

        if quota_like:
            logger.warning("OpenAI unavailable (%s) -> fallback digest", err)
            text = _fallback_digest(news, note="Квота/ключ OpenAI недоступны — использован фоллбек.")
            return text, PostStatus.GENERATED, None, ids_json, key

        # НЕ квотная ошибка -> тоже фоллбек (по твоей договорённости),
        # но ошибку фиксируем в error, чтобы было видно, что AI реально упал.
        logger.exception("OpenAI генерация упала (не квота): %s", err)
        text = _fallback_digest(news, note="OpenAI упал по ошибке — использован фоллбек.")
        return text, PostStatus.GENERATED, err, ids_json, key