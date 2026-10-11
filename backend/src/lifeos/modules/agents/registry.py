"""Specialised agents, activated on demand (F08).

An agent is a prompt plus a deterministic context builder. It reads data only through its
own principal (explicit, time-limited permissions per space) and never acts: proposals go
through approvals like everything else. One cloud call per request at most.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    name: str
    label: str
    description: str
    resources: tuple[str, ...]
    instructions: str
    keywords: tuple[str, ...]


AGENT_SPECS: dict[str, AgentSpec] = {
    spec.name: spec
    for spec in (
        AgentSpec(
            name="planning",
            label="Planification",
            description="Priorise les tâches, répartit la charge, relie le travail aux objectifs.",
            resources=("tasks", "goals"),
            instructions=(
                "Rôle : agent de planification. Appuie-toi sur la liste des tâches et la charge"
                " par jour fournies. Propose un ordre de priorité et un découpage réaliste ;"
                " signale les conflits d'échéances. Tu ne modifies rien toi-même : la personne"
                " valide et applique."
            ),
            keywords=(
                "planifier",
                "planning",
                "priorit",
                "organiser",
                "agenda",
                "semaine",
                "échéance",
                "retard",
                "to-do",
                "tâche",
            ),
        ),
        AgentSpec(
            name="coaching",
            label="Coaching",
            description="Fait le point sur les objectifs, le journal et les derniers bilans.",
            resources=("goals", "journal", "reviews"),
            instructions=(
                "Rôle : coach. Honnête, constructif, jamais culpabilisant. Relie tes remarques"
                " aux objectifs, aux entrées de journal et aux bilans fournis ; distingue les"
                " faits de tes hypothèses ; propose au plus trois pistes concrètes."
            ),
            keywords=(
                "motivation",
                "objectif",
                "progrès",
                "coach",
                "bilan",
                "habitude",
                "découragé",
                "fatigué",
                "stress",
            ),
        ),
        AgentSpec(
            name="memory",
            label="Mémoire",
            description="Retrouve ce que LifeOS sait, avec dates et origines.",
            resources=("facts", "memories"),
            instructions=(
                "Rôle : agent mémoire. Réponds uniquement à partir des éléments de mémoire"
                " fournis, en citant leur date et leur origine (déclaré, confirmé, extrait,"
                " inféré). Si l'information manque ou se contredit, dis-le."
            ),
            keywords=(
                "souviens",
                "rappelle-moi",
                "qu'est-ce que je",
                "quand ai-je",
                "mémoire",
                "j'avais dit",
                "sais-tu",
            ),
        ),
    )
}


def suggest_agent(text: str) -> str | None:
    """Deterministic hint (no LLM): which agent could help with this message."""
    lowered = text.lower()
    best, best_hits = None, 0
    for spec in AGENT_SPECS.values():
        hits = sum(1 for keyword in spec.keywords if keyword in lowered)
        if hits > best_hits:
            best, best_hits = spec.name, hits
    return best
