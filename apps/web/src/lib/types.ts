// API types (mirror of the backend Pydantic schemas).

export type Uuid = string;

export interface Problem {
  type?: string;
  title: string;
  status: number;
  code: string;
  detail?: string;
  request_id?: string;
  errors?: { loc: (string | number)[]; msg: string; type: string }[];
  [key: string]: unknown;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Space {
  id: Uuid;
  kind: "personal" | "professional";
  name: string;
  cloud_ai_enabled?: boolean;
}

export interface User {
  id: Uuid;
  email: string;
  display_name: string;
  locale: string;
  timezone: string;
}

export interface Me {
  user: User;
  spaces: Space[];
  session: { id: Uuid; authenticated_at: string; expires_at: string };
}

export type TaskStatus = "todo" | "in_progress" | "done" | "cancelled";
export type TaskPriority = "low" | "normal" | "high";

export interface Task {
  id: Uuid;
  space_id: Uuid;
  title: string;
  notes: string | null;
  status: TaskStatus;
  priority: TaskPriority;
  due_at: string | null;
  remind_at: string | null;
  completed_at: string | null;
  source_type: string;
  source_id: Uuid | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface JournalEntry {
  id: Uuid;
  space_id: Uuid;
  entry_date: string;
  content: string;
  mood: number | null;
  analyzed_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export type Origin = "user_declared" | "user_confirmed" | "extracted" | "inferred";
export type FactKind = "fact" | "event" | "preference" | "decision";

export interface Fact {
  id: Uuid;
  space_id: Uuid;
  kind: FactKind;
  statement: string;
  origin: Origin;
  confidence: string;
  status: "active" | "superseded" | "retracted";
  occurred_at: string | null;
  source_type: string;
  source_id: Uuid | null;
  supersedes_id: Uuid | null;
  created_at: string;
  updated_at: string;
}

export interface Memory {
  id: Uuid;
  space_id: Uuid;
  title: string;
  content: string;
  importance: number;
  protected: boolean;
  origin: Origin;
  source_type: string;
  source_id: Uuid | null;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface MemoryRevision {
  revision: number;
  title: string;
  content: string;
  importance: number;
  protected: boolean;
  change_note: string | null;
  changed_at: string;
}

export interface Conversation {
  id: Uuid;
  space_id: Uuid;
  title: string;
  last_message_at: string | null;
  created_at: string;
}

export interface Message {
  id: Uuid;
  conversation_id: Uuid;
  role: "user" | "assistant";
  content: string;
  status: string;
  model: string | null;
  purge_after: string | null;
  retained: boolean;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export interface SendResult {
  user_message: Message;
  assistant_message: Message | null;
  error: { code: string; title: string; detail: string | null } | null;
  context_items: number;
}

export interface Approval {
  id: Uuid;
  space_id: Uuid;
  action: string;
  description: string;
  params: Record<string, unknown>;
  preview: { summary?: string; items?: { type: string; text: string; date: string | null }[] } & Record<string, unknown>;
  level: "approval" | "reinforced";
  status: "pending" | "approved" | "rejected" | "expired" | "executed" | "failed";
  requested_by: string;
  created_at: string;
  expires_at: string;
  decided_at: string | null;
  executed_at: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface NotificationItem {
  id: Uuid;
  space_id: Uuid | null;
  category: "critical" | "important" | "suggestion";
  title: string;
  body: string | null;
  status: string;
  source_type: string | null;
  source_id: Uuid | null;
  deliver_after: string;
  created_at: string;
}

export interface SearchHit {
  type: "task" | "journal" | "fact" | "memory" | "message";
  id: Uuid;
  space_id: Uuid;
  title: string;
  snippet: string;
  date: string;
  rank: number;
  parent_id: Uuid | null;
}

export interface Dashboard {
  generated_at: string;
  timezone: string;
  today: string;
  tasks_overdue: Task[];
  tasks_today: Task[];
  tasks_upcoming: Task[];
  tasks_without_due: number;
  journal_today: JournalEntry[];
  pending_approvals: number;
  unread_notifications: number;
  memory: { facts: number; memories: number; protected: number };
  assistant: {
    provider: string;
    enabled: boolean;
    cloud_enabled_spaces: Uuid[];
    month_tokens_used: number;
    month_token_budget: number;
  };
}

export interface Settings {
  display_name: string;
  email: string;
  locale: string;
  timezone: string;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
  llm_monthly_token_budget: number;
}

export interface SessionInfo {
  id: Uuid;
  created_at: string;
  authenticated_at: string;
  last_seen_at: string;
  expires_at: string;
  user_agent: string | null;
  ip: string | null;
  current: boolean;
}

export interface Passkey {
  id: Uuid;
  label: string;
  created_at: string;
  last_used_at: string | null;
  backup_eligible: boolean;
  backup_state: boolean;
}

export interface AgentPermission {
  id: Uuid;
  agent: string;
  space_id: Uuid;
  resource: string;
  access: string;
  purpose: string;
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface AuditEvent {
  id: number;
  occurred_at: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  outcome: "success" | "denied" | "failure";
  target_type: string | null;
  target_id: string | null;
  space_id: Uuid | null;
  details: Record<string, unknown>;
}

export interface LLMUsage {
  provider: string;
  enabled: boolean;
  month_tokens_used: number;
  month_token_budget: number;
  recent_calls: {
    id: Uuid;
    purpose: string;
    model: string | null;
    input_tokens: number;
    output_tokens: number;
    redactions: number;
    outcome: string;
    error_code: string | null;
    duration_ms: number;
    created_at: string;
  }[];
}
