"""Writing to the calendar under approval (task 16, ADR 0016).

Creating, moving and cancelling an event are proposals: the owner sees an exact preview
(calendar, before and after in local time with the zone, attendees, whether they are
notified) and approves it. Nothing in the calendar changes before that, whoever asks
(the owner from the agenda, an agent from a planning suggestion).

- The preview is built from the provider's state, read again when proposing: nothing an
  agent or the LLM supplies about the event is trusted. Its etag is kept in the exact
  parameters, and the write is conditional on it (If-Match): a change at the provider
  in between makes the approval fail (`event_changed`) instead of overwriting it; the sync
  invalidates the pending proposals of an event that changed.
- A shared event (other attendees, someone else's event, a calendar the account does not
  own) goes through `calendar.update_shared_event`: approving it requires a recent
  passkey login. The classification is checked again right before writing.
- Every write runs through guardian/operations.py: claimed once, with the approval's
  idempotency key as the id of a new event; a replay never creates a second event.

Attendees' addresses appear in the parameters and preview of the approval only (purged
with it, docs/politique-de-donnees.md), never in calendar_events, logs or audit.
"""

import re
import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import delete, or_, select

from lifeos.core.errors import ProblemError, not_found
from lifeos.core.security import utcnow
from lifeos.modules.calendar.models import CalendarEvent
from lifeos.modules.calendar.service import owner_zone
from lifeos.modules.calendar.sync import event_target, store_event
from lifeos.modules.connectors import tokens
from lifeos.modules.connectors.base import (
    CalendarAdapter,
    ConnectorAuthError,
    ConnectorError,
    ConnectorGoneError,
    EventDraft,
    EventTimes,
    RemoteCollection,
    RemoteEvent,
    SendUpdates,
)
from lifeos.modules.connectors.models import ConnectorAccount, ConnectorGrant, ConnectorSource
from lifeos.modules.connectors.service import provider_for, require_capability
from lifeos.modules.guardian import actions
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.models import Approval
from lifeos.modules.guardian.operations import Attempt, ExternalAction, Outcome

CREATE = "calendar.create_event"
MOVE = "calendar.move_event"
CANCEL = "calendar.cancel_event"
SHARED = "calendar.update_shared_event"

MAX_GUESTS = 50
# A timed event spans 14 days at most, an all-day one 31 days (a typing error otherwise).
MAX_TIMED_SPAN = timedelta(days=14)
MAX_DAYS = 31
_EMAIL = re.compile(r"^[^@\s<>\"',;]{1,64}@[^@\s<>\"',;]{1,190}\.[a-z]{2,24}$")

_DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_MONTHS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _valid_zone(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unknown time zone") from exc
    return value


# --- Exact parameters -----------------------------------------------------------------------


class Times(BaseModel):
    """When: aware instants, or dates for an all-day event (`end_date` exclusive)."""

    model_config = ConfigDict(extra="forbid")

    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    start_date: date | None = None
    end_date: date | None = None
    timezone: str | None = Field(default=None, max_length=64)

    check_zone = field_validator("timezone")(_valid_zone)

    @model_validator(mode="after")
    def _one_form(self) -> "Times":
        timed = self.starts_at is not None or self.ends_at is not None
        dated = self.start_date is not None or self.end_date is not None
        if timed == dated:
            raise ValueError("give either instants or dates")
        if timed:
            if self.starts_at is None or self.ends_at is None:
                raise ValueError("start and end required")
            if not self.starts_at < self.ends_at <= self.starts_at + MAX_TIMED_SPAN:
                raise ValueError("end must follow start, within 14 days")
        else:
            if self.start_date is None or self.end_date is None:
                raise ValueError("start and end dates required")
            if not 0 < (self.end_date - self.start_date).days <= MAX_DAYS:
                raise ValueError("end date must follow start date, within 31 days")
        return self

    def event_times(self) -> EventTimes:
        return EventTimes(
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            start_date=self.start_date,
            end_date=self.end_date,
            timezone=self.timezone if self.starts_at is not None else None,
        )

    @classmethod
    def of(cls, event: RemoteEvent, zone: ZoneInfo) -> "Times":
        """The provider's times, written in the owner's zone (instants unchanged)."""
        return cls(
            starts_at=event.starts_at.astimezone(zone) if event.starts_at else None,
            ends_at=event.ends_at.astimezone(zone) if event.ends_at else None,
            start_date=event.start_date,
            end_date=event.end_date,
            timezone=event.timezone if event.starts_at else None,
        )


def _guests(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        address = value.strip().lower()
        if not _EMAIL.match(address):
            # Never echo the input (docs/conventions-api.md).
            raise ValueError("invalid attendee address")
        if address not in cleaned:
            cleaned.append(address)
    return cleaned


class _Change(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The account it goes through (a disconnection expires the proposal).
    account_id: uuid.UUID
    source_id: uuid.UUID
    calendar_name: str = Field(max_length=300)
    # Zone in which the owner reads the preview.
    display_zone: str = Field(max_length=64)
    # Notify the attendees (sendUpdates=all), only if the owner ticked it.
    notify: bool = False

    check_display_zone = field_validator("display_zone")(_valid_zone)


class CreateEventParams(_Change):
    operation: Literal["create"] = "create"
    summary: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=2000)
    location: str | None = Field(default=None, max_length=300)
    when: Times
    guests: list[str] = Field(default_factory=list, max_length=MAX_GUESTS)
    # The calendar is not the account's own (shared with write access).
    calendar_shared: bool = False

    check_guests = field_validator("guests")(_guests)


class _Existing(_Change):
    # The occurrence: local id, provider id, and its state when proposed.
    event_id: uuid.UUID
    provider_event_id: str = Field(min_length=1, max_length=1024)
    expected_etag: str | None = Field(default=None, max_length=4096)
    summary: str = Field(max_length=300)
    recurring: bool = False
    organizer_self: bool = True
    guests: list[str] = Field(default_factory=list, max_length=500)
    calendar_shared: bool = False
    before: Times


class MoveEventParams(_Existing):
    operation: Literal["move"] = "move"
    after: Times


class CancelEventParams(_Existing):
    operation: Literal["cancel"] = "cancel"


ChangeParams = CreateEventParams | MoveEventParams | CancelEventParams


class SharedEventParams(
    RootModel[
        Annotated[
            CreateEventParams | MoveEventParams | CancelEventParams,
            Field(discriminator="operation"),
        ]
    ]
):
    """Any change of a shared event (reinforced approval)."""


def is_shared(params: ChangeParams) -> bool:
    if params.calendar_shared or params.guests:
        return True
    return isinstance(params, MoveEventParams | CancelEventParams) and not params.organizer_self


# --- Preview --------------------------------------------------------------------------------


def _when_label(times: Times, zone: ZoneInfo) -> str:
    """« mardi 27 octobre 2026, 14:00 - 14:45 (Europe/Paris) » (an en dash), in the owner zone."""

    def day(value: date) -> str:
        return f"{_DAYS[value.weekday()]} {value.day} {_MONTHS[value.month - 1]} {value.year}"

    if times.starts_at is not None and times.ends_at is not None:
        start, end = times.starts_at.astimezone(zone), times.ends_at.astimezone(zone)
        until = f"{end:%H:%M}" if end.date() == start.date() else f"{day(end.date())} {end:%H:%M}"
        return f"{day(start.date())}, {start:%H:%M} \u2013 {until} ({zone.key})"
    assert times.start_date is not None and times.end_date is not None
    last = times.end_date - timedelta(days=1)
    if last == times.start_date:
        return f"{day(times.start_date)}, toute la journée"
    return f"du {day(times.start_date)} au {day(last)}, journées entières"


def preview(params: Any) -> dict[str, Any]:
    change: ChangeParams = params.root if isinstance(params, SharedEventParams) else params
    zone = _zone(change.display_zone)
    shared = is_shared(change)
    out: dict[str, Any] = {
        "operation": change.operation,
        "calendar": change.calendar_name,
        "summary": change.summary,
        "timezone": zone.key,
        "attendees": list(change.guests),
        "notify": change.notify,
        "shared": shared,
    }
    notes: list[str] = []
    if isinstance(change, CreateEventParams):
        out["after"] = _when_label(change.when, zone)
        if change.location:
            out["location"] = change.location
        out["summary_line"] = f"Ajouter « {change.summary} » à {change.calendar_name}"
    else:
        out["before"] = _when_label(change.before, zone)
        out["recurring"] = change.recurring
        if change.recurring:
            notes.append("Cette occurrence seulement : les autres restent inchangées.")
        if not change.organizer_self:
            notes.append(
                "Vous n'organisez pas cet événement : il sera retiré de votre agenda, "
                "l'organisateur garde le sien."
            )
        if isinstance(change, MoveEventParams):
            out["after"] = _when_label(change.after, zone)
            out["summary_line"] = f"Déplacer « {change.summary} »"
        else:
            out["summary_line"] = f"Annuler « {change.summary} »"
    if change.calendar_shared:
        notes.append("Cet agenda appartient à quelqu'un d'autre.")
    if change.guests:
        notes.append(
            "Les participants recevront un e-mail du fournisseur."
            if change.notify
            else "Les participants ne seront pas prévenus."
        )
    out["notes"] = notes
    return out


# --- Provider access ------------------------------------------------------------------------


def _adapter(ctx: Ctx, account: ConnectorAccount) -> CalendarAdapter:
    provider = provider_for(ctx.connectors, account.provider)
    if not hasattr(provider, "create_event"):
        raise ProblemError(409, "connector_capability_missing", "No calendar for this account")
    adapter: CalendarAdapter = provider  # type: ignore[assignment]
    return adapter


async def _token(ctx: Ctx, account: ConnectorAccount) -> str:
    provider = provider_for(ctx.connectors, account.provider)
    return await tokens.access_token(ctx.database, ctx.settings, provider, account.id)


def _provider_problem(exc: ConnectorError) -> ProblemError:
    if isinstance(exc, ConnectorAuthError):
        return ProblemError(
            409, "connector_reauth_required", "This account must be connected again"
        )
    return ProblemError(
        503, "connector_unavailable", "The provider did not answer", extra={"reason": exc.reason}
    )


async def _calendar(
    adapter: CalendarAdapter, token: str, source: ConnectorSource
) -> RemoteCollection:
    calendars = {c.provider_ref: c for c in await adapter.list_calendars(token)}
    calendar = calendars.get(source.provider_ref)
    if calendar is None:
        raise ProblemError(409, "calendar_gone", "This calendar no longer exists")
    if calendar.read_only:
        raise ProblemError(409, "calendar_read_only", "This calendar cannot be modified")
    return calendar


async def _source(ctx: Ctx, source_id: uuid.UUID) -> ConnectorSource:
    source = await ctx.db.scalar(
        select(ConnectorSource).where(
            ConnectorSource.id == source_id,
            ConnectorSource.user_id == ctx.principal.user_id,
            ConnectorSource.space_id.in_(ctx.principal.space_ids),
            ConnectorSource.kind == "calendar",
        )
    )
    if source is None:
        raise not_found("source_not_found", "Calendar not found")
    if not source.enabled:
        raise ProblemError(409, "calendar_gone", "This calendar is no longer followed")
    return source


async def writable_sources(ctx: Ctx, space_id: uuid.UUID) -> list[ConnectorSource]:
    """Followed calendars of the space whose account may write there (consent)."""
    ctx.principal.require_space(space_id)
    now = utcnow()
    return list(
        await ctx.db.scalars(
            select(ConnectorSource)
            .join(
                ConnectorGrant,
                (ConnectorGrant.account_id == ConnectorSource.account_id)
                & (ConnectorGrant.space_id == ConnectorSource.space_id),
            )
            .where(
                ConnectorSource.user_id == ctx.principal.user_id,
                ConnectorSource.space_id == space_id,
                ConnectorSource.kind == "calendar",
                ConnectorSource.enabled.is_(True),
                ConnectorGrant.capability == "calendar.write",
                ConnectorGrant.revoked_at.is_(None),
                or_(ConnectorGrant.expires_at.is_(None), ConnectorGrant.expires_at > now),
            )
            .order_by(ConnectorSource.created_at, ConnectorSource.name)
        )
    )


async def _event_row(ctx: Ctx, event_id: uuid.UUID) -> CalendarEvent:
    row = await ctx.db.scalar(
        select(CalendarEvent).where(
            CalendarEvent.id == event_id,
            CalendarEvent.user_id == ctx.principal.user_id,
            CalendarEvent.space_id.in_(ctx.principal.space_ids),
        )
    )
    if row is None:
        raise not_found("event_not_found", "Event not found")
    return row


def _not_past(times: Times, zone: ZoneInfo) -> None:
    now = utcnow()
    if (times.starts_at is not None and times.starts_at < now) or (
        times.start_date is not None and times.start_date < now.astimezone(zone).date()
    ):
        raise ProblemError(422, "event_in_past", "The new time is already past")


def _action(params: ChangeParams) -> tuple[str, BaseModel]:
    if is_shared(params):
        return SHARED, SharedEventParams(params)
    return {"create": CREATE, "move": MOVE, "cancel": CANCEL}[params.operation], params


# --- Proposals ------------------------------------------------------------------------------


def times_in(
    zone: ZoneInfo,
    *,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> Times:
    """Times from a request: an instant without offset is read in the owner's zone. 422
    `invalid_event_times` otherwise (the input is never echoed)."""

    def aware(moment: datetime | None) -> datetime | None:
        if moment is None:
            return None
        return (moment if moment.tzinfo else moment.replace(tzinfo=zone)).astimezone(zone)

    try:
        return Times(
            starts_at=aware(starts_at),
            ends_at=aware(ends_at),
            start_date=start_date,
            end_date=end_date,
            timezone=zone.key if starts_at is not None else None,
        )
    except ValidationError as exc:
        raise ProblemError(
            422,
            "invalid_event_times",
            "Give a start and an end (instants or dates), the end after the start",
        ) from exc


def guests_in(values: list[str]) -> list[str]:
    try:
        return _guests(values)
    except ValueError as exc:
        raise ProblemError(422, "invalid_guest", "An attendee address is not valid") from exc


async def propose_create(
    ctx: Ctx,
    *,
    space_id: uuid.UUID,
    source_id: uuid.UUID | None,
    summary: str,
    when: Times,
    description: str | None = None,
    location: str | None = None,
    guests: list[str] | None = None,
    notify: bool = False,
    requested_by: str = "owner",
) -> Approval:
    """A new event, as a proposal (nothing is written before the owner approves)."""
    ctx.principal.require_space(space_id)
    if source_id is None:
        writable = await writable_sources(ctx, space_id)
        if not writable:
            raise ProblemError(
                409,
                "calendar_write_unavailable",
                "No followed calendar of this space may be modified",
                "Allow « Modifier l'agenda » for this space in the connected accounts.",
            )
        source = writable[0]
    else:
        source = await _source(ctx, source_id)
        if source.space_id != space_id:
            raise not_found("source_not_found", "Calendar not found")
    zone = owner_zone(ctx)
    _not_past(when, zone)
    account = await require_capability(
        ctx, ctx.connectors, source.account_id, space_id, "calendar.write"
    )
    adapter = _adapter(ctx, account)
    try:
        calendar = await _calendar(adapter, await _token(ctx, account), source)
    except ConnectorError as exc:
        raise _provider_problem(exc) from exc
    if when.starts_at is not None and when.timezone is None:
        when = when.model_copy(update={"timezone": zone.key})
    params = CreateEventParams(
        account_id=account.id,
        source_id=source.id,
        calendar_name=calendar.name,
        display_zone=zone.key,
        notify=notify,
        summary=summary.strip(),
        description=(description or "").strip() or None,
        location=(location or "").strip() or None,
        when=when,
        guests=guests_in(guests or []),
        calendar_shared=not calendar.owned,
    )
    action, payload = _action(params)
    return await actions.request_approval(ctx, action, space_id, payload, requested_by=requested_by)


async def _existing(
    ctx: Ctx, event_id: uuid.UUID
) -> tuple[CalendarEvent, ConnectorSource, ConnectorAccount, RemoteCollection, RemoteEvent]:
    """The occurrence, read again at the provider (never trusted from the caller)."""
    row = await _event_row(ctx, event_id)
    source = await _source(ctx, row.source_id)
    account = await require_capability(
        ctx, ctx.connectors, source.account_id, row.space_id, "calendar.write"
    )
    adapter = _adapter(ctx, account)
    try:
        token = await _token(ctx, account)
        calendar = await _calendar(adapter, token, source)
        remote = await adapter.get_event(token, source.provider_ref, row.provider_event_id)
    except ConnectorGoneError as exc:
        raise ProblemError(409, "calendar_gone", "This calendar no longer exists") from exc
    except ConnectorError as exc:
        raise _provider_problem(exc) from exc
    if remote is None:
        raise ProblemError(409, "event_gone", "This event no longer exists at the provider")
    return row, source, account, calendar, remote


def _existing_fields(
    ctx: Ctx,
    row: CalendarEvent,
    source: ConnectorSource,
    account: ConnectorAccount,
    calendar: RemoteCollection,
    remote: RemoteEvent,
    notify: bool,
) -> dict[str, Any]:
    zone = owner_zone(ctx)
    return {
        "account_id": account.id,
        "source_id": source.id,
        "calendar_name": calendar.name,
        "display_zone": zone.key,
        "notify": notify,
        "event_id": row.id,
        "provider_event_id": remote.provider_id,
        "expected_etag": remote.etag,
        "summary": remote.summary,
        "recurring": remote.series_id is not None,
        "organizer_self": remote.organizer_self,
        "guests": list(remote.guests),
        "calendar_shared": not calendar.owned,
        "before": Times.of(remote, zone),
    }


async def propose_move(
    ctx: Ctx,
    event_id: uuid.UUID,
    *,
    after: Times,
    notify: bool = False,
    requested_by: str = "owner",
) -> Approval:
    row, source, account, calendar, remote = await _existing(ctx, event_id)
    if not remote.organizer_self:
        raise ProblemError(
            409,
            "event_not_organizer",
            "Only the organizer can move this event",
            "Ask the organizer, or cancel it in your calendar.",
        )
    zone = owner_zone(ctx)
    _not_past(after, zone)
    if after.starts_at is not None and after.timezone is None:
        after = after.model_copy(update={"timezone": remote.timezone or zone.key})
    if after.event_times().same_as(remote.times):
        raise ProblemError(422, "event_unchanged", "The event already takes place then")
    params = MoveEventParams(
        **_existing_fields(ctx, row, source, account, calendar, remote, notify), after=after
    )
    action, payload = _action(params)
    return await actions.request_approval(
        ctx, action, row.space_id, payload, requested_by=requested_by, target=event_target(row.id)
    )


async def propose_cancel(
    ctx: Ctx, event_id: uuid.UUID, *, notify: bool = False, requested_by: str = "owner"
) -> Approval:
    row, source, account, calendar, remote = await _existing(ctx, event_id)
    params = CancelEventParams(
        **_existing_fields(ctx, row, source, account, calendar, remote, notify)
    )
    action, payload = _action(params)
    return await actions.request_approval(
        ctx, action, row.space_id, payload, requested_by=requested_by, target=event_target(row.id)
    )


# --- Execution (after approval) -------------------------------------------------------------


def _send(params: ChangeParams) -> SendUpdates:
    return "all" if params.notify and params.guests else "none"


def _changed() -> ProblemError:
    return ProblemError(409, "event_changed", "The event changed since the preview")


class _Write:
    """Perform and reconcile of one calendar action. `shared`: the approved level."""

    def __init__(self, shared: bool) -> None:
        self.shared = shared

    @staticmethod
    def _change(params: Any) -> ChangeParams:
        change: ChangeParams = params.root if isinstance(params, SharedEventParams) else params
        return change

    async def _context(
        self, ctx: Ctx, space_id: uuid.UUID, change: ChangeParams
    ) -> tuple[ConnectorSource, CalendarAdapter, str, RemoteCollection]:
        source = await _source(ctx, change.source_id)
        if source.space_id != space_id:
            raise not_found("source_not_found", "Calendar not found")
        account = await require_capability(
            ctx, ctx.connectors, change.account_id, space_id, "calendar.write"
        )
        if source.account_id != account.id:
            raise not_found("source_not_found", "Calendar not found")
        adapter = _adapter(ctx, account)
        token = await _token(ctx, account)
        calendar = await _calendar(adapter, token, source)
        return source, adapter, token, calendar

    def _check_level(self, shared_now: bool) -> None:
        # Approved as a private event, shared now (or the reverse): not what was approved.
        if shared_now != self.shared:
            raise _changed()

    async def perform(
        self, ctx: Ctx, space_id: uuid.UUID, params: Any, attempt: Attempt
    ) -> Outcome:
        change = self._change(params)
        source, adapter, token, calendar = await self._context(ctx, space_id, change)
        ref = source.provider_ref
        if isinstance(change, CreateEventParams):
            self._check_level(bool(change.guests) or not calendar.owned)
            draft = EventDraft(
                summary=change.summary,
                times=change.when.event_times(),
                description=change.description,
                location=change.location,
                guests=tuple(change.guests),
            )
            remote = await adapter.create_event(
                token, ref, draft, event_id=attempt.idempotency_key, send_updates=_send(change)
            )
            local = await store_event(ctx.db, source, remote)
            return Outcome({"event_id": str(local), "operation": "create"}, remote.provider_id)
        # Moving or cancelling: the event as it is now, against what was approved.
        current = await adapter.get_event(token, ref, change.provider_event_id)
        if current is None:
            if isinstance(change, CancelEventParams):
                return await self._cancelled(ctx, change, already=True)
            raise ProblemError(409, "event_gone", "This event no longer exists at the provider")
        if isinstance(change, MoveEventParams) and current.times.same_as(
            change.after.event_times()
        ):
            # Moved already (an earlier attempt whose outcome was lost).
            return await self._moved(ctx, source, change, current)
        if change.expected_etag is not None and current.etag != change.expected_etag:
            raise _changed()
        # Same rule as the proposal (is_shared), on the event as it is now.
        self._check_level(bool(current.guests) or not current.organizer_self or not calendar.owned)
        try:
            if isinstance(change, MoveEventParams):
                moved = await adapter.move_event(
                    token,
                    ref,
                    change.provider_event_id,
                    change.after.event_times(),
                    etag=change.expected_etag,
                    send_updates=_send(change),
                )
                return await self._moved(ctx, source, change, moved)
            await adapter.cancel_event(
                token,
                ref,
                change.provider_event_id,
                etag=change.expected_etag,
                send_updates=_send(change),
            )
        except ConnectorGoneError as exc:
            raise ProblemError(409, "event_gone", "This event no longer exists") from exc
        return await self._cancelled(ctx, change, already=False)

    async def _moved(
        self, ctx: Ctx, source: ConnectorSource, change: MoveEventParams, remote: RemoteEvent
    ) -> Outcome:
        local = await store_event(ctx.db, source, remote)
        # Other proposals about this event describe its former state.
        await actions.invalidate_targets(
            ctx.db, ctx.principal.user_id, [event_target(change.event_id)], reason="event_changed"
        )
        return Outcome({"event_id": str(local), "operation": "move"}, remote.provider_id)

    async def _cancelled(self, ctx: Ctx, change: _Existing, *, already: bool) -> Outcome:
        await ctx.db.execute(
            delete(CalendarEvent).where(
                CalendarEvent.id == change.event_id,
                CalendarEvent.user_id == ctx.principal.user_id,
            )
        )
        await actions.invalidate_targets(
            ctx.db, ctx.principal.user_id, [event_target(change.event_id)], reason="event_gone"
        )
        result: dict[str, Any] = {"event_id": str(change.event_id), "operation": "cancel"}
        if already:
            result["already_gone"] = True
        return Outcome(result, change.provider_event_id)

    async def reconcile(
        self, ctx: Ctx, space_id: uuid.UUID, params: Any, attempt: Attempt
    ) -> Outcome | None:
        """Did the earlier attempt's write happen? Then its local effects are applied."""
        change = self._change(params)
        source = await _source(ctx, change.source_id)
        account = await ctx.db.get(ConnectorAccount, change.account_id)
        if account is None:
            raise ConnectorAuthError("not_found")
        adapter = _adapter(ctx, account)
        token = await _token(ctx, account)
        ref = source.provider_ref
        if isinstance(change, CreateEventParams):
            remote = await adapter.get_event(token, ref, attempt.idempotency_key)
            if remote is None:
                return None
            local = await store_event(ctx.db, source, remote)
            return Outcome({"event_id": str(local), "operation": "create"}, remote.provider_id)
        current = await adapter.get_event(token, ref, change.provider_event_id)
        if isinstance(change, CancelEventParams):
            return (
                None if current is not None else await self._cancelled(ctx, change, already=False)
            )
        assert isinstance(change, MoveEventParams)
        if current is None or not current.times.same_as(change.after.event_times()):
            return None
        return await self._moved(ctx, source, change, current)

    @staticmethod
    def account(params: Any) -> uuid.UUID | None:
        change: ChangeParams = params.root if isinstance(params, SharedEventParams) else params
        return change.account_id


def _external(shared: bool) -> ExternalAction:
    write = _Write(shared)
    return ExternalAction(
        perform=write.perform, reconcile=write.reconcile, account_id=write.account
    )


actions.register(
    CREATE, CreateEventParams, None, preview, external=_external(False), editable=False
)
actions.register(MOVE, MoveEventParams, None, preview, external=_external(False), editable=False)
actions.register(
    CANCEL, CancelEventParams, None, preview, external=_external(False), editable=False
)
actions.register(SHARED, SharedEventParams, None, preview, external=_external(True), editable=False)
