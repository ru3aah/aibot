import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated

from sqlalchemy import String, DateTime, Enum, Text
from sqlalchemy.orm import mapped_column


class SourceType(StrEnum):
    SITE = "site"
    TG = "tg"


class PostStatus(StrEnum):
    # ВАЖНО: значения должны совпадать с тем, что лежит в БД (alembic enum)
    NEW = "NEW"
    GENERATED = "GENERATED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"

    SKIPPED_QUOTA = "SKIPPED_QUOTA"
    RETRYABLE = "RETRYABLE"


PK = Annotated[
    str,
    mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    ),
]

FK = Annotated[
    str,
    mapped_column(
        String,
        nullable=False,
    ),
]

TextContentOptional = Annotated[
    str,
    mapped_column(Text, nullable=True),
]

TimeStamp = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    ),
]

TimeStampOptional = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        nullable=True,
    ),
]

STATUS = Annotated[
    PostStatus,
    mapped_column(Enum(PostStatus, native_enum=False), nullable=False),
]