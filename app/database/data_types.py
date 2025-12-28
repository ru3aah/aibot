import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, String, DateTime, Enum, Text
from sqlalchemy.sql.annotation import Annotated


class PostStatus(StrEnum):
    NEW = "new"
    DRAFT = "generated"
    PUBLISHED = "published"
    FAILED = "failed"


class SourceType(StrEnum):
    SITE = "site"
    TELEGRAM = "tg"


ID = Annotated[int, Column(String,
                           primary_key=True,
                           index=True,
                           default=uuid.uuid4    )
                ]
URL = Annotated[str, Column(String, nullable=False, index=True)]
TextContent = Annotated[str, Column(Text)]
TimeStamp = Annotated[datetime, Column(DateTime, nullable=False,
                                       index=True, default=datetime.now)
                    ]
STATUS  = Annotated[PostStatus, Column(Enum(PostStatus), nullable=False)]
SOURCE_TYPE = Annotated[SourceType, Column(Enum(SourceType), nullable=False)]
