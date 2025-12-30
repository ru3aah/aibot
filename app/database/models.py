from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.database.data_types import FK, PK, STATUS, TimeStamp, TimeStampOptional


# =========================
# Base (ЕДИНСТВЕННОЕ МЕСТО)
# =========================
class Base(DeclarativeBase):
    pass


# =========================
# Models
# =========================
class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[PK]
    word: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[PK]
    type: Mapped[str] = mapped_column(String, nullable=False)  # "site" / "tg"
    name: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[TimeStamp]


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[PK]
    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(String, nullable=True)

    # для дедупликации
    text100: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        index=True,
    )

    source_id: Mapped[FK] = mapped_column(ForeignKey("sources.id"), nullable=False)
    source: Mapped["Source"] = relationship(lazy="selectin")

    published_at: Mapped[TimeStampOptional]
    created_at: Mapped[TimeStamp]


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[PK]

    # агрегированный пост; может не ссылаться на одну новость
    news_id: Mapped[str | None] = mapped_column(
        ForeignKey("news_items.id"),
        nullable=True,
        index=True,
    )

    generated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[STATUS]

    published_at: Mapped[TimeStampOptional]
    created_at: Mapped[TimeStamp]

    telegram_message_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    input_news_ids: Mapped[str | None] = mapped_column(String, nullable=True)
    input_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
