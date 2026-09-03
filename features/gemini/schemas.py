from pydantic import BaseModel, Field
from dataclasses import dataclass

class GeminiRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str | None = None
    use_search: bool = True

class GeminiSwitchRequest(BaseModel):
    key: str | int

@dataclass
class GeminiKey:
    name: str
    value: str

class GeminiError(Exception):
    pass
