from typing import Optional

from sqlalchemy import Column, String, Boolean
from sqlalchemy.orm import declarative_base

from app.data_types import ID, URL, TextContent, TimeStamp, STATUS, SOURCE_TYPE

Base = declarative_base()


class NewsItem(Base):
    __tablename__ = "news_items"

    id: ID
    title: str = Column(String, nullable=False)
    url: Optional[URL]
    summary: TextContent
    source: str = Column(String, nullable=False)
    published_at: TimeStamp
    raw_text: Optional[TextContent]
    created_at: TimeStamp


class Post(Base):
    __tablename__ = "posts"

    id: ID
    news_id: ID
    generated_text: Optional[TextContent]
    published_at: TimeStamp
    status: STATUS
    created_at: TimeStamp


class Source(Base):
    __tablename__ = "sources"

    id: ID
    type: SOURCE_TYPE
    name: str = Column(String, nullable=False, index=True)
    url: Optional[URL]
    enabled: bool = Column(Boolean, default=True)
    created_at: TimeStamp


class Keyword(Base):
    __tablename__ = "keywords"

    id: ID
    word: str = Column(String, nullable=False, index=True)
