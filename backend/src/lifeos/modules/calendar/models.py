"""Calendar occurrences synced from a connected account (task 14, ADR 0012).

Keep in sync with migration 0011 and docs/modele-de-donnees.md.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Owned, Timestamps, UUIDPrimaryKey

EVENT_STATUSES = ("confirmed", "tentative")


class CalendarEvent(UUIDPrimaryKey, Owned, Timestamps, Base):
    """One occurrence, as the provider expanded it, in the space of its source.

    Unique per source and provider id: a resync updates it in place, never duplicates it.
    A timed occurrence is stored as UTC instants (the provider resolved its time zone and
    daylight saving time); an all-day one as dates (`end_date` exclusive), converted to the
    owner's time zone only when shown. Its texts come from third parties: data, never
    instructions.
    """

    __tablename__ = "calendar_events"
    __table_args__ = (
        CheckConstraint("status IN ('confirmed', 'tentative')", name="status"),
        CheckConstraint(
            "(starts_at IS NOT NULL AND ends_at IS NOT NULL AND start_date IS NULL "
            "AND end_date IS NULL) OR (starts_at IS NULL AND ends_at IS NULL "
            "AND start_date IS NOT NULL AND end_date IS NOT NULL)",
            name="when",
        ),
        UniqueConstraint("source_id", "provider_event_id", name="uq_calendar_events_provider"),
        Index("ix_calendar_events_space_starts", "space_id", "starts_at"),
        Index("ix_calendar_events_space_dates", "space_id", "start_date"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connector_sources.id", ondelete="CASCADE"), index=True
    )
    provider_event_id: Mapped[str] = mapped_column(Text)
    # Series the occurrence belongs to (recurring event), if any.
    series_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="confirmed")
    summary: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime | None]
    ends_at: Mapped[datetime | None]
    start_date: Mapped[date | None]
    end_date: Mapped[date | None]
    # Time zone of the event at the provider (IANA), for display only.
    timezone: Mapped[str | None] = mapped_column(Text)
    # Free time (transparent) does not block a slot.
    busy: Mapped[bool] = mapped_column(default=True)
    organizer_self: Mapped[bool] = mapped_column(default=False)
    # The owner's answer when invited (accepted, tentative, declined, needsAction).
    response: Mapped[str | None] = mapped_column(Text)
    attendees: Mapped[int] = mapped_column(default=0)
    etag: Mapped[str | None] = mapped_column(Text)
    provider_updated_at: Mapped[datetime | None]
    # Last sync pass that saw it: after a complete pass, older ones are gone at the provider.
    seen_generation: Mapped[int] = mapped_column(default=0)
