import uuid
from typing import Literal

from pydantic import BaseModel

from lifeos.modules.privacy.erasure import Derived


class DerivedItem(BaseModel):
    type: Literal["fact", "task", "memory"]
    id: uuid.UUID
    # Statement, title: shown to the owner before choosing what to delete.
    text: str


class DerivedOut(BaseModel):
    """What was saved from a journal entry or a conversation (proposals the owner
    validated, memories saved from a message). It stays when its source is deleted,
    unless the owner deletes it too (`with_derived=true`)."""

    items: list[DerivedItem]

    @classmethod
    def of(cls, derived: Derived) -> "DerivedOut":
        return cls(
            items=[DerivedItem(type="fact", id=f.id, text=f.statement) for f in derived.facts]
            + [DerivedItem(type="task", id=t.id, text=t.title) for t in derived.tasks]
            + [DerivedItem(type="memory", id=m.id, text=m.title) for m in derived.memories]
        )
