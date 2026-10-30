"""Calendar sync (task 14): import, time zones, recurrences, cancellations, updates, resume.

Acceptance criteria: an occurrence stays unique after a resync; its time is right across
daylight saving changes. Every test uses the in-memory FakeConnector (no network), except
the Google parsing tests, which go through httpx.MockTransport.
"""

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
from lifeos.db.session import Database
from lifeos.main import create_app
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.calendar import sync
from lifeos.modules.calendar.models import CalendarEvent
from lifeos.modules.connectors.base import (
    ConnectorAuthError,
    ConnectorGoneError,
    ConnectorQuotaError,
    ConnectorUnavailableError,
    RemoteEvent,
)
from lifeos.modules.connectors.fake import FakeConnector
from lifeos.modules.connectors.google import GoogleConnector, parse_event
from lifeos.modules.connectors.models import ConnectorGrant, ConnectorSource
from lifeos.modules.llm.fake import FakeProvider

CONNECTORS_KEY = base64.b64encode(b"k" * 32).decode()
PARIS = ZoneInfo("Europe/Paris")
# 2026: summer time ends on Sunday 25 October in Paris (03:00 CEST -> 02:00 CET).
NOW = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)


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


async def connect(client: AsyncClient, space_id: str, capabilities: list[str]) -> dict[str, Any]:
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
    account: dict[str, Any] = callback.json()["account"]
    return account


async def map_calendar(
    client: AsyncClient, account_id: str, space_id: str, ref: str = "primary"
) -> str:
    response = await client.post(
        "/v1/connectors/sources",
        json={"account_id": account_id, "provider_ref": ref, "space_id": space_id},
    )
    assert response.status_code == 201, response.text
    source_id: str = response.json()["id"]
    return source_id


def timed(
    provider_id: str, summary: str, start: datetime, minutes: int = 60, **extra: Any
) -> RemoteEvent:
    return RemoteEvent(
        provider_id=provider_id,
        status="confirmed",
        summary=summary,
        starts_at=start,
        ends_at=start + timedelta(minutes=minutes),
        timezone="Europe/Paris",
        **extra,
    )


async def events(db: AsyncSession, source_id: str) -> list[CalendarEvent]:
    db.expire_all()
    return list(
        await db.scalars(
            select(CalendarEvent)
            .where(CalendarEvent.source_id == uuid.UUID(source_id))
            .order_by(CalendarEvent.provider_event_id)
        )
    )


async def run_sync(
    database: Database,
    settings: Settings,
    connector: FakeConnector,
    source_id: str,
    now: datetime = NOW,
) -> sync.SyncResult:
    return await sync.sync_source(
        database, settings, {"fake": connector}, uuid.UUID(source_id), now=now
    )


@pytest.fixture
async def mapped(
    client: AsyncClient, spaces: dict[str, str], fake_connector: FakeConnector
) -> dict[str, str]:
    account = await connect(client, spaces["personal"], ["calendar.read"])
    source_id = await map_calendar(client, account["id"], spaces["personal"])
    return {"account": account["id"], "source": source_id, "space": spaces["personal"]}


async def test_an_occurrence_stays_unique_after_resyncs(
    database: Database,
    db: AsyncSession,
    connector_settings: Settings,
    fake_connector: FakeConnector,
    mapped: dict[str, str],
) -> None:
    start = datetime(2026, 10, 21, 9, 30, tzinfo=PARIS)
    fake_connector.add_event("primary", timed("e1", "Point d'équipe", start))
    fake_connector.add_event("primary", timed("e2", "Déjeuner", start + timedelta(hours=3)))

    for _ in range(3):
        result = await run_sync(database, connector_settings, fake_connector, mapped["source"])
        assert result.outcome == "synced", result
    rows = await events(db, mapped["source"])
    assert [r.provider_event_id for r in rows] == ["e1", "e2"]

    # Moved and renamed at the provider: updated in place, same row.
    first_id = rows[0].id
    fake_connector.add_event(
        "primary", timed("e1", "Point d'équipe (déplacé)", start + timedelta(hours=1))
    )
    await run_sync(database, connector_settings, fake_connector, mapped["source"])
    rows = await events(db, mapped["source"])
    assert len(rows) == 2
    assert rows[0].id == first_id and rows[0].summary == "Point d'équipe (déplacé)"
    assert rows[0].starts_at == datetime(2026, 10, 21, 8, 30, tzinfo=UTC)

    # Cancelled at the provider: gone after the next complete pass.
    fake_connector.remove_event("primary", "e2")
    result = await run_sync(database, connector_settings, fake_connector, mapped["source"])
    assert result.removed == 1
    assert [r.provider_event_id for r in await events(db, mapped["source"])] == ["e1"]
    assert await db.scalar(select(func.count()).select_from(CalendarEvent)) == 1


async def test_local_time_is_kept_across_daylight_saving_changes(
    client: AsyncClient,
    database: Database,
    db: AsyncSession,
    connector_settings: Settings,
    fake_connector: FakeConnector,
    mapped: dict[str, str],
) -> None:
    # A weekly 09:00 meeting in Paris, expanded by the provider in its time zone: before
    # and after the end of summer time (Sunday 25 October 2026).
    for day in (date(2026, 10, 22), date(2026, 10, 29)):
        local = datetime(day.year, day.month, day.day, 9, 0, tzinfo=PARIS)
        fake_connector.add_event(
            "primary",
            timed(f"weekly_{day:%Y%m%d}", "Hebdo", local, series_id="weekly"),
        )
    await run_sync(database, connector_settings, fake_connector, mapped["source"])

    rows = await events(db, mapped["source"])
    assert [r.starts_at for r in rows] == [
        datetime(2026, 10, 22, 7, 0, tzinfo=UTC),  # CEST, UTC+2
        datetime(2026, 10, 29, 8, 0, tzinfo=UTC),  # CET, UTC+1
    ]
    assert all(r.series_id == "weekly" for r in rows)

    listed = await client.get(
        "/v1/calendar/events",
        params={"start": "2026-10-19T00:00:00+02:00", "end": "2026-11-02T00:00:00+01:00"},
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["timezone"] == "Europe/Paris"
    assert [i["starts_at"] for i in body["items"]] == [
        "2026-10-22T09:00:00+02:00",
        "2026-10-29T09:00:00+01:00",
    ]
    assert all(i["recurring"] and not i["all_day"] for i in body["items"])
    assert body["items"][0]["source_name"] == "Agenda de démonstration"


async def test_all_day_occurrences_stay_dates(
    client: AsyncClient,
    database: Database,
    db: AsyncSession,
    connector_settings: Settings,
    fake_connector: FakeConnector,
    mapped: dict[str, str],
) -> None:
    fake_connector.add_event(
        "primary",
        RemoteEvent(
            provider_id="holiday",
            status="confirmed",
            summary="Toussaint",
            starts_at=None,
            ends_at=None,
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 2),
            busy=False,
        ),
    )
    await run_sync(database, connector_settings, fake_connector, mapped["source"])
    (row,) = await events(db, mapped["source"])
    assert (row.start_date, row.end_date, row.starts_at) == (
        date(2026, 11, 1),
        date(2026, 11, 2),
        None,
    )

    # The day itself, in the owner's time zone: listed; the day after: not.
    day = await client.get(
        "/v1/calendar/events",
        params={"start": "2026-11-01T00:00:00+01:00", "end": "2026-11-02T00:00:00+01:00"},
    )
    (item,) = day.json()["items"]
    assert item["all_day"] and item["start_date"] == "2026-11-01" and not item["busy"]
    after = await client.get(
        "/v1/calendar/events",
        params={"start": "2026-11-02T00:00:00+01:00", "end": "2026-11-03T00:00:00+01:00"},
    )
    assert after.json()["items"] == []


async def test_an_interrupted_sync_resumes_and_removes_nothing_meanwhile(
    database: Database,
    db: AsyncSession,
    connector_settings: Settings,
    fake_connector: FakeConnector,
    mapped: dict[str, str],
) -> None:
    fake_connector.page_size = 2
    start = datetime(2026, 10, 21, 8, 0, tzinfo=PARIS)
    for n in range(5):
        fake_connector.add_event(
            "primary", timed(f"e{n}", f"Rendez-vous {n}", start + timedelta(hours=n))
        )
    await run_sync(database, connector_settings, fake_connector, mapped["source"])
    assert len(await events(db, mapped["source"])) == 5

    # e0 deleted at the provider; the next pass fails on its second page.
    fake_connector.remove_event("primary", "e0")
    fake_connector.add_event("primary", timed("e9", "Nouveau", start + timedelta(hours=9)))
    calls = fake_connector.event_calls
    original = fake_connector.list_events

    async def failing_second_page(*args: Any, **kwargs: Any) -> Any:
        if fake_connector.event_calls == calls + 1:
            fake_connector.event_calls += 1
            raise ConnectorUnavailableError("http_503")
        return await original(*args, **kwargs)

    fake_connector.list_events = failing_second_page  # type: ignore[method-assign]
    result = await run_sync(database, connector_settings, fake_connector, mapped["source"])
    assert (result.outcome, result.error, result.removed) == ("failed", "http_503", 0)
    source = await db.get(ConnectorSource, uuid.UUID(mapped["source"]))
    assert source is not None
    await db.refresh(source)
    assert source.page_token == "2" and source.lease_until is None
    assert source.last_error == "http_503"
    # Nothing removed by an incomplete pass.
    assert "e0" in [r.provider_event_id for r in await events(db, mapped["source"])]

    # Next run: resumes at the stored page, then sweeps.
    fake_connector.list_events = original  # type: ignore[method-assign]
    result = await run_sync(database, connector_settings, fake_connector, mapped["source"])
    assert result.outcome == "synced" and result.pages == 2
    ids = [r.provider_event_id for r in await events(db, mapped["source"])]
    assert ids == ["e1", "e2", "e3", "e4", "e9"]
    await db.refresh(source)
    assert source.page_token is None and source.last_error is None
    assert source.last_synced_at is not None


async def test_one_pass_at_a_time_per_calendar(
    database: Database,
    connector_settings: Settings,
    fake_connector: FakeConnector,
    mapped: dict[str, str],
) -> None:
    async with database.sessionmaker() as session, session.begin():
        await session.execute(
            update(ConnectorSource)
            .where(ConnectorSource.id == uuid.UUID(mapped["source"]))
            .values(lease_until=NOW + timedelta(minutes=5))
        )
    result = await run_sync(database, connector_settings, fake_connector, mapped["source"])
    assert result.outcome == "skipped"
    # A lease left by a crashed worker expires.
    later = await run_sync(
        database, connector_settings, fake_connector, mapped["source"], NOW + timedelta(minutes=6)
    )
    assert later.outcome == "synced"


async def test_no_sync_without_the_consent_of_the_space(
    client: AsyncClient,
    database: Database,
    db: AsyncSession,
    connector_settings: Settings,
    fake_connector: FakeConnector,
    spaces: dict[str, str],
    mapped: dict[str, str],
) -> None:
    fake_connector.add_event(
        "primary", timed("e1", "Réunion", datetime(2026, 10, 21, 9, tzinfo=PARIS))
    )
    # Mapping into a space without consent: refused (and audited).
    refused = await client.post(
        "/v1/connectors/sources",
        json={
            "account_id": mapped["account"],
            "provider_ref": "family",
            "space_id": spaces["professional"],
        },
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "connector_capability_missing"

    # Consent withdrawn: the sync stops (data kept, the owner removes the source if wanted).
    grant = await db.scalar(
        select(ConnectorGrant).where(ConnectorGrant.account_id == uuid.UUID(mapped["account"]))
    )
    assert grant is not None
    assert (await client.delete(f"/v1/connectors/grants/{grant.id}")).status_code == 204
    calls = fake_connector.event_calls
    result = await run_sync(database, connector_settings, fake_connector, mapped["source"])
    assert (result.outcome, result.error) == ("skipped", "consent_missing")
    assert fake_connector.event_calls == calls

    accounts = (await client.get("/v1/connectors/accounts")).json()
    (source,) = accounts[0]["sources"]
    assert source["last_error"] == "consent_missing"


async def test_removing_a_source_or_purging_the_account_deletes_its_occurrences(
    client: AsyncClient,
    database: Database,
    db: AsyncSession,
    connector_settings: Settings,
    fake_connector: FakeConnector,
    mapped: dict[str, str],
) -> None:
    fake_connector.add_event(
        "primary", timed("e1", "Réunion", datetime(2026, 10, 21, 9, tzinfo=PARIS))
    )
    await run_sync(database, connector_settings, fake_connector, mapped["source"])
    assert len(await events(db, mapped["source"])) == 1
    assert (await client.delete(f"/v1/connectors/sources/{mapped['source']}")).status_code == 204
    assert await events(db, mapped["source"]) == []

    # Mapped again, synced, then the account disconnected with purge.
    source_id = await map_calendar(client, mapped["account"], mapped["space"])
    await run_sync(database, connector_settings, fake_connector, source_id)
    assert len(await events(db, source_id)) == 1
    response = await client.delete(
        f"/v1/connectors/accounts/{mapped['account']}", params={"purge": "true"}
    )
    assert response.status_code == 200
    assert await events(db, source_id) == []


async def test_calendar_listing_and_mapping(
    client: AsyncClient, mapped: dict[str, str], spaces: dict[str, str]
) -> None:
    calendars = (await client.get(f"/v1/connectors/accounts/{mapped['account']}/calendars")).json()
    assert [(c["provider_ref"], c["primary"], c["read_only"]) for c in calendars] == [
        ("primary", True, False),
        ("family", False, True),
    ]
    assert calendars[0]["source_id"] == mapped["source"]
    assert calendars[0]["space_id"] == spaces["personal"]
    assert calendars[1]["source_id"] is None
    # The same calendar cannot feed two spaces; an unknown one is not found.
    unknown = await client.post(
        "/v1/connectors/sources",
        json={"account_id": mapped["account"], "provider_ref": "nope", "space_id": mapped["space"]},
    )
    assert unknown.status_code == 404
    again = await client.post(
        "/v1/connectors/sources",
        json={
            "account_id": mapped["account"],
            "provider_ref": "primary",
            "space_id": mapped["space"],
        },
    )
    assert again.status_code == 201 and again.json()["id"] == mapped["source"]


async def test_events_are_scoped_to_the_spaces_and_audited_without_content(
    client: AsyncClient,
    database: Database,
    db: AsyncSession,
    connector_settings: Settings,
    fake_connector: FakeConnector,
    mapped: dict[str, str],
    spaces: dict[str, str],
) -> None:
    fake_connector.add_event(
        "primary",
        timed(
            "e1", "Rendez-vous médical", datetime(2026, 10, 21, 9, tzinfo=PARIS), description="Dr X"
        ),
    )
    await run_sync(database, connector_settings, fake_connector, mapped["source"])
    window = {"start": "2026-10-19T00:00:00+02:00", "end": "2026-10-26T00:00:00+01:00"}
    personal = await client.get(
        "/v1/calendar/events", params={**window, "space_id": spaces["personal"]}
    )
    assert len(personal.json()["items"]) == 1
    professional = await client.get(
        "/v1/calendar/events", params={**window, "space_id": spaces["professional"]}
    )
    assert professional.json()["items"] == []
    foreign = await client.get(
        "/v1/calendar/events", params={**window, "space_id": str(uuid.uuid4())}
    )
    assert foreign.status_code == 404
    too_long = await client.get(
        "/v1/calendar/events",
        params={"start": "2026-01-01T00:00:00+01:00", "end": "2026-06-01T00:00:00+02:00"},
    )
    assert too_long.status_code == 422

    audited = list(await db.scalars(select(AuditEvent).where(AuditEvent.action == "calendar.sync")))
    assert audited and audited[-1].details["upserted"] == 1
    assert "médical" not in json.dumps([a.details for a in audited])


async def test_sync_errors_keep_the_data(
    database: Database,
    db: AsyncSession,
    connector_settings: Settings,
    fake_connector: FakeConnector,
    mapped: dict[str, str],
) -> None:
    fake_connector.add_event(
        "primary", timed("e1", "Réunion", datetime(2026, 10, 21, 9, tzinfo=PARIS))
    )
    await run_sync(database, connector_settings, fake_connector, mapped["source"])

    fake_connector.fail_next("list_events", ConnectorQuotaError("rate_limited"))
    result = await run_sync(database, connector_settings, fake_connector, mapped["source"])
    assert (result.outcome, result.error) == ("failed", "rate_limited")
    assert len(await events(db, mapped["source"])) == 1

    # A refused access token is refreshed once, then the pass goes on.
    refreshes = fake_connector.refresh_calls
    fake_connector.fail_next("list_events", ConnectorAuthError("access_token_rejected"))
    result = await run_sync(database, connector_settings, fake_connector, mapped["source"])
    assert result.outcome == "synced"
    assert fake_connector.refresh_calls == refreshes + 1

    # The calendar deleted at the provider: reported, the data kept.
    fake_connector.fail_next("list_events", ConnectorGoneError("http_404"))
    result = await run_sync(database, connector_settings, fake_connector, mapped["source"])
    assert (result.outcome, result.error) == ("failed", "calendar_gone")
    assert len(await events(db, mapped["source"])) == 1


async def test_due_sources_are_synced_by_the_periodic_job(
    database: Database,
    connector_settings: Settings,
    fake_connector: FakeConnector,
    mapped: dict[str, str],
) -> None:
    counts = await sync.sync_due(database, connector_settings, {"fake": fake_connector}, now=NOW)
    assert counts == {"synced": 1}
    # Just synced: not due again before calendar_sync_minutes.
    again = await sync.sync_due(
        database, connector_settings, {"fake": fake_connector}, now=NOW + timedelta(minutes=1)
    )
    assert again == {}


def test_the_window_is_stable_within_a_day(connector_settings: Settings) -> None:
    morning = sync.window(connector_settings, datetime(2026, 10, 20, 0, 5, tzinfo=UTC))
    evening = sync.window(connector_settings, datetime(2026, 10, 20, 23, 55, tzinfo=UTC))
    assert morning == evening
    assert morning[0] == datetime(2026, 9, 20, tzinfo=UTC)
    assert morning[1] == datetime(2027, 10, 21, tzinfo=UTC)


# --- Google Calendar API (MockTransport: no request reaches Google) ------------------------


def google_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "connectors_provider": "google",
            "connectors_key": SecretStr(CONNECTORS_KEY),
            "google_client_id": "client.apps.googleusercontent.com",
            "google_client_secret": SecretStr("secret"),
        }
    )


def test_google_events_are_parsed_into_aware_instants_or_dates() -> None:
    event = parse_event(
        {
            "id": "abc_20261029T080000Z",
            "status": "confirmed",
            "summary": "  Hebdo  ",
            "start": {"dateTime": "2026-10-29T09:00:00+01:00", "timeZone": "Europe/Paris"},
            "end": {"dateTime": "2026-10-29T10:00:00+01:00", "timeZone": "Europe/Paris"},
            "recurringEventId": "abc",
            "attendees": [{"self": True, "responseStatus": "accepted"}, {"email": "x@y"}],
            "organizer": {"self": False},
            "transparency": "opaque",
            "etag": '"123"',
        }
    )
    assert event is not None
    assert event.starts_at == datetime(2026, 10, 29, 8, 0, tzinfo=UTC)
    assert event.summary == "Hebdo" and event.series_id == "abc"
    assert (event.response, event.attendees, event.busy, event.all_day) == (
        "accepted",
        2,
        True,
        False,
    )

    all_day = parse_event(
        {
            "id": "d1",
            "start": {"date": "2026-11-01"},
            "end": {"date": "2026-11-02"},
            "transparency": "transparent",
        }
    )
    assert all_day is not None and all_day.all_day and not all_day.busy
    assert (all_day.start_date, all_day.end_date) == (date(2026, 11, 1), date(2026, 11, 2))

    assert parse_event({"id": "x", "status": "cancelled"}) is None
    assert parse_event({"id": "x", "start": {"dateTime": "2026-10-29T09:00:00"}}) is None
    assert parse_event({"start": {"date": "2026-11-01"}}) is None
    long = parse_event(
        {
            "id": "l",
            "summary": "a" * 5000,
            "description": "b" * 9000,
            "start": {"date": "2026-11-01"},
            "end": {"date": "2026-11-01"},
            "start_tz": "Not/AZone",
        }
    )
    assert long is not None and len(long.summary) == 300 and long.description is not None
    assert len(long.description) == 2000 and long.end_date == date(2026, 11, 2)


async def test_google_calendar_requests_and_error_mapping(settings: Settings) -> None:
    seen: list[httpx.Request] = []
    replies: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return replies.pop(0)

    connector = GoogleConnector(google_settings(settings), transport=httpx.MockTransport(handler))
    replies.append(
        httpx.Response(
            200,
            json={
                "items": [
                    {"id": "primary@x", "summary": "Moi", "primary": True, "accessRole": "owner"},
                    {
                        "id": "fr.french#holiday@group.v.calendar.google.com",
                        "summary": "Fêtes",
                        "accessRole": "reader",
                    },
                ]
            },
        )
    )
    calendars = await connector.list_calendars("token-1")
    assert [(c.provider_ref, c.primary, c.read_only) for c in calendars] == [
        ("primary@x", True, False),
        ("fr.french#holiday@group.v.calendar.google.com", False, True),
    ]
    assert seen[0].headers["authorization"] == "Bearer token-1"
    assert seen[0].url.host == "www.googleapis.com"

    replies.append(httpx.Response(200, json={"items": [], "nextPageToken": "p2"}))
    page = await connector.list_events(
        "token-1",
        "fr.french#holiday@group.v.calendar.google.com",
        time_min=datetime(2026, 9, 20, tzinfo=UTC),
        time_max=datetime(2027, 10, 21, tzinfo=UTC),
        page="p1",
    )
    assert page.next_page == "p2"
    request = seen[-1]
    assert (
        request.url.raw_path.decode()
        .split("?")[0]
        .endswith("/calendars/fr.french%23holiday%40group.v.calendar.google.com/events")
    )
    query = parse_qs(request.url.query.decode())
    assert query["singleEvents"] == ["true"] and query["showDeleted"] == ["false"]
    assert query["pageToken"] == ["p1"]
    assert query["timeMin"] == ["2026-09-20T00:00:00+00:00"]
    assert "token-1" not in str(request.url)

    window = {
        "time_min": datetime(2026, 9, 20, tzinfo=UTC),
        "time_max": datetime(2027, 1, 1, tzinfo=UTC),
    }
    for reply, error, reason in (
        (httpx.Response(401), ConnectorAuthError, "access_token_rejected"),
        (httpx.Response(404), ConnectorGoneError, "http_404"),
        (httpx.Response(410), ConnectorGoneError, "http_410"),
        (httpx.Response(429, headers={"retry-after": "30"}), ConnectorQuotaError, "rate_limited"),
        (
            httpx.Response(403, json={"error": {"errors": [{"reason": "rateLimitExceeded"}]}}),
            ConnectorQuotaError,
            "rate_limited",
        ),
        (
            httpx.Response(403, json={"error": {"errors": [{"reason": "forbidden"}]}}),
            Exception,
            "forbidden",
        ),
        (httpx.Response(503), ConnectorUnavailableError, "http_503"),
    ):
        replies.append(reply)
        with pytest.raises(error) as caught:
            await connector.list_events("token-1", "primary", **window)
        assert getattr(caught.value, "reason", None) == reason


async def test_the_export_lists_synced_occurrences(
    client: AsyncClient,
    database: Database,
    connector_settings: Settings,
    fake_connector: FakeConnector,
    mapped: dict[str, str],
) -> None:
    fake_connector.add_event(
        "primary", timed("e1", "Réunion", datetime(2026, 10, 21, 9, tzinfo=PARIS))
    )
    await run_sync(database, connector_settings, fake_connector, mapped["source"])
    exported = (await client.get("/v1/privacy/export")).json()
    assert [e["summary"] for e in exported["calendar_events"]] == ["Réunion"]
