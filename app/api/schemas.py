from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.database.data_types import SourceType, PostStatus


class SourceBase(BaseModel):
    """
    Represents the base model for a source entity with configurable attributes.

    This class is used to define the core attributes of a source, including its type,
    name, URL, and activation status. It provides a structured way to manage and
    validate source data for applications that work with source entities.

    :ivar type: Represents the type of the source.
    :type type: SourceType
    :ivar name: The name of the source.
    :type name: str
    :ivar url: The optional URL associated with the source.
    :type url: Optional[str]
    :ivar enabled: Indicates whether the source is enabled for parsing.
    :type enabled: bool
    """
    type: SourceType = Field(..., description="Тип источника")
    name: str = Field(..., description="Название источника")
    url: Optional[str] = Field(None, description="URL-адрес источника")
    enabled: bool = Field(default=True,
                          description="Включен ли источник(для парсинга)")


class SourceCreate(SourceBase):
    """
    Represents a model for creating a source extending from the base source class.

    This class is intended to capture necessary attributes and behaviors specific to
    the creation of a source. It inherits all the basic functionalities of the
    SourceBase class.

    :ivar attribute1: Description of attribute1.
    :type attribute1: type
    :ivar attribute2: Description of attribute2.
    :type attribute2: type
    """
    pass


class SourceResponse(SourceBase):
    """
    Represents a response source with assigned identifier and creation timestamp.

    This class extends the functionality of SourceBase by adding specified
    attributes to represent the unique identifier and creation time of the
    source. It is primarily used to encapsulate data in source responses
    with predetermined structure and configuration.

    :ivar id: The unique identifier for the source.
    :type id: str
    :ivar created_at: The timestamp when the source was created.
    :type created_at: datetime
    """
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


class SourceUpdate(BaseModel):
    """
    Represents an update for a source with optional attributes.

    This class is used to define and manage updates to a source,
    allowing one to specify changes in the source attributes such
    as type, name, URL, and whether it is enabled. It inherits from
    BaseModel and utilizes optional type hinting for flexibility.

    :ivar type: The type of the source, represented as an optional
        SourceType value. It specifies the category or nature of
        the source.
    :type type: Optional[SourceType]
    :ivar name: The name of the source, represented as an optional
        string. It defines the identifier or label of the source.
    :type name: Optional[str]
    :ivar url: The URL associated with the source, represented as
        an optional string. It specifies the source's location or
        endpoint.
    :type url: Optional[str]
    :ivar enabled: A boolean flag indicating whether the source is
        enabled or not, represented as an optional boolean value.
    :type enabled: Optional[bool]
    """
    type: Optional[SourceType] = None
    name: Optional[str] = None
    url: Optional[str] = None
    enabled: Optional[bool] = None


class KeywordBase(BaseModel):
    """
    Represents a base model for storing keyword information.

    This class is used as a foundational structure for storing and validating
    a single keyword entry. It leverages Pydantic's BaseModel to ensure type
    validation and additional metadata for the keyword attribute.

    :ivar word: The keyword to be stored. This is mandatory and must be a string.
    :type word: str
    """
    word: str = Field(..., description="Ключевое слово")


class KeywordCreate(KeywordBase):
    """
    Represents a class for creating new keywords by inheriting from the base keyword class.

    This class is specifically designed to extend the functionality of the `KeywordBase`
    class for creating new keyword objects. It inherits all behaviors and attributes from
    its parent class without adding additional methods or properties. This is primarily
    used to ensure type safety and clarity in cases where distinct handling of newly created
    keywords is required.

    """
    pass


class KeywordResponse(KeywordBase):
    """
    Represents a response containing keyword data.

    This class inherits from KeywordBase and is used to structure responses that
    involve keyword-related data. The `id` attribute uniquely identifies the
    keyword response object. This class facilitates seamless mapping of
    attributes from data sources to the object by leveraging configuration.

    :ivar id: Unique identifier for the keyword response object.
    :type id: str
    """
    id: str

    class Config:
        from_attributes = True


class KeywordUpdate(BaseModel):
    """
    Represents an update to a keyword model.

    This class provides an optional keyword field that can be used to update
    the associated keyword in the underlying data model.

    :ivar word: The keyword to update. It is optional and defaults to None.
    :type word: Optional[str]
    """
    word: Optional[str] = None


class TaskTriggerResponse(BaseModel):
    """
    Represents the response of a task trigger.

    This class contains information about a task that has been triggered, including
    its unique identifier and name. It can be utilized to encapsulate responses
    when dealing with task operations.

    :ivar task_id: The unique identifier of the triggered task.
    :type task_id: str
    :ivar task_name: The name of the triggered task.
    :type task_name: str
    """
    task_id: str
    task_name: str


class NewsItemResponse(BaseModel):
    """
    Represents a response model for a news item.

    This class serves as a data structure for encapsulating details about
    a news item. It provides attributes for identifying the news item,
    including its ID, title, and source. Additional optional fields can
    include the URL, a summary, and the raw text of the news item, along
    with timestamps for publication and creation. This model is used
    primarily for transferring data related to news articles.

    :ivar id: Unique identifier for the news item.
    :type id: str
    :ivar title: Title of the news item.
    :type title: str
    :ivar url: Optional URL of the news item.
    :type url: Optional[str]
    :ivar summary: Optional summary of the news item content.
    :type summary: Optional[str]
    :ivar raw_text: Optional raw text content of the news item.
    :type raw_text: Optional[str]
    :ivar source_id: Identifier for the source of the news item.
    :type source_id: str
    :ivar published_at: Optional date and time the news item was published.
    :type published_at: Optional[datetime]
    :ivar created_at: Date and time the news item record was created.
    :type created_at: datetime
    """
    id: str
    title: str
    url: Optional[str]
    summary: Optional[str]
    raw_text: Optional[str]
    source_id: str
    published_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class PostResponse(BaseModel):
    """
    Represents a response for a post entity.

    This class is used to model the structured response for a post, including its
    unique identifier, related news, generated text, publication status, and
    timestamps for creation and publication.

    :ivar id: The unique identifier for the post.
    :type id: str
    :ivar news_id: The identifier for the related news, if any.
    :type news_id: Optional[str]
    :ivar generated_text: The generated text content for the post.
    :type generated_text: Optional[str]
    :ivar published_at: The timestamp indicating when the post was published.
    :type published_at: Optional[datetime]
    :ivar status: The current status of the post.
    :type status: PostStatus
    :ivar created_at: The timestamp indicating when the post was created.
    :type created_at: datetime
    """
    id: str
    news_id: Optional[str]
    generated_text: Optional[str]
    published_at: Optional[datetime]
    status: PostStatus
    created_at: datetime

    class Config:
        from_attributes = True