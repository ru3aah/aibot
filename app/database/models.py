from __future__ import annotations

from typing import List

from sqlalchemy import String, Boolean, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.database.data_types import (
    PK,
    FK,
    URL_REQUIRED,
    URL_OPTIONAL,
    TextContent,
    TextContentOptional,
    TimeStamp,
    TimeStampOptional,
    STATUS,
    SOURCE_TYPE,
)


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[PK]
    type: Mapped[SOURCE_TYPE]

    name: Mapped[str] = mapped_column(String, nullable=False, index=True)

    url: Mapped[URL_OPTIONAL]

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[TimeStamp]

    news_items: Mapped[List["NewsItem"]] = relationship(
        "NewsItem",
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[PK]

    title: Mapped[str] = mapped_column(String, nullable=False)

    # In many parsers URL can be absent or malformed. If you require it, switch to URL_REQUIRED.
    url: Mapped[URL_OPTIONAL]

    summary: Mapped[TextContent]
    raw_text: Mapped[TextContentOptional]

    source_id: Mapped[FK] = mapped_column(
        ForeignKey("sources.id"),
        nullable=False,
        index=True,
    )

    source: Mapped["Source"] = relationship(
        "Source",
        back_populates="news_items",
        lazy="selectin",
    )

    published_at: Mapped[TimeStamp]
    created_at: Mapped[TimeStamp]

    posts: Mapped[List["Post"]] = relationship(
        "Post",
        back_populates="news_item",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[PK]

    news_id: Mapped[FK] = mapped_column(
        ForeignKey("news_items.id"),
        nullable=False,
        index=True,
    )

    news_item: Mapped["NewsItem"] = relationship(
        "NewsItem",
        back_populates="posts",
        lazy="selectin",
    )

    generated_text: Mapped[TextContentOptional]

    status: Mapped[STATUS]

    # Often not known until actually published:
    published_at: Mapped[TimeStampOptional]

    created_at: Mapped[TimeStamp]

    # Optional (but recommended): enforce one post per news item per status, etc.
    # You can add UniqueConstraint in __table_args__ if needed.


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[PK]

    word: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
        unique=True,
    )
