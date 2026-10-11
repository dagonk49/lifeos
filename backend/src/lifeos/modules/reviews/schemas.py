import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Period = Literal["daily", "weekly", "monthly"]


class ReviewRequest(BaseModel):
    space_id: uuid.UUID
    period: Period = "weekly"
    # Any day inside the wanted period; default: today (period in progress).
    day: date | None = None
    with_narrative: bool = True


class Suggestion(BaseModel):
    action: str = Field(description="Action concrète, réaliste, formulée simplement")
    why: str = Field(description="Le fait observé qui motive cette suggestion")


class ReviewNarrative(BaseModel):
    """What the model may write. Every statement must rest on the facts provided."""

    summary: str = Field(description="Synthèse factuelle et bienveillante en 2 à 4 phrases")
    progress: list[str] = Field(default_factory=list, max_length=6, description="Progrès observés")
    obstacles: list[str] = Field(
        default_factory=list, max_length=6, description="Difficultés observées"
    )
    suggestions: list[Suggestion] = Field(default_factory=list, max_length=5)
    interpretations: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Hypothèses de lecture, formulées comme telles, jamais comme des faits",
    )
    questions: list[str] = Field(
        default_factory=list, max_length=3, description="Questions de réflexion pour la personne"
    )


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    space_id: uuid.UUID
    period: str
    period_start: date
    period_end: date
    facts: dict[str, Any]
    narrative: dict[str, Any] | None
    narrative_status: str
    narrative_error: str | None
    model: str | None
    generated_by: str
    generated_at: datetime
