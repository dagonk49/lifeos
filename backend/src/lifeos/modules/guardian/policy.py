"""Autonomy policy (cahier des charges §5). Deterministic, independent of the LLM.

Instructions found in an e-mail, a web page or an imported document are never an
authorisation: only the owner's explicit decisions are.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from lifeos.modules.guardian.principal import Principal

if TYPE_CHECKING:
    from lifeos.modules.guardian.models import Approval


class Level(StrEnum):
    AUTOMATIC = "automatic"  # logged and correctable
    RULE = "rule"  # under an explicit, revocable standing permission
    APPROVAL = "approval"  # exact preview + one-off consent
    REINFORCED = "reinforced"  # adapted authentication; denied by default


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    user_level: Level
    # None: agents may never perform or propose this action.
    agent_level: Level | None
    resource: str | None = None
    access: str = "write"
    # For APPROVAL actions: "reinforced" also requires a recent passkey login to approve
    # (exact preview + one-off consent + adapted authentication).
    approval_level: Literal["approval", "reinforced"] = "approval"


_SPECS = [
    ActionSpec("task.create", "Créer une tâche", Level.AUTOMATIC, Level.RULE, "tasks"),
    ActionSpec("task.update", "Modifier une tâche", Level.AUTOMATIC, Level.RULE, "tasks"),
    ActionSpec("task.delete", "Supprimer une tâche", Level.AUTOMATIC, Level.APPROVAL, "tasks"),
    ActionSpec("journal.write", "Écrire dans le journal", Level.AUTOMATIC, None, "journal"),
    ActionSpec(
        "journal.apply_proposals",
        "Enregistrer les tâches et faits proposés à partir du journal",
        Level.AUTOMATIC,
        Level.APPROVAL,
        "journal",
    ),
    ActionSpec(
        "chat.apply_proposals",
        "Enregistrer les tâches et faits tirés d'une conversation",
        Level.AUTOMATIC,
        Level.APPROVAL,
        "facts",
    ),
    ActionSpec(
        "fact.write", "Enregistrer ou corriger un fait", Level.AUTOMATIC, Level.APPROVAL, "facts"
    ),
    ActionSpec(
        "memory.write", "Créer ou modifier un souvenir", Level.AUTOMATIC, Level.APPROVAL, "memories"
    ),
    ActionSpec("memory.delete", "Supprimer un souvenir", Level.AUTOMATIC, None, "memories"),
    ActionSpec(
        "goal.write", "Créer ou modifier un objectif", Level.AUTOMATIC, Level.APPROVAL, "goals"
    ),
    ActionSpec(
        "document.write",
        "Ajouter ou corriger un document",
        Level.AUTOMATIC,
        Level.APPROVAL,
        "documents",
    ),
    ActionSpec("document.delete", "Supprimer un document", Level.AUTOMATIC, None, "documents"),
    # Lifting « Toujours garder en local »: the document may go to the assistant again.
    ActionSpec(
        "document.allow_cloud",
        "Autoriser l'envoi à l'IA d'un document gardé en local",
        Level.REINFORCED,
        None,
    ),
    # "Classer une dépense" is automatic (cahier des charges §5), logged and correctable.
    ActionSpec(
        "finance.write",
        "Enregistrer ou classer une dépense",
        Level.AUTOMATIC,
        Level.APPROVAL,
        "finances",
    ),
    # Bank statement imports (task 25): only the owner, after checking the preview.
    ActionSpec(
        "finance.import", "Importer des opérations bancaires", Level.AUTOMATIC, None, "finances"
    ),
    ActionSpec(
        "finance.import.undo",
        "Annuler un import d'opérations bancaires",
        Level.AUTOMATIC,
        None,
        "finances",
    ),
    # Data access is checked section by section with the principal's own read rights.
    ActionSpec("review.generate", "Établir un bilan", Level.AUTOMATIC, Level.RULE),
    ActionSpec("llm.cloud_call", "Appeler le LLM cloud", Level.AUTOMATIC, Level.RULE, None, "read"),
    # E-mail sending (task 18, ADR 0018): every e-mail is approved by the owner from its
    # exact content; a new recipient (never written to from LifeOS in this space) or a
    # sensitive attachment also requires a recent passkey login. Agents only propose.
    ActionSpec("email.send", "Envoyer un e-mail", Level.APPROVAL, Level.APPROVAL, "email"),
    ActionSpec(
        "email.send_reinforced",
        "Envoyer un e-mail à un nouveau destinataire ou avec une pièce jointe sensible",
        Level.APPROVAL,
        Level.APPROVAL,
        "email",
        approval_level="reinforced",
    ),
    # Calendar writes (task 16): every write is approved by the owner, even an event of
    # their own (owner's decision); a shared event (other attendees, someone else's event
    # or calendar) also requires a recent passkey login. Agents only propose.
    ActionSpec(
        "calendar.create_event",
        "Ajouter un événement à l'agenda",
        Level.APPROVAL,
        Level.APPROVAL,
        "calendar",
    ),
    ActionSpec(
        "calendar.move_event",
        "Déplacer un événement de l'agenda",
        Level.APPROVAL,
        Level.APPROVAL,
        "calendar",
    ),
    ActionSpec(
        "calendar.cancel_event",
        "Annuler un événement de l'agenda",
        Level.APPROVAL,
        Level.APPROVAL,
        "calendar",
    ),
    ActionSpec(
        "calendar.update_shared_event",
        "Modifier un événement partagé",
        Level.APPROVAL,
        Level.APPROVAL,
        "calendar",
        approval_level="reinforced",
    ),
    ActionSpec("document.share", "Partager un document", Level.APPROVAL, Level.APPROVAL),
    ActionSpec("payment.execute", "Effectuer un paiement", Level.REINFORCED, None),
    ActionSpec("permission.grant", "Accorder une permission à un agent", Level.REINFORCED, None),
    ActionSpec("space.cloud_consent", "Modifier le consentement cloud", Level.REINFORCED, None),
    # F09: critical alerts may go through quiet hours only with this explicit consent;
    # withdrawing it is immediate (settings/router.py).
    ActionSpec(
        "notification.critical_bypass",
        "Autoriser les alertes critiques pendant la tranquillité",
        Level.REINFORCED,
        None,
    ),
    ActionSpec("data.bulk_delete", "Destruction importante de données", Level.REINFORCED, None),
    # Reversibility (task 33): only the owner exports, after a recent passkey sign-in (the
    # whole of their data leaves the server: a stolen session alone is not enough).
    ActionSpec("data.export", "Exporter mes données", Level.REINFORCED, None, None, "read"),
    # Connected accounts (ADR 0012): giving access is reinforced, withdrawing it immediate.
    ActionSpec("connector.connect", "Connecter un compte externe", Level.REINFORCED, None),
    ActionSpec(
        "connector.grant", "Autoriser un compte connecté dans un espace", Level.REINFORCED, None
    ),
    ActionSpec("connector.disconnect", "Déconnecter un compte externe", Level.AUTOMATIC, None),
]

ACTIONS: dict[str, ActionSpec] = {spec.name: spec for spec in _SPECS}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str
    level: Level | None = None


def evaluate(
    principal: Principal,
    action: str,
    space_id: uuid.UUID | None,
    approval: "Approval | None" = None,
) -> Decision:
    spec = ACTIONS.get(action)
    if spec is None:
        return Decision(False, "unknown_action")
    if space_id is not None and space_id not in principal.space_ids:
        return Decision(False, "space_forbidden")

    if principal.kind == "agent":
        if spec.agent_level is None:
            return Decision(False, "forbidden_for_agents")
        level = spec.agent_level
        if spec.resource is not None and (
            space_id is None or not principal.can(space_id, spec.resource, spec.access)
        ):
            return Decision(False, "no_permission", level)
    else:
        level = spec.user_level

    if level in (Level.AUTOMATIC, Level.RULE):
        return Decision(True, "allowed", level)
    if level == Level.APPROVAL:
        if (
            approval is not None
            and approval.status == "approved"
            and approval.action == action
            and approval.user_id == principal.user_id
            and approval.space_id == space_id
        ):
            if spec.approval_level == "reinforced" and not (
                approval.level == "reinforced"
                and principal.kind == "user"
                and principal.recent_auth
            ):
                # Shared event: the approval itself must be the reinforced kind, decided
                # by the owner after a recent passkey login.
                return Decision(False, "reauthentication_required", level)
            return Decision(True, "approved", level)
        return Decision(False, "approval_required", level)
    # REINFORCED: only the owner, with a recent passkey authentication.
    if principal.kind == "user" and principal.recent_auth:
        return Decision(True, "allowed", level)
    return Decision(False, "reauthentication_required", level)
