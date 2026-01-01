from __future__ import annotations

import uuid
import json
from datetime import datetime
from typing import Optional, List

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.database.data_types import PostStatus, SourceType


def utcnow() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    word: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    type: Mapped[SourceType] = mapped_column(Enum(SourceType), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    news_items: Mapped[list["NewsItem"]] = relationship("NewsItem", back_populates="source")


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    text100: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    source_id: Mapped[str] = mapped_column(String, ForeignKey("sources.id"), nullable=False)
    source: Mapped["Source"] = relationship("Source", back_populates="news_items")

    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    news_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    generated_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[PostStatus] = mapped_column(Enum(PostStatus), nullable=False)

    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    telegram_message_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    input_news_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    input_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class FilterSettings(Base):
    """
    Одна запись (id="main") хранит настройки админки:
      - язык генерации
      - выбранные активные ключевые слова (до 5)
    """
    __tablename__ = "filter_settings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default="main")

    language: Mapped[str] = mapped_column(String, default="ru", nullable=False)

    # JSON-массив строк, например: ["ai", "cloud", "k8s"]
    active_keywords_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    # ---- helper methods (не обязательны, но удобны) ----
    def get_active_keywords(self) -> List[str]:
        try:
            if not self.active_keywords_json:
                return []
            data = json.loads(self.active_keywords_json)
            if isinstance(data, list):
                out = []
                for x in data:
                    s = (str(x) if x is not None else "").strip()
                    if s:
                        out.append(s.lower())
                return out[:5]
        except Exception:
            return []
        return []

    def set_active_keywords(self, words: List[str]) -> None:
        cleaned = []
        for w in words:
            s = (w or "").strip().lower()
            if s and s not in cleaned:
                cleaned.append(s)
        self.active_keywords_json = json.dumps(cleaned[:5], ensure_ascii=False)