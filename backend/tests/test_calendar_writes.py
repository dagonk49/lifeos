"""Calendar writes under approval (task 16, ADR 0016).

Acceptance criteria: no sensitive change runs without the required approval (a reinforced
one, with a recent passkey, for a shared event); a replayed approval or a retried request
never creates a second event. Every test uses the in-memory FakeConnector, except the
Google request tests, which go through httpx.MockTransport (no request reaches Google).
"""

import asyncio
import base64
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lifeos.config import Settings
from lifeos.core.errors import ProblemError
from lifeos.core.security import utcnow
from lifeos.db.session import Database
from lifeos.main import create_app
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.calendar import actions as calendar_actions
from lifeos.modules.calendar import sync
from lifeos.modules.calendar.models import CalendarEvent
from lifeos.modules.connectors.base import (
    ConnectorConflictError,
    ConnectorUnavailableError,
    EventDraft,
    EventTimes,
    OutcomeUnknownError,
    RemoteEvent,
)
from lifeos.modules.connectors.fake import FakeConnector
from lifeos.modules.connectors.google import GoogleConnector
from lifeos.modules.guardian import actions, operations
from lifeos.modules.guardian.context import Ctx
from lifeos.modules.guardian.models import Approval, ExternalOperation
from lifeos.modules.guardian.policy import evaluate
from lifeos.modules.guardian.principal import Principal
from lifeos.modules.identity.models import User, UserSession
from lifeos.modules.llm.fake import FakeProvider

from .conftest import Owner, login, make_ctx

CONNECTORS_KEY = base64.b64encode(b"k" * 32).decode()
PARIS = ZoneInfo("Europe/Paris")
DASH = "\u2013"  # en dash, as in the previews


def soon(days: int, hour: int, minute: int = 0) -> datetime:
    day = utcnow().astimezone(PARIS).date() + timedelta(days=days)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=PARIS)


@pytest.fixture
def fake_connector() -> FakeConnector:
    return FakeConnector()


@pytest.fixture
def connector_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"connectors_provider": "fake", "connectors_key": SecretStr(CONNECTORS_KEY)}
    )


@pytest.fixture
async def app(
    connector_settings: Settings, fake_llm: FakeProvider, fake_connector: FakeConnector
) -> AsyncIterator[FastAPI]:
    application = create_app(connector_settings)
    application.state.llm = fake_llm
    application.state.connectors = {"fake": fake_connector}
    async with application.router.lifespan_context(application):
        yield application


async def connect(client: AsyncClient, space_id: str, capabilities: list[str]) -> str:
    response = await client.post(
        "/v1/connectors/oauth/start",
        json={"provider": "fake", "space_id": space_id, "capabilities": capabilities},
    )
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    callback = await client.post(
        "/v1/connectors/oauth/callback",
        json={"state": query["state"][0], "code": query["code"][0]},
    )
    assert callback.status_code == 200, callback.text
    account_id: str = callback.json()["account"]["id"]
    return account_id


def event(provider_id: str, start: datetime, minutes: int = 60, **extra: Any) -> RemoteEvent:
    values: dict[str, Any] = {
        "provider_id": provider_id,
        "status": "confirmed",
        "summary": "Dentiste",
        "starts_at": start,
        "ends_at": start + timedelta(minutes=minutes),
        "timezone": "Europe/Paris",
        "organizer_self": True,
        "etag": f'"{provider_id}-1"',
    }
    values.update(extra)
    return RemoteEvent(**values)


@pytest.fixture
async def writable(
    client: AsyncClient,
    spaces: dict[str, str],
    database: Database,
    connector_settings: Settings,
    fake_connector: FakeConnector,
) -> dict[str, str]:
    """A calendar followed in the personal space, with consent to modify it, holding a
    private event (`private`) and a shared one (`shared`), synced."""
    space = spaces["personal"]
    account = await connect(client, space, ["calendar.read", "calendar.write"])
    response = await client.post(
        "/v1/connectors/sources",
        json={"account_id": account, "provider_ref": "primary", "space_id": space},
    )
    assert response.status_code == 201, response.text
    source = response.json()["id"]
    fake_connector.add_event("primary", event("private", soon(2, 14)))
    fake_connector.add_event(
        "primary",
        event(
            "shared",
            soon(3, 10),
            summary="Point d'équipe",
            attendees=3,
            guests=("alex@example.org", "sam@example.org"),
        ),
    )
    result = await sync.sync_source(
        database, connector_settings, {"fake": fake_connector}, uuid.UUID(source)
    )
    assert result.outcome == "synced", result
    async with database.sessionmaker() as session:
        rows = dict(
            (
                await session.execute(
                    select(CalendarEvent.provider_event_id, CalendarEvent.id).where(
                        CalendarEvent.source_id == uuid.UUID(source)
                    )
                )
            ).all()
        )
    return {
        "space": space,
        "account": account,
        "source": source,
        "private": str(rows["private"]),
        "shared": str(rows["shared"]),
    }


def when(start: datetime, minutes: int = 60) -> dict[str, str]:
    return {
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(minutes=minutes)).isoformat(),
    }


async def approve(client: AsyncClient, approval_id: str) -> httpx.Response:
    return await client.post(f"/v1/approvals/{approval_id}/approve", json={})


async def operation(db: AsyncSession, approval_id: str) -> ExternalOperation | None:
    db.expire_all()
    return await db.scalar(
        select(ExternalOperation).where(ExternalOperation.approval_id == uuid.UUID(approval_id))
    )


async def local_events(db: AsyncSession, source: str) -> dict[str, CalendarEvent]:
    db.expire_all()
    rows = await db.scalars(
        select(CalendarEvent).where(CalendarEvent.source_id == uuid.UUID(source))
    )
    return {r.provider_event_id: r for r in rows}


async def owner_ctx(
    database: Database,
    db: AsyncSession,
    settings: Settings,
    owner: Owner,
    llm: FakeProvider,
    connector: FakeConnector,
) -> Ctx:
    user = await db.get(User, owner.user.id)
    assert user is not None
    ctx = await make_ctx(database, db, settings, user, llm)
    ctx.connectors = {"fake": connector}
    return ctx


# --- Nothing without an approval ------------------------------------------------------------


async def test_no_calendar_write_without_an_approval(
    database: Database,
    db: AsyncSession,
    connector_settings: Settings,
    logged_in: Owner,
    fake_llm: FakeProvider,
    fake_connector: FakeConnector,
    writable: dict[str, str],
) -> None:
    ctx = await owner_ctx(database, db, connector_settings, logged_in, fake_llm, fake_connector)
    approval = await calendar_actions.propose_move(
        ctx, uuid.UUID(writable["private"]), after=calendar_actions.Times(**when(soon(2, 16)))
    )
    await db.commit()
    # Executing the very parameters of the proposal, without its approval: refused.
    for name in (calendar_actions.MOVE, calendar_actions.CREATE, calendar_actions.CANCEL):
        assert evaluate(ctx.principal, name, uuid.UUID(writable["space"])).code == (
            "approval_required"
        )
    with pytest.raises(ProblemError) as exc:
        await actions.execute(ctx, approval.action, approval.space_id, approval.params)
    assert exc.value.code == "approval_required"
    # A pending approval is no approval either.
    with pytest.raises(ProblemError) as exc:
        await actions.execute(
            ctx, approval.action, approval.space_id, approval.params, approval=approval
        )
    assert exc.value.code == "approval_required"
    await db.rollback()
    assert fake_connector.write_calls == 0
    denied = await db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.action == calendar_actions.MOVE, AuditEvent.outcome == "denied")
    )
    assert denied == 2


async def test_rejected_or_expired_proposals_never_reach_the_provider(
    client: AsyncClient, db: AsyncSession, fake_connector: FakeConnector, writable: dict[str, str]
) -> None:
    first = await client.post(
        f"/v1/calendar/events/{writable['private']}/cancel", json={"notify": False}
    )
    assert first.status_code == 201, first.text
    rejected = await client.post(f"/v1/approvals/{first.json()['id']}/reject")
    assert rejected.json()["status"] == "rejected"
    second = await client.post(f"/v1/calendar/events/{writable['private']}/cancel", json={})
    await db.execute(
        update(Approval)
        .where(Approval.id == uuid.UUID(second.json()["id"]))
        .values(expires_at=utcnow() - timedelta(seconds=1))
    )
    await db.commit()
    expired = await approve(client, second.json()["id"])
    assert expired.status_code == 409
    assert expired.json()["approval_status"] == "expired"
    assert fake_connector.write_calls == 0
    assert "private" in fake_connector.events["primary"]


async def test_an_agent_proposes_but_never_approves(
    database: Database,
    db: AsyncSession,
    connector_settings: Settings,
    logged_in: Owner,
    fake_llm: FakeProvider,
    fake_connector: FakeConnector,
    writable: dict[str, str],
) -> None:
    space = uuid.UUID(writable["space"])
    ctx = await owner_ctx(database, db, connector_settings, logged_in, fake_llm, fake_connector)
    planning = Principal(
        kind="agent",
        user_id=logged_in.user.id,
        actor_id="planning",
        agent="planning",
        space_ids=frozenset({space}),
        grants=frozenset({(space, "calendar", "write")}),
    )
    agent_ctx = Ctx(**{**ctx.__dict__, "principal": planning})
    approval = await calendar_actions.propose_create(
        agent_ctx,
        space_id=space,
        source_id=None,
        summary="Bloc de travail",
        when=calendar_actions.Times(**when(soon(4, 9), 90)),
        requested_by="agent:planning",
    )
    assert approval.status == "pending" and approval.requested_by == "agent:planning"
    with pytest.raises(ProblemError) as exc:
        await actions.approve(agent_ctx, approval.id)
    assert exc.value.code == "action_denied"
    # Without its permission on the calendar, an agent cannot even propose.
    reader = Principal(
        kind="agent",
        user_id=logged_in.user.id,
        actor_id="planning",
        agent="planning",
        space_ids=frozenset({space}),
        grants=frozenset({(space, "calendar", "read")}),
    )
    with pytest.raises(ProblemError) as exc:
        await calendar_actions.propose_create(
            Ctx(**{**ctx.__dict__, "principal": reader}),
            space_id=space,
            source_id=None,
            summary="Bloc",
            when=calendar_actions.Times(**when(soon(4, 11))),
        )
    assert exc.value.code == "agent_permission_required"
    assert fake_connector.write_calls == 0


# --- Exact preview, approval, one single event ----------------------------------------------


async def test_creating_an_event_needs_one_approval_and_creates_one_event(
    client: AsyncClient,
    db: AsyncSession,
    fake_connector: FakeConnector,
    writable: dict[str, str],
) -> None:
    body = {"space_id": writable["space"], "summary": "Bloc de travail", **when(soon(5, 9), 90)}
    key = {"Idempotency-Key": "create-1"}
    first = await client.post("/v1/calendar/events", json=body, headers=key)
    assert first.status_code == 201, first.text
    proposal = first.json()
    assert proposal["action"] == "calendar.create_event" and proposal["level"] == "approval"
    preview = proposal["preview"]
    assert preview["calendar"] == "Agenda de démonstration"
    assert preview["after"].endswith(f", 09:00 {DASH} 10:30 (Europe/Paris)")
    assert preview["attendees"] == [] and preview["shared"] is False
    # A retried request (same key) and the same proposal again: one approval.
    again = await client.post("/v1/calendar/events", json=body, headers=key)
    assert again.status_code == 201 and again.json()["id"] == proposal["id"]
    same = await client.post("/v1/calendar/events", json=body)
    assert same.json()["id"] == proposal["id"]
    assert fake_connector.write_calls == 0

    done = await approve(client, proposal["id"])
    assert done.status_code == 200 and done.json()["status"] == "executed", done.text
    key_id = operations.idempotency_key(uuid.UUID(proposal["id"]))
    assert fake_connector.writes == [("create_event", "primary", key_id, "none")]
    rows = await local_events(db, writable["source"])
    assert rows[key_id].summary == "Bloc de travail"
    assert done.json()["result"]["event_id"] == str(rows[key_id].id)
    op = await operation(db, proposal["id"])
    assert op is not None and op.state == "succeeded" and op.provider_ref == key_id

    # Replayed approval: refused, still one event at the provider.
    replay = await approve(client, proposal["id"])
    assert replay.status_code == 409 and replay.json()["code"] == "approval_not_pending"
    assert len(fake_connector.writes) == 1
    assert len(fake_connector.events["primary"]) == 3


async def test_a_crash_after_the_write_never_creates_a_second_event(
    client: AsyncClient,
    db: AsyncSession,
    fake_connector: FakeConnector,
    writable: dict[str, str],
) -> None:
    body = {"space_id": writable["space"], "summary": "Sport", **when(soon(6, 18))}
    proposal = (await client.post("/v1/calendar/events", json=body)).json()
    # The provider creates the event, then the process dies before recording it.
    fake_connector.fail_after_next("create_event", RuntimeError("worker died"))
    with pytest.raises(RuntimeError):
        await approve(client, proposal["id"])
    db.expire_all()
    stored = await db.get(Approval, uuid.UUID(proposal["id"]))
    assert stored is not None and stored.status == "pending"  # the request rolled back
    op = await operation(db, proposal["id"])
    assert op is not None and op.state == "claimed"  # the claim did not

    # The owner approves again: the claim exists, so the provider is asked first.
    retried = await approve(client, proposal["id"])
    assert retried.json()["status"] == "executed", retried.text
    assert fake_connector.write_calls == 1
    assert len(fake_connector.writes) == 1
    op = await operation(db, proposal["id"])
    assert op is not None and op.state == "succeeded" and op.attempts == 1
    assert operations.idempotency_key(uuid.UUID(proposal["id"])) in await local_events(
        db, writable["source"]
    )


async def test_concurrent_approvals_execute_once(
    client: AsyncClient, fake_connector: FakeConnector, writable: dict[str, str]
) -> None:
    body = {"space_id": writable["space"], "summary": "Lecture", **when(soon(7, 20))}
    proposal = (await client.post("/v1/calendar/events", json=body)).json()
    first, second = await asyncio.gather(
        approve(client, proposal["id"]), approve(client, proposal["id"])
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert len(fake_connector.writes) == 1


async def test_an_unknown_outcome_is_reconciled_never_resent(
    client: AsyncClient,
    database: Database,
    db: AsyncSession,
    connector_settings: Settings,
    fake_connector: FakeConnector,
    writable: dict[str, str],
) -> None:
    target = soon(2, 17)
    proposal = (
        await client.post(f"/v1/calendar/events/{writable['private']}/move", json=when(target))
    ).json()
    assert proposal["action"] == "calendar.move_event"
    assert proposal["preview"]["before"].endswith(f"14:00 {DASH} 15:00 (Europe/Paris)")
    assert proposal["preview"]["after"].endswith(f"17:00 {DASH} 18:00 (Europe/Paris)")
    # Sent, then the connection broke: nobody knows whether it was applied (it was).
    fake_connector.fail_after_next("move_event", OutcomeUnknownError("timeout"))
    failed = (await approve(client, proposal["id"])).json()
    assert failed["status"] == "failed" and failed["error"] == "outcome_unknown"
    op = await operation(db, proposal["id"])
    assert op is not None and op.state == "unknown"

    counts = await operations.reconcile_due(database, connector_settings, {"fake": fake_connector})
    assert counts == {"succeeded": 1}
    assert len(fake_connector.writes) == 1  # checked, never sent again
    db.expire_all()
    stored = await db.get(Approval, uuid.UUID(proposal["id"]))
    assert stored is not None and stored.status == "executed" and stored.error is None
    rows = await local_events(db, writable["source"])
    assert rows["private"].starts_at == target.astimezone(UTC)


async def test_a_stale_claim_not_applied_may_be_approved_again(
    client: AsyncClient,
    database: Database,
    db: AsyncSession,
    connector_settings: Settings,
    fake_connector: FakeConnector,
    writable: dict[str, str],
) -> None:
    proposal = (
        await client.post(f"/v1/calendar/events/{writable['private']}/cancel", json={})
    ).json()
    # The request died before reaching the provider: the claim stays, nothing was sent.
    fake_connector.fail_next("cancel_event", RuntimeError("worker died"))
    with pytest.raises(RuntimeError):
        await approve(client, proposal["id"])
    await db.execute(
        update(ExternalOperation)
        .where(ExternalOperation.approval_id == uuid.UUID(proposal["id"]))
        .values(claimed_at=utcnow() - timedelta(minutes=10))
    )
    await db.commit()
    counts = await operations.reconcile_due(database, connector_settings, {"fake": fake_connector})
    assert counts == {"not_applied": 1}
    assert "private" in fake_connector.events["primary"]
    done = (await approve(client, proposal["id"])).json()
    assert done["status"] == "executed", done
    assert fake_connector.writes == [("cancel_event", "primary", "private", "none")]
    assert "private" not in await local_events(db, writable["source"])


async def test_a_cancel_replayed_after_a_crash_counts_once(
    client: AsyncClient, db: AsyncSession, fake_connector: FakeConnector, writable: dict[str, str]
) -> None:
    proposal = (
        await client.post(f"/v1/calendar/events/{writable['private']}/cancel", json={})
    ).json()
    fake_connector.fail_after_next("cancel_event", RuntimeError("worker died"))
    with pytest.raises(RuntimeError):
        await approve(client, proposal["id"])
    # Deleted at the provider (it would answer 410 Gone): the retry is a success.
    retried = (await approve(client, proposal["id"])).json()
    assert retried["status"] == "executed"
    assert len(fake_connector.writes) == 1
    assert "private" not in await local_events(db, writable["source"])


# --- Shared events: reinforced approval -----------------------------------------------------


async def test_a_shared_event_needs_a_recent_passkey(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: Owner,
    fake_connector: FakeConnector,
    writable: dict[str, str],
) -> None:
    response = await client.post(
        f"/v1/calendar/events/{writable['shared']}/move",
        json={**when(soon(3, 11)), "notify": True},
    )
    assert response.status_code == 201, response.text
    proposal = response.json()
    assert proposal["action"] == "calendar.update_shared_event"
    assert proposal["level"] == "reinforced"
    assert proposal["preview"]["shared"] is True
    assert proposal["preview"]["attendees"] == ["alex@example.org", "sam@example.org"]
    assert proposal["preview"]["notify"] is True

    # The login is no longer recent: refused, audited, nothing written.
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == logged_in.user.id)
        .values(authenticated_at=utcnow() - timedelta(hours=1))
    )
    await db.commit()
    refused = await approve(client, proposal["id"])
    assert refused.status_code == 403 and refused.json()["code"] == "reauthentication_required"
    assert fake_connector.write_calls == 0
    assert (
        await db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "approval.approve", AuditEvent.outcome == "denied")
        )
        == 1
    )

    # After a passkey login: executed, attendees notified as chosen.
    await login(client, logged_in.authenticator)
    done = await approve(client, proposal["id"])
    assert done.json()["status"] == "executed", done.text
    assert fake_connector.writes == [("move_event", "primary", "shared", "all")]


async def test_inviting_attendees_or_writing_in_someone_elses_calendar_is_shared(
    client: AsyncClient, writable: dict[str, str]
) -> None:
    body = {
        "space_id": writable["space"],
        "summary": "Déjeuner",
        "guests": ["Alex@Example.org", "alex@example.org"],
        **when(soon(8, 12)),
    }
    proposal = (await client.post("/v1/calendar/events", json=body)).json()
    assert proposal["action"] == "calendar.update_shared_event"
    assert proposal["level"] == "reinforced"
    assert proposal["params"]["guests"] == ["alex@example.org"]
    assert "ne seront pas prévenus" in " ".join(proposal["preview"]["notes"])
    invalid = await client.post("/v1/calendar/events", json={**body, "guests": ["pas une adresse"]})
    assert invalid.status_code == 422 and invalid.json()["code"] == "invalid_guest"
    assert "pas une adresse" not in invalid.text


def test_policy_requires_a_reinforced_approval_for_shared_events() -> None:
    space = uuid.uuid4()

    class _Approved:
        def __init__(self, level: str) -> None:
            self.action, self.space_id, self.status = calendar_actions.SHARED, space, "approved"
            self.level = level
            self.user_id = user

    user = uuid.uuid4()
    owner = Principal(kind="user", user_id=user, actor_id="o", space_ids=frozenset({space}))
    recent = Principal(
        kind="user", user_id=user, actor_id="o", space_ids=frozenset({space}), recent_auth=True
    )
    shared = calendar_actions.SHARED
    assert evaluate(recent, shared, space).code == "approval_required"
    plain: Any = _Approved("approval")
    reinforced: Any = _Approved("reinforced")
    assert evaluate(recent, shared, space, plain).code == "reauthentication_required"
    assert evaluate(owner, shared, space, reinforced).code == "reauthentication_required"
    assert evaluate(recent, shared, space, reinforced).allowed


# --- What was approved, and nothing else ----------------------------------------------------


async def test_tampered_parameters_are_refused(
    client: AsyncClient, db: AsyncSession, fake_connector: FakeConnector, writable: dict[str, str]
) -> None:
    proposal = (
        await client.post(f"/v1/calendar/events/{writable['private']}/move", json=when(soon(2, 16)))
    ).json()
    params = dict(proposal["params"])
    params["after"] = {**params["after"], "starts_at": soon(2, 7).isoformat()}
    await db.execute(
        update(Approval).where(Approval.id == uuid.UUID(proposal["id"])).values(params=params)
    )
    await db.commit()
    result = (await approve(client, proposal["id"])).json()
    assert result["status"] == "failed" and result["error"] == "approval_params_mismatch"
    assert fake_connector.write_calls == 0
    denied = await db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == calendar_actions.MOVE, AuditEvent.outcome == "denied"
        )
    )
    assert denied is not None and denied.details == {"reason": "approval_params_mismatch"}
    # And the parameters of a calendar proposal cannot be edited when approving.
    other = (await client.post(f"/v1/calendar/events/{writable['private']}/cancel", json={})).json()
    edited = await client.post(
        f"/v1/approvals/{other['id']}/approve", json={"params": other["params"]}
    )
    assert edited.status_code == 422 and edited.json()["code"] == "approval_not_editable"
    # An approval without its digest (inserted by an older version) is never executed.
    await db.execute(
        update(Approval).where(Approval.id == uuid.UUID(other["id"])).values(params_sha256=None)
    )
    await db.commit()
    unbound = (await approve(client, other["id"])).json()
    assert unbound["status"] == "failed" and unbound["error"] == "approval_params_mismatch"
    assert fake_connector.write_calls == 0


async def test_an_event_changed_since_the_preview_is_not_overwritten(
    client: AsyncClient, db: AsyncSession, fake_connector: FakeConnector, writable: dict[str, str]
) -> None:
    proposal = (
        await client.post(f"/v1/calendar/events/{writable['private']}/move", json=when(soon(2, 16)))
    ).json()
    # Someone moves it at the provider meanwhile (new etag).
    fake_connector.add_event("primary", event("private", soon(2, 9), etag='"private-2"'))
    result = (await approve(client, proposal["id"])).json()
    assert result["status"] == "failed" and result["error"] == "event_changed"
    assert fake_connector.writes == []
    assert fake_connector.events["primary"]["private"].starts_at == soon(2, 9)


async def test_a_sync_that_sees_a_change_invalidates_the_pending_proposals(
    client: AsyncClient,
    database: Database,
    db: AsyncSession,
    connector_settings: Settings,
    fake_connector: FakeConnector,
    writable: dict[str, str],
) -> None:
    move = (
        await client.post(f"/v1/calendar/events/{writable['private']}/move", json=when(soon(2, 16)))
    ).json()
    cancel = (await client.post(f"/v1/calendar/events/{writable['shared']}/cancel", json={})).json()
    fake_connector.add_event("primary", event("private", soon(2, 9), etag='"private-2"'))
    fake_connector.remove_event("primary", "shared")
    await sync.sync_source(
        database, connector_settings, {"fake": fake_connector}, uuid.UUID(writable["source"])
    )
    db.expire_all()
    for proposal, reason in ((move, "event_changed"), (cancel, "event_gone")):
        stored = await db.get(Approval, uuid.UUID(proposal["id"]))
        assert stored is not None and (stored.status, stored.error) == ("invalidated", reason)
        replay = await approve(client, proposal["id"])
        assert replay.status_code == 409
        assert replay.json()["approval_status"] == "invalidated"
    listed = (await client.get("/v1/approvals", params={"status": "invalidated"})).json()
    assert {a["id"] for a in listed} == {move["id"], cancel["id"]}
    assert fake_connector.write_calls == 0


async def test_a_new_proposal_replaces_the_older_one_for_the_same_event(
    client: AsyncClient, db: AsyncSession, writable: dict[str, str]
) -> None:
    path = f"/v1/calendar/events/{writable['private']}/move"
    first = (await client.post(path, json=when(soon(2, 16)))).json()
    second = (await client.post(path, json=when(soon(2, 18)))).json()
    assert first["id"] != second["id"]
    db.expire_all()
    older = await db.get(Approval, uuid.UUID(first["id"]))
    assert older is not None and (older.status, older.error) == ("invalidated", "superseded")


async def test_refusals_when_proposing(
    client: AsyncClient,
    spaces: dict[str, str],
    db: AsyncSession,
    fake_connector: FakeConnector,
    writable: dict[str, str],
) -> None:
    # Someone else's event cannot be moved (only cancelled in one's own calendar).
    fake_connector.add_event("primary", event("private", soon(2, 14), organizer_self=False))
    moved = await client.post(
        f"/v1/calendar/events/{writable['private']}/move", json=when(soon(2, 16))
    )
    assert moved.status_code == 409 and moved.json()["code"] == "event_not_organizer"
    past = await client.post(
        "/v1/calendar/events",
        json={"space_id": writable["space"], "summary": "Hier", **when(soon(-1, 9))},
    )
    assert past.status_code == 422 and past.json()["code"] == "event_in_past"
    backwards = await client.post(
        "/v1/calendar/events",
        json={
            "space_id": writable["space"],
            "summary": "À l'envers",
            "starts_at": soon(3, 10).isoformat(),
            "ends_at": soon(3, 9).isoformat(),
        },
    )
    assert backwards.status_code == 422 and backwards.json()["code"] == "invalid_event_times"
    # A space without a calendar it may modify.
    nowhere = await client.post(
        "/v1/calendar/events",
        json={"space_id": spaces["professional"], "summary": "Bloc", **when(soon(3, 9))},
    )
    assert nowhere.status_code == 409 and nowhere.json()["code"] == "calendar_write_unavailable"
    # Another owner's event, or an unknown one: 404.
    unknown = await client.post(f"/v1/calendar/events/{uuid.uuid4()}/cancel", json={})
    assert unknown.status_code == 404
    gone = fake_connector.events["primary"].pop("shared")
    assert gone is not None
    vanished = await client.post(f"/v1/calendar/events/{writable['shared']}/cancel", json={})
    assert vanished.status_code == 409 and vanished.json()["code"] == "event_gone"
    assert await db.scalar(select(func.count()).select_from(Approval)) == 0


async def test_writable_calendars_and_editable_events(
    client: AsyncClient, writable: dict[str, str]
) -> None:
    calendars = (
        await client.get("/v1/calendar/calendars", params={"space_id": writable["space"]})
    ).json()
    assert [c["name"] for c in calendars] == ["Agenda de démonstration"]
    start = soon(0, 0)
    listed = (
        await client.get(
            "/v1/calendar/events",
            params={
                "start": start.isoformat(),
                "end": (start + timedelta(days=7)).isoformat(),
                "space_id": writable["space"],
            },
        )
    ).json()
    assert listed["items"] and all(e["editable"] for e in listed["items"])


async def test_all_day_events_move_as_dates(
    client: AsyncClient, fake_connector: FakeConnector, writable: dict[str, str]
) -> None:
    day = soon(10, 0).date()
    body = {
        "space_id": writable["space"],
        "summary": "Congé",
        "start_date": day.isoformat(),
        "end_date": (day + timedelta(days=2)).isoformat(),
    }
    proposal = (await client.post("/v1/calendar/events", json=body)).json()
    assert proposal["preview"]["after"].endswith("journées entières")
    assert (await approve(client, proposal["id"])).json()["status"] == "executed"
    created = fake_connector.events["primary"][
        operations.idempotency_key(uuid.UUID(proposal["id"]))
    ]
    assert (created.start_date, created.end_date) == (day, day + timedelta(days=2))
    assert created.starts_at is None


# --- Google Calendar API (MockTransport) ----------------------------------------------------


def google(settings: Settings, handler: Any) -> GoogleConnector:
    return GoogleConnector(
        settings.model_copy(
            update={
                "connectors_provider": "google",
                "connectors_key": SecretStr(CONNECTORS_KEY),
                "google_client_id": "client.apps.googleusercontent.com",
                "google_client_secret": SecretStr("secret"),
            }
        ),
        transport=httpx.MockTransport(handler),
    )


def _event_body(**extra: Any) -> dict[str, Any]:
    body = {
        "id": "lo0123456789abcdefghijklmnop",
        "etag": '"2"',
        "status": "confirmed",
        "summary": "Bloc",
        "start": {"dateTime": "2026-11-03T09:00:00+01:00", "timeZone": "Europe/Paris"},
        "end": {"dateTime": "2026-11-03T10:00:00+01:00", "timeZone": "Europe/Paris"},
        "organizer": {"self": True},
    }
    body.update(extra)
    return body


TIMES = EventTimes(
    starts_at=datetime(2026, 11, 3, 9, tzinfo=PARIS),
    ends_at=datetime(2026, 11, 3, 10, tzinfo=PARIS),
    timezone="Europe/Paris",
)


async def test_google_create_uses_the_client_id_and_treats_a_duplicate_as_done(
    settings: Settings,
) -> None:
    seen: list[httpx.Request] = []
    replies = [
        httpx.Response(409, json={"error": {"errors": [{"reason": "duplicate"}]}}),
        httpx.Response(
            200,
            json=_event_body(
                attendees=[{"email": "Alex@Example.org"}, {"email": "me@x.org", "self": True}]
            ),
        ),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return replies.pop(0)

    connector = google(settings, handler)
    draft = EventDraft(summary="Bloc", times=TIMES, guests=("alex@example.org",))
    created = await connector.create_event(
        "token", "primary", draft, event_id="lo0123456789abcdefghijklmnop", send_updates="none"
    )
    assert created.provider_id == "lo0123456789abcdefghijklmnop"
    assert created.guests == ("alex@example.org",)
    insert, read = seen
    assert insert.method == "POST"
    assert insert.url.path == "/calendar/v3/calendars/primary/events"
    assert insert.url.params["sendUpdates"] == "none"
    assert insert.headers["authorization"] == "Bearer token"
    sent = json.loads(insert.content)
    assert sent["id"] == "lo0123456789abcdefghijklmnop"
    assert sent["start"] == {"dateTime": "2026-11-03T09:00:00+01:00", "timeZone": "Europe/Paris"}
    assert sent["attendees"] == [{"email": "alex@example.org"}]
    assert read.method == "GET"
    assert read.url.path.endswith("/events/lo0123456789abcdefghijklmnop")
    # The key is a valid Google event id: base32hex, 5 to 1024 characters.
    key = operations.idempotency_key(uuid.uuid4())
    assert 5 <= len(key) <= 1024 and set(key) <= set("0123456789abcdefghijklmnopqrstuv")


async def test_google_move_is_conditional_on_the_etag(settings: Settings) -> None:
    seen: list[httpx.Request] = []
    replies: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return replies.pop(0)

    connector = google(settings, handler)
    replies.append(httpx.Response(200, json=_event_body()))
    moved = await connector.move_event(
        "token", "primary", "ev1", TIMES, etag='"1"', send_updates="all"
    )
    assert moved.etag == '"2"'
    patch = seen[-1]
    assert patch.method == "PATCH" and patch.headers["if-match"] == '"1"'
    assert patch.url.params["sendUpdates"] == "all"
    assert json.loads(patch.content)["start"]["date"] is None

    # 412: already at the target times (a replay) is a success...
    replies += [httpx.Response(412), httpx.Response(200, json=_event_body())]
    again = await connector.move_event(
        "token", "primary", "ev1", TIMES, etag='"1"', send_updates="none"
    )
    assert again.starts_at == TIMES.starts_at
    # ... elsewhere, a conflict: nothing is overwritten.
    elsewhere = _event_body(
        start={"dateTime": "2026-11-03T15:00:00+01:00"},
        end={"dateTime": "2026-11-03T16:00:00+01:00"},
    )
    replies += [httpx.Response(412), httpx.Response(200, json=elsewhere)]
    with pytest.raises(ConnectorConflictError):
        await connector.move_event(
            "token", "primary", "ev1", TIMES, etag='"1"', send_updates="none"
        )


async def test_google_cancel_and_unknown_outcomes(settings: Settings) -> None:
    seen: list[httpx.Request] = []
    replies: list[httpx.Response | Exception] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    connector = google(settings, handler)
    # Deleted already (410 Gone): done.
    replies.append(httpx.Response(410))
    await connector.cancel_event("token", "primary", "ev1", etag='"3"', send_updates="none")
    assert seen[-1].method == "DELETE" and seen[-1].headers["if-match"] == '"3"'
    replies.append(httpx.Response(204))
    await connector.cancel_event("token", "primary", "ev1", etag=None, send_updates="all")
    assert "if-match" not in seen[-1].headers and seen[-1].url.params["sendUpdates"] == "all"
    # A timeout once sent, or a server error: the outcome is unknown, never "not done".
    replies.append(httpx.ReadTimeout("slow"))
    with pytest.raises(OutcomeUnknownError):
        await connector.cancel_event("token", "primary", "ev1", etag=None, send_updates="none")
    replies.append(httpx.Response(503))
    with pytest.raises(OutcomeUnknownError):
        await connector.create_event(
            "token",
            "primary",
            EventDraft(summary="x", times=TIMES),
            event_id="lo0123456789",
            send_updates="none",
        )
    # Nothing sent (connection refused): plainly unavailable.
    replies.append(httpx.ConnectError("refused"))
    with pytest.raises(ConnectorUnavailableError):
        await connector.move_event("token", "primary", "ev1", TIMES, etag=None, send_updates="none")
    # An all-day event is written as dates.
    replies.append(
        httpx.Response(
            200, json=_event_body(start={"date": "2026-11-03"}, end={"date": "2026-11-04"})
        )
    )
    await connector.move_event(
        "token",
        "primary",
        "ev1",
        EventTimes(start_date=date(2026, 11, 3), end_date=date(2026, 11, 4)),
        etag=None,
        send_updates="none",
    )
    assert json.loads(seen[-1].content)["start"] == {
        "date": "2026-11-03",
        "dateTime": None,
        "timeZone": None,
    }
