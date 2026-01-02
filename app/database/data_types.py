import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated

from sqlalchemy import String, DateTime, Enum, Text
from sqlalchemy.orm import mapped_column


class SourceType(StrEnum):
    """
    Represents types of sources as an enumeration.

    This class is used to define and handle predefined types of sources.
    It is a specialized enumeration that inherits from `StrEnum`, providing
    string-based values for its members.

    :ivar SITE: Represents a source of type "site".
    :type SITE: str
    :ivar TG: Represents a source of type "tg".
    :type TG: str
    """
    SITE = "site"
    TG = "tg"


class PostStatus(StrEnum):
    """
    Represents the status of a post in the system.

    This class is an enumeration for various statuses a post can have during its
    lifecycle. It defines the possible statuses, such as newly created, successfully
    published, or failed. The class helps in standardizing the representation of
    post statuses across the system to promote consistency and ease of use.

    :ivar NEW: Status indicating the post has been newly created and not processed yet.
    :type NEW: str
    :ivar GENERATED: Status indicating the post has been generated.
    :type GENERATED: str
    :ivar PUBLISHED: Status indicating the post has been successfully published.
    :type PUBLISHED: str
    :ivar FAILED: Status indicating the post could not be processed or published
        due to an error.
    :type FAILED: str
    :ivar SKIPPED_QUOTA: Status indicating the post was skipped due to exceeding
        some quota limit.
    :type SKIPPED_QUOTA: str
    :ivar RETRYABLE: Status indicating the post has encountered a retryable error
        and can be tried again later.
    :type RETRYABLE: str
    """
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