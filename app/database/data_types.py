import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated

from sqlalchemy import String, DateTime, Enum, Text
from sqlalchemy.orm import mapped_column


# =========================
# Enums
# =========================

class PostStatus(StrEnum):
    NEW = "new"
    DRAFT = "generated"
    PUBLISHED = "published"
    FAILED = "failed"


class SourceType(StrEnum):
    SITE = "site"
    TELEGRAM = "tg"


# =========================
# ID columns
# =========================
# Primary key (use ONLY for "id" columns)
PK = Annotated[
    str,
    mapped_column(
        String,
        primary_key=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    ),
]

# Foreign key / generic id (use for *_id columns; NOT a primary key)
FK = Annotated[
    str,
    mapped_column(
        String,
        nullable=False,
        index=True,
    ),
]


# =========================
# URL columns
# =========================
# URL is mandatory (NOT NULL)
URL_REQUIRED = Annotated[
    str,
    mapped_column(
        String,
        nullable=False,
        index=True,
    ),
]

# URL may be NULL
URL_OPTIONAL = Annotated[
    str,
    mapped_column(
        String,
        nullable=True,
        index=True,
    ),
]


# =========================
# Text & timestamps
# =========================

TextContent = Annotated[
    str,
    mapped_column(Text, nullable=False),
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
        index=True,
        default=lambda: datetime.now(timezone.utc),
    ),
]

TimeStampOptional = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    ),
]


# =========================
# Enum columns
# =========================

STATUS = Annotated[
    PostStatus,
    mapped_column(
        Enum(PostStatus, native_enum=False),
        nullable=False,
    ),
]

SOURCE_TYPE = Annotated[
    SourceType,
    mapped_column(
        Enum(SourceType, native_enum=False),
        nullable=False,
    ),
]