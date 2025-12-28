import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated

from sqlalchemy import String, DateTime, Enum, Text
from sqlalchemy.orm import mapped_column


class PostStatus(StrEnum):
    NEW = "new"
    DRAFT = "generated"
    PUBLISHED = "published"
    FAILED = "failed"


class SourceType(StrEnum):
    SITE = "site"
    TELEGRAM = "tg"


ID = Annotated[
    str,
    mapped_column(
        String,
        primary_key=True,
        index=True,
        default=lambda: str(uuid.uuid4())
    )
]

URL = Annotated[
    str,
    mapped_column(String, nullable=False, index=True)
]

TextContent = Annotated[
    str,
    mapped_column(Text)
]

TimeStamp = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        default=lambda: datetime.now(timezone.utc)
    )
]

STATUS = Annotated[
    PostStatus,
    mapped_column(Enum(PostStatus, native_enum=False), nullable=False)
]

SOURCE_TYPE = Annotated[
    SourceType,
    mapped_column(Enum(SourceType, native_enum=False), nullable=False)
]