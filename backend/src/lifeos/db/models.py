"""Imports every model so that `Base.metadata` is complete (Alembic, tests)."""

from lifeos.core.idempotency import IdempotencyKey
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.chat.models import Conversation, Message
from lifeos.modules.connectors.models import (
    ConnectorAccount,
    ConnectorGrant,
    ConnectorSource,
    OAuthState,
)
from lifeos.modules.documents.models import Document
from lifeos.modules.finance.models import CategoryRule, FinanceCategory, Transaction
from lifeos.modules.goals.models import Goal, GoalCheckin, GoalMilestone
from lifeos.modules.guardian.models import AgentPermission, Approval, LLMCall
from lifeos.modules.identity.models import (
    AuthChallenge,
    EnrollmentToken,
    PasskeyCredential,
    Space,
    User,
    UserSession,
)
from lifeos.modules.journal.models import JournalEntry
from lifeos.modules.memory.models import (
    Fact,
    FactPairDismissal,
    Memory,
    MemoryRevision,
    memory_facts,
)
from lifeos.modules.notifications.models import Notification
from lifeos.modules.retrieval.models import Embedding
from lifeos.modules.reviews.models import Review
from lifeos.modules.tasks.models import Task

__all__ = [
    "AgentPermission",
    "Approval",
    "AuditEvent",
    "AuthChallenge",
    "CategoryRule",
    "ConnectorAccount",
    "ConnectorGrant",
    "ConnectorSource",
    "Conversation",
    "Document",
    "Embedding",
    "EnrollmentToken",
    "Fact",
    "FactPairDismissal",
    "FinanceCategory",
    "Goal",
    "GoalCheckin",
    "GoalMilestone",
    "IdempotencyKey",
    "JournalEntry",
    "LLMCall",
    "Memory",
    "MemoryRevision",
    "Message",
    "Notification",
    "OAuthState",
    "PasskeyCredential",
    "Review",
    "Space",
    "Task",
    "Transaction",
    "User",
    "UserSession",
    "memory_facts",
]
