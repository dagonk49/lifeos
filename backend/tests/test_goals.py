from decimal import Decimal

from httpx import AsyncClient

from lifeos.modules.goals.service import metric_ratio


def test_metric_ratio_handles_increasing_and_decreasing_targets() -> None:
    assert metric_ratio(Decimal(0), Decimal(10), Decimal(5)) == 0.5
    assert metric_ratio(Decimal(80), Decimal(75), Decimal(78)) == 0.4  # weight loss
    assert metric_ratio(Decimal(80), Decimal(75), Decimal(82)) == 0.0  # clamped
    assert metric_ratio(None, Decimal(10), Decimal(12)) == 1.0
    assert metric_ratio(Decimal(0), Decimal(10), None) is None


async def test_measurable_goal_with_checkins_and_linked_tasks(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    created = await client.post(
        "/v1/goals",
        json={
            "space_id": spaces["personal"],
            "title": "Courir un semi-marathon",
            "motivation": "Être en forme pour mes 40 ans",
            "kind": "measurable",
            "metric_unit": "km",
            "start_value": "5",
            "target_value": "21.1",
            "due_date": "2027-04-01",
        },
    )
    assert created.status_code == 201, created.text
    goal = created.json()
    assert goal["current_value"] == "5.00"
    assert goal["progress"] == {
        "ratio": 0.0,
        "basis": "metric",
        "milestones_done": 0,
        "milestones_total": 0,
        "tasks_open": 0,
        "tasks_done": 0,
    }

    checkin = await client.post(
        f"/v1/goals/{goal['id']}/checkins", json={"value": "13.05", "note": "Sortie longue"}
    )
    assert checkin.status_code == 201
    task = await client.post(
        "/v1/tasks",
        json={
            "space_id": spaces["personal"],
            "title": "Acheter des chaussures",
            "goal_id": goal["id"],
        },
    )
    assert task.status_code == 201 and task.json()["goal_id"] == goal["id"]

    detail = (await client.get(f"/v1/goals/{goal['id']}")).json()
    assert detail["current_value"] == "13.05"
    assert round(detail["progress"]["ratio"], 2) == 0.5
    assert detail["progress"]["tasks_open"] == 1
    assert detail["checkins"][0]["note"] == "Sortie longue"
    linked = (await client.get(f"/v1/tasks?goal_id={goal['id']}")).json()["items"]
    assert [t["title"] for t in linked] == ["Acheter des chaussures"]


async def test_qualitative_goal_with_milestones(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    goal = (
        await client.post(
            "/v1/goals", json={"space_id": spaces["professional"], "title": "Lancer le site"}
        )
    ).json()
    first = (
        await client.post(f"/v1/goals/{goal['id']}/milestones", json={"title": "Maquettes"})
    ).json()
    await client.post(f"/v1/goals/{goal['id']}/milestones", json={"title": "Mise en ligne"})
    done = await client.patch(
        f"/v1/goals/{goal['id']}/milestones/{first['id']}", json={"done": True}
    )
    assert done.json()["done_at"] is not None
    detail = (await client.get(f"/v1/goals/{goal['id']}")).json()
    assert detail["progress"]["basis"] == "milestones"
    assert detail["progress"]["ratio"] == 0.5
    assert [m["title"] for m in detail["milestones"]] == ["Maquettes", "Mise en ligne"]

    achieved = await client.patch(
        f"/v1/goals/{goal['id']}", json={"status": "achieved"}, headers={"If-Match": "1"}
    )
    assert achieved.json()["achieved_at"] is not None
    assert achieved.json()["progress"]["ratio"] == 1.0
    stale = await client.patch(
        f"/v1/goals/{goal['id']}", json={"title": "x"}, headers={"If-Match": "1"}
    )
    assert stale.status_code == 409

    active = (await client.get("/v1/goals?status=active")).json()
    assert active == []
    assert (await client.delete(f"/v1/goals/{goal['id']}")).status_code == 204


async def test_goal_validation_and_space_rules(client: AsyncClient, spaces: dict[str, str]) -> None:
    missing_target = await client.post(
        "/v1/goals", json={"space_id": spaces["personal"], "title": "x", "kind": "measurable"}
    )
    assert missing_target.status_code == 422
    pro_goal = (
        await client.post("/v1/goals", json={"space_id": spaces["professional"], "title": "Pro"})
    ).json()
    mismatch = await client.post(
        "/v1/tasks",
        json={"space_id": spaces["personal"], "title": "Perso", "goal_id": pro_goal["id"]},
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["code"] == "goal_space_mismatch"
    empty_checkin = await client.post(f"/v1/goals/{pro_goal['id']}/checkins", json={})
    assert empty_checkin.status_code == 422
