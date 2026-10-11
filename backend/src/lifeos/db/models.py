"""Imports every model so that `Base.metadata` is complete (Alembic, tests)."""

from lifeos.core.idempotency import IdempotencyKey
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.chat.models import Conversation, Message
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
from lifeos.modules.memory.models import Fact, Memory, MemoryRevision, memory_facts
from lifeos.modules.notifications.models import Notification
from lifeos.modules.retrieval.models import Embedding
from lifeos.modules.reviews.models import Review
from lifeos.modules.tasks.models import Task

__all__ = [
    "AgentPermission",
    "Approval",
    "AuditEvent",
    "AuthChallenge",
    "Conversation",
    "Embedding",
    "EnrollmentToken",
    "Fact",
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
    "PasskeyCredential",
    "Review",
    "Space",
    "Task",
    "User",
    "UserSession",
    "memory_facts",
]
