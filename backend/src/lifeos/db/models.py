"""Imports every model so that `Base.metadata` is complete (Alembic, tests)."""

from lifeos.core.idempotency import IdempotencyKey
from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.calendar.models import CalendarEvent
from lifeos.modules.chat.models import Conversation, Message
from lifeos.modules.connectors.models import (
    ConnectorAccount,
    ConnectorGrant,
    ConnectorSource,
    OAuthState,
)
from lifeos.modules.dashboard.models import DashboardPreference
from lifeos.modules.documents.models import Document
from lifeos.modules.finance.models import (
    CategoryRule,
    FinanceCategory,
    FinanceImport,
    FinanceImportRow,
    FinanceRecurring,
    Transaction,
)
from lifeos.modules.goals.models import Goal, GoalCheckin, GoalMilestone
from lifeos.modules.guardian.models import AgentPermission, Approval, ExternalOperation, LLMCall
from lifeos.modules.identity.models import (
    AuthChallenge,
    EnrollmentToken,
    PasskeyCredential,
    Space,
    User,
    UserSession,
)
from lifeos.modules.journal.models import JournalEntry
from lifeos.modules.mail.models import MailDraft, MailMessage
from lifeos.modules.memory.models import (
    Fact,
    FactPairDismissal,
    Memory,
    MemoryRevision,
    memory_facts,
)
from lifeos.modules.notifications.models import Notification, NotificationPreference
from lifeos.modules.retrieval.models import Embedding
from lifeos.modules.reviews.models import Review
from lifeos.modules.tasks.models import Task, TaskDependency

__all__ = [
    "AgentPermission",
    "Approval",
    "AuditEvent",
    "AuthChallenge",
    "CalendarEvent",
    "CategoryRule",
    "ConnectorAccount",
    "ConnectorGrant",
    "ConnectorSource",
    "Conversation",
    "DashboardPreference",
    "Document",
    "Embedding",
    "EnrollmentToken",
    "ExternalOperation",
    "Fact",
    "FactPairDismissal",
    "FinanceCategory",
    "FinanceImport",
    "FinanceImportRow",
    "FinanceRecurring",
    "Goal",
    "GoalCheckin",
    "GoalMilestone",
    "IdempotencyKey",
    "JournalEntry",
    "LLMCall",
    "MailDraft",
    "MailMessage",
    "Memory",
    "MemoryRevision",
    "Message",
    "Notification",
    "NotificationPreference",
    "OAuthState",
    "PasskeyCredential",
    "Review",
    "Space",
    "Task",
    "TaskDependency",
    "Transaction",
    "User",
    "UserSession",
    "memory_facts",
]
