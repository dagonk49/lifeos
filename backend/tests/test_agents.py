from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from lifeos.modules.agents.registry import suggest_agent
from lifeos.modules.llm.fake import FakeProvider

from .conftest import enable_cloud


def test_agent_suggestion_is_deterministic() -> None:
    assert suggest_agent("Aide-moi à planifier ma semaine, j'ai trop de tâches") == "planning"
    assert suggest_agent("Je suis découragé, je n'avance pas sur mon objectif") == "coaching"
    assert suggest_agent("Quand ai-je changé de banque ? Rappelle-moi") == "memory"
    assert suggest_agent("Bonjour") is None


async def _conversation(client: AsyncClient, space: str) -> str:
    conversation_id: str = (
        await client.post("/v1/chat/conversations", json={"space_id": space})
    ).json()["id"]
    return conversation_id


async def test_agent_needs_explicit_access_then_sees_only_its_resources(
    client: AsyncClient, spaces: dict[str, str], fake_llm: FakeProvider
) -> None:
    space = spaces["personal"]
    await enable_cloud(client, space)
    soon = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    await client.post(
        "/v1/tasks",
        json={
            "space_id": space,
            "title": "Préparer la réunion",
            "due_at": soon,
            "priority": "high",
        },
    )
    await client.post("/v1/goals", json={"space_id": space, "title": "Courir 10 km"})
    await client.post(
        "/v1/memory/facts", json={"space_id": space, "statement": "Ma réunion préférée : le lundi"}
    )
    conversation = await _conversation(client, space)

    agents = (await client.get(f"/v1/agents?space_id={space}")).json()
    planning = next(a for a in agents if a["name"] == "planning")
    assert planning["missing_permissions"] == ["tasks", "goals"]

    refused = (
        await client.post(
            f"/v1/chat/conversations/{conversation}/messages",
            json={"content": "Organise ma semaine", "agent": "planning"},
        )
    ).json()
    assert refused["error"]["code"] == "agent_permission_required"
    assert refused["error"]["missing"] == ["tasks", "goals"]
    assert fake_llm.requests == []

    granted = await client.put(
        "/v1/agents/planning/access", json={"space_id": space, "enabled": True}
    )
    assert granted.status_code == 200, granted.text
    sent = (
        await client.post(
            f"/v1/chat/conversations/{conversation}/messages",
            json={"content": "Organise ma réunion de la semaine", "agent": "planning"},
        )
    ).json()
    assert sent["error"] is None
    assert sent["assistant_message"]["agent"] == "planning"
    request = fake_llm.requests[-1]
    assert request.purpose == "agent.planning"
    assert request.system is not None
    assert "Préparer la réunion (priorité haute, échéance" in request.system
    assert "CHARGE DES 7 PROCHAINS JOURS" in request.system
    assert "Courir 10 km" in request.system
    # Facts are not part of the planning agent's permissions.
    assert "lundi" not in request.system

    # Withdrawal is immediate.
    await client.put("/v1/agents/planning/access", json={"space_id": space, "enabled": False})
    again = (
        await client.post(
            f"/v1/chat/conversations/{conversation}/messages",
            json={"content": "Et maintenant ?", "agent": "planning"},
        )
    ).json()
    assert again["error"]["code"] == "agent_permission_required"


async def test_agent_access_is_per_space(
    client: AsyncClient, spaces: dict[str, str], fake_llm: FakeProvider
) -> None:
    personal, pro = spaces["personal"], spaces["professional"]
    await enable_cloud(client, pro)
    await client.post(
        "/v1/memory/facts", json={"space_id": pro, "statement": "Le client Durand paie à 60 jours"}
    )
    await client.put("/v1/agents/memory/access", json={"space_id": personal, "enabled": True})
    conversation = await _conversation(client, pro)
    refused = (
        await client.post(
            f"/v1/chat/conversations/{conversation}/messages",
            json={"content": "Que sais-tu du client Durand ?", "agent": "memory"},
        )
    ).json()
    assert refused["error"]["code"] == "agent_permission_required"

    await client.put("/v1/agents/memory/access", json={"space_id": pro, "enabled": True})
    answered = (
        await client.post(
            f"/v1/chat/conversations/{conversation}/messages",
            json={"content": "Que sais-tu du client Durand ?", "agent": "memory"},
        )
    ).json()
    assert answered["error"] is None
    assert "Durand paie à 60 jours" in (fake_llm.requests[-1].system or "")


async def test_general_assistant_suggests_an_agent(
    client: AsyncClient, spaces: dict[str, str]
) -> None:
    conversation = await _conversation(client, spaces["personal"])
    sent = (
        await client.post(
            f"/v1/chat/conversations/{conversation}/messages",
            json={"content": "Peux-tu m'aider à planifier mes tâches en retard ?"},
        )
    ).json()
    assert sent["agent"] == "core"
    assert sent["suggested_agent"] == "planning"
