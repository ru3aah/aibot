from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.database.data_types import SourceType, PostStatus


class SourceBase(BaseModel):
    type: SourceType = Field(..., description='Тип источника')
    name: str = Field(..., description='Название источника')
    url: Optional[str] = Field(None, description='URL-адрес источника')
    enabled: bool = Field(default=True, description='Включен ли источник(для парсинга)')


class SourceCreate(SourceBase):
    pass


class SourceResponse(SourceBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


class SourceUpdate(BaseModel):
    type: Optional[SourceType] = None
    name: Optional[str] = None
    url: Optional[str] = None
    enabled: Optional[bool] = None


class KeywordBase(BaseModel):
    word: str = Field(..., description="Ключевое слово")


class KeywordCreate(KeywordBase):
    pass


class KeywordResponse(KeywordBase):
    id: str

    class Config:
        from_attributes = True



class KeywordUpdate(BaseModel):
    word: Optional[str] = None

class TaskTriggerResponse(BaseModel):
    task_id: str
    task_name: str

class NewsItemResponse(BaseModel):
    id: str
    title: str
    url: Optional[str]
    summary: str
    raw_text: Optional[str]
    source_id: str
    published_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class PostResponse(BaseModel):
    id: str
    news_id: str
    generated_text: Optional[str]
    published_at: Optional[datetime]
    status: PostStatus
    created_at: datetime
    # TODO: news_item: NewsItemResponse

    class Config:
        from_attributes = True