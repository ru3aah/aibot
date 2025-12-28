from typing import List

from sqlalchemy import String, Boolean, ForeignKey
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

from app.database.data_types import (
    ID,
    URL,
    TextContent,
    TimeStamp,
    STATUS,
    SOURCE_TYPE,
)


class Base(DeclarativeBase):
    pass



class Source(Base):
    __tablename__ = "sources"

    id: Mapped[ID]
    type: Mapped[SOURCE_TYPE]

    name: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
    )

    url: Mapped[URL | None]

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[TimeStamp]

    news_items: Mapped[List["NewsItem"]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
    )



class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[ID]

    title: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )

    url: Mapped[URL | None]

    summary: Mapped[TextContent]
    raw_text: Mapped[TextContent | None]

    source_id: Mapped[ID] = mapped_column(
        ForeignKey("sources.id"),
        nullable=False,
        index=True,
    )

    source: Mapped["Source"] = relationship(
        back_populates="news_items",
    )

    published_at: Mapped[TimeStamp]
    created_at: Mapped[TimeStamp]

    posts: Mapped[List["Post"]] = relationship(
        back_populates="news_item",
        cascade="all, delete-orphan",
    )


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[ID]

    news_id: Mapped[ID] = mapped_column(
        ForeignKey("news_items.id"),
        nullable=False,
        index=True,
    )

    news_item: Mapped["NewsItem"] = relationship(
        back_populates="posts",
    )

    generated_text: Mapped[TextContent | None]

    status: Mapped[STATUS]

    published_at: Mapped[TimeStamp | None]
    created_at: Mapped[TimeStamp]



class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[ID]

    word: Mapped[str] = mapped_column(
        String,
        nullable=False,
        index=True,
        unique=True,
    )