import html
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def clean_name(value):
    value = value.strip()
    if not value:
        raise ValueError('Must not be blank')
    escaped = html.escape(value, quote=True)
    if len(escaped) > 255:
        raise ValueError('Name is too long after sanitization')
    return escaped


Name = Annotated[str, Field(min_length=1, max_length=255), AfterValidator(clean_name)]
Identifier = UUID


class CleanModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
