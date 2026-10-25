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
  /** Goal this task contributes to (F06). */
  goal_id: Uuid | null;
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
  last_confirmed_at: string | null;
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
  consolidated_until: string | null;
  created_at: string;
}

export interface Message {
  id: Uuid;
  conversation_id: Uuid;
  role: "user" | "assistant";
  content: string;
  status: string;
  model: string | null;
  /** Specialised agent that wrote an assistant message (null = general assistant). */
  agent: AgentName | null;
  /** Documents attached by the owner (their extracted text went with the message). */
  attachments?: Uuid[];
  purge_after: string | null;
  retained: boolean;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export type AgentName = "planning" | "coaching" | "memory" | "finance";

export interface SendResult {
  user_message: Message;
  assistant_message: Message | null;
  /** Degraded mode; `missing` lists the resources an agent still needs (code
   * `agent_permission_required`). */
  error: { code: string; title: string; detail: string | null; missing?: string[] } | null;
  context_items: number;
  agent: AgentName | "core";
  /** Deterministic hint: an agent that could help with this kind of request. */
  suggested_agent: AgentName | null;
}

/** Identifiers masked locally before a message leaves the server (kinds, never values). */
export type MaskKind =
  | "email"
  | "phone"
  | "iban"
  | "card"
  | "nir"
  | "secret"
  | "passport"
  | "id_number"
  | "tax_number";
export type MaskCounts = Partial<Record<MaskKind, number>>;
/** Why an attached document stays on the server. */
export type AttachmentRefusal =
  | "not_allowed"
  | "not_found"
  | "not_ready"
  | "sensitive"
  | "local_only"
  | "unreadable";

export interface AttachmentPreview {
  document_id: Uuid;
  sent: boolean;
  reason: AttachmentRefusal | null;
  sensitivity: SensitivityCategory[];
  masked: MaskCounts;
}

/** POST /v1/chat/preview: the control before sending (nothing saved, nothing sent). */
export interface SendPreview {
  masked: MaskCounts;
  attachments: AttachmentPreview[];
}

export interface ConsolidationResult {
  /** Null when nothing durable was found. */
  approval_id: Uuid | null;
  messages: number;
  proposed: number;
}

export interface AgentInfo {
  name: AgentName;
  label: string;
  description: string;
  resources: string[];
  /** Empty when the agent has read access to all its required resources in the space. */
  missing_permissions: string[];
  /** Optional resources not granted: the agent works without them, with less context. */
  missing_optional?: string[];
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

export type SearchType =
  | "tasks"
  | "journal"
  | "facts"
  | "memories"
  | "messages"
  | "goals"
  | "documents";

export interface SearchHit {
  type: "task" | "journal" | "fact" | "memory" | "message" | "goal" | "document";
  id: Uuid;
  space_id: Uuid;
  title: string;
  snippet: string;
  date: string;
  rank: number;
  parent_id: Uuid | null;
  /** "text": words match; "semantic": similar meaning only; "both". */
  match: "text" | "semantic" | "both";
}

export interface SearchResult {
  query: string;
  hits: SearchHit[];
  /** False: no local embedding model, full-text search only. */
  semantic: boolean;
}

export interface IndexStatus {
  semantic: boolean;
  model: string | null;
  indexed: number;
  total: number;
}

export interface Dashboard {
  /** Month budget of the selected space (personal by default); null without categories. */
  budget?: BudgetSnapshot | null;
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
    paused: boolean;
    billing: "free" | "paid";
    cloud_enabled_spaces: Uuid[];
    month_tokens_used: number;
    month_token_budget: number;
    requests_today: Record<LLMTier, number>;
    daily_request_caps: Record<LLMTier, number>;
    cost_month_usd: string;
  };
}

export type LLMTier = "fast" | "deep";

export interface Settings {
  display_name: string;
  email: string;
  locale: string;
  timezone: string;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
  llm_monthly_token_budget: number;
  /** Emergency stop of every cloud call. */
  llm_paused: boolean;
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

/** `POST /v1/auth/sessions/revoke-others`: sessions signed out (the current one stays). */
export interface RevokedSessions {
  revoked: number;
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
  paused: boolean;
  /** free: daily request caps (no billing account); paid: cost budgets in USD. */
  billing: "free" | "paid";
  models: Record<LLMTier, string | null>;
  /** Includes the calls still running (their quota is reserved, ADR 0010). */
  requests_today: Record<LLMTier, number>;
  /** Calls currently holding a reservation. */
  in_flight: Record<LLMTier, number>;
  /** Enforced caps: the configured ones minus `daily_request_margin`. */
  daily_request_caps: Record<LLMTier, number>;
  daily_request_margin: number;
  quota_resets_at: string;
  month_tokens_used: number;
  month_token_budget: number;
  cost_today_usd: string;
  cost_month_usd: string;
  /** Enforced budgets: the configured ones minus the `budget_margin_ratio` share. */
  daily_budget_usd: number;
  monthly_budget_usd: number;
  budget_margin_ratio: number;
  recent_calls: {
    id: Uuid;
    space_id: Uuid | null;
    purpose: string;
    provider: string;
    tier: LLMTier;
    model: string | null;
    cost_usd: string;
    input_tokens: number;
    output_tokens: number;
    redactions: number;
    outcome: string;
    error_code: string | null;
    duration_ms: number;
    created_at: string;
  }[];
}

// --- Goals (F06) -----------------------------------------------------------------------

export type GoalStatus = "active" | "paused" | "achieved" | "abandoned" | "archived";

export interface Goal {
  id: Uuid;
  space_id: Uuid;
  title: string;
  description: string | null;
  motivation: string | null;
  kind: "measurable" | "qualitative";
  metric_unit: string | null;
  /** Decimals are strings ("12.50"). */
  start_value: string | null;
  target_value: string | null;
  current_value: string | null;
  start_date: string | null;
  due_date: string | null;
  status: GoalStatus;
  review_cadence: "weekly" | "monthly";
  achieved_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface GoalProgress {
  /** 0..1, null when there is nothing to measure yet. */
  ratio: number | null;
  basis: "metric" | "milestones" | "none";
  milestones_done: number;
  milestones_total: number;
  tasks_open: number;
  tasks_done: number;
}

export interface GoalSummary extends Goal {
  progress: GoalProgress;
}

export interface Milestone {
  id: Uuid;
  title: string;
  due_date: string | null;
  done_at: string | null;
  position: number;
}

export interface Checkin {
  id: Uuid;
  value: string | null;
  note: string | null;
  created_at: string;
}

export interface GoalDetail extends GoalSummary {
  milestones: Milestone[];
  checkins: Checkin[];
}

// --- Reviews (F05) ---------------------------------------------------------------------

export type ReviewPeriod = "daily" | "weekly" | "monthly";

/** Local, deterministic facts of a period (never written by the model). */
export interface ReviewFacts {
  period: { start: string; end: string; timezone: string; computed_at: string; partial: boolean };
  /** Sections the generating principal could not read. */
  omitted: string[];
  tasks?: {
    completed: number;
    completed_titles: string[];
    created: number;
    overdue_open: number;
    overdue_titles: string[];
    due_next_7_days: number;
  };
  journal?: {
    entries: number;
    days_with_entry: number;
    mood_average: number | null;
    mood_entries: number;
  };
  [section: string]: unknown;
}

/** Written by the model from the facts; interpretations are hypotheses, not facts. */
export interface ReviewNarrative {
  summary: string;
  progress: string[];
  obstacles: string[];
  suggestions: { action: string; why: string }[];
  interpretations: string[];
  questions: string[];
}

export interface Review {
  id: Uuid;
  space_id: Uuid;
  period: ReviewPeriod;
  period_start: string;
  period_end: string;
  facts: ReviewFacts;
  narrative: ReviewNarrative | null;
  narrative_status: string;
  narrative_error: string | null;
  model: string | null;
  generated_by: string;
  generated_at: string;
}

export interface ReviewAutomation {
  space_id: Uuid;
  enabled: boolean;
  missing_permissions: string[];
  cloud_ai_enabled: boolean;
}

// --- Memory quality (F07) --------------------------------------------------------------

export interface MemoryReviewItem {
  kind: "duplicate" | "conflict" | "stale";
  reason: string;
  facts: Fact[];
  /** Cosine distance between the two facts (null: exact text match or ageing). */
  distance: number | null;
}

export interface MemoryReviewQueue {
  items: MemoryReviewItem[];
  /** False: no local model, only exact duplicates and ageing are detected. */
  semantic: boolean;
}

// --- Documents (F10) -------------------------------------------------------------------

export type DocumentKind = "image" | "pdf" | "text" | "audio";
export type DocumentPurpose = "document" | "receipt" | "attachment" | "dictation";
export type DocumentStatus = "pending" | "processing" | "ready" | "failed" | "rejected";
export type SensitivityCategory = "identity" | "health" | "bank";
export type DocumentSourceType =
  | "journal_entry"
  | "message"
  | "conversation"
  | "task"
  | "goal"
  | "transaction";

/** Local receipt reading (UC02), stored in `analysis.receipt`; never applied by itself. */
export interface ReceiptAnalysis {
  merchant: string | null;
  tx_date: string | null;
  /** Exact decimal as a string ("6.43"). */
  total: string | null;
  currency: string;
  vat_rates: string[];
  card_payment: boolean;
  confidence: number;
  warnings: string[];
  lines: { merchant: string | null; tx_date: string | null; total: string | null };
  [key: string]: unknown;
}

export interface DocumentAnalysis {
  /** Category -> human-readable reasons (never the detected values). */
  sensitivity_reasons?: Partial<Record<SensitivityCategory, string[]>>;
  receipt?: ReceiptAnalysis;
  [key: string]: unknown;
}

export interface DocumentSummary {
  id: Uuid;
  space_id: Uuid;
  title: string;
  kind: DocumentKind;
  mime: string;
  size_bytes: number;
  purpose: DocumentPurpose;
  status: DocumentStatus;
  /** Machine-readable reason of a pending (retry) or failed status. */
  error: string | null;
  sensitivity: SensitivityCategory[];
  /** « Toujours garder en local »: the owner's choice, whatever the detection finds. */
  local_only: boolean;
  /** "pdf" (text layer), "ocr", "text" (plain text file), "speech" (dictation). */
  text_source: string | null;
  text_confidence: number | null;
  pages: number | null;
  processed_at: string | null;
  text_corrected_at: string | null;
  original_available: boolean;
  /** Kept and, for a picture, re-encoded from its pixels: the server serves it. */
  original_downloadable: boolean;
  /** Purge date of the original; null while it is kept indefinitely. */
  original_until: string | null;
  keep_original: boolean;
  source_type: DocumentSourceType | null;
  source_id: Uuid | null;
  analysis: DocumentAnalysis | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface DocumentDetail extends DocumentSummary {
  extracted_text: string | null;
}

// --- Finances (F04, UC02) ----------------------------------------------------------------

/** Amounts are exact decimals serialised as strings ("12.50"): never parse them as floats
 * for arithmetic. */
export type Decimal = string;

export interface FinanceCategory {
  id: Uuid;
  space_id: Uuid;
  name: string;
  kind: "expense" | "income";
  monthly_budget: Decimal | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface Transaction {
  id: Uuid;
  space_id: Uuid;
  tx_date: string;
  amount: Decimal;
  direction: "expense" | "income";
  currency: string;
  merchant: string | null;
  label: string | null;
  notes: string | null;
  category_id: Uuid | null;
  /** False: chosen by a learned rule (correctable); true: by the owner. */
  category_confirmed: boolean;
  source_type: "manual" | "receipt" | "import";
  document_id: Uuid | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ReceiptProposal {
  document_id: Uuid;
  space_id: Uuid;
  title: string;
  merchant: string | null;
  tx_date: string | null;
  total: Decimal | null;
  currency: string;
  vat_rates: string[];
  card_payment: boolean;
  confidence: number;
  warnings: string[];
  lines: { merchant: string | null; tx_date: string | null; total: string | null };
  suggested_category: { id: Uuid; name: string; source: "rule" | "keyword" } | null;
  /** Transactions already recorded from this receipt. */
  recorded_transaction_ids: Uuid[];
}

export interface CategorySummary {
  category_id: Uuid;
  name: string;
  kind: "expense" | "income";
  archived: boolean;
  budget: Decimal | null;
  spent: Decimal;
  received: Decimal;
  remaining: Decimal | null;
  ratio: number | null;
  over_budget: boolean;
  transactions: number;
}

export interface BudgetSummary {
  space_id: Uuid;
  month: string;
  currency: string;
  period_start: string;
  period_end: string;
  categories: CategorySummary[];
  uncategorised: { expenses: Decimal; income: Decimal; transactions: number };
  totals: {
    expenses: Decimal;
    income: Decimal;
    balance: Decimal;
    budget: Decimal;
    budgeted_spent: Decimal;
    budget_remaining: Decimal;
  };
  other_currency_transactions: number;
}

export interface BudgetSnapshot {
  space_id: Uuid;
  month: string;
  currency: string;
  spent: Decimal;
  budget: Decimal;
  alerts: { category_id: Uuid; name: string; budget: Decimal; spent: Decimal; ratio: number }[];
}

// --- Connected accounts (ADR 0012) --------------------------------------------------------

export type ConnectorCapability = "calendar.read" | "calendar.write" | "mail.read" | "mail.send";
export type ConnectorStatus = "active" | "reauth_required" | "revoked";

export interface ConnectorProvider {
  name: string;
  label: string;
  capabilities: ConnectorCapability[];
}

/** The owner's consent: this account may serve this capability in this space. */
export interface ConnectorGrant {
  id: Uuid;
  space_id: Uuid;
  capability: ConnectorCapability;
  created_at: string;
  expires_at: string | null;
}

/** A calendar or a mail label mapped to a space. */
export interface ConnectorSource {
  id: Uuid;
  space_id: Uuid;
  kind: "calendar" | "mail_label";
  name: string;
  enabled: boolean;
  last_synced_at: string | null;
  last_error: string | null;
}

export interface ConnectorAccount {
  id: Uuid;
  provider: string;
  email: string | null;
  status: ConnectorStatus;
  /** What the provider actually granted (the owner may untick accesses there). */
  capabilities: ConnectorCapability[];
  grants: ConnectorGrant[];
  sources: ConnectorSource[];
  connected_at: string;
  revoked_at: string | null;
  last_error: string | null;
}

export interface OAuthStart {
  authorization_url: string;
}

export interface OAuthResult {
  account: ConnectorAccount;
  space_id: Uuid;
  requested: ConnectorCapability[];
  granted: ConnectorCapability[];
  missing: ConnectorCapability[];
  /** Reconnection: tokens restored, consents per space unchanged (none added). */
  reconnected: boolean;
}

export interface DisconnectResult {
  account_id: Uuid;
  /**
   * "failed": the provider could not revoke; "manual_required": the tokens were already wiped
   * (reconnection needed), nothing to revoke with. Either way, the owner should withdraw
   * LifeOS's access in the account's own settings.
   */
  remote_revocation: "done" | "failed" | "manual_required" | "not_needed";
  purged: boolean;
}

// --- Voice (UC01) ------------------------------------------------------------------------

export interface VoiceStatus {
  /** False: dictation is disabled on this server. */
  transcription: boolean;
  max_seconds: number;
}

export interface Dictation {
  id: Uuid;
  space_id: Uuid;
  status: "pending" | "processing" | "ready" | "failed";
  error: string | null;
  /** The transcript once ready ("" when nothing was said). */
  text: string | null;
  duration: number | null;
  expires_at: string;
  created_at: string;
}

/** Something saved from a journal entry or a conversation, listed before deleting it. */
export interface DerivedItem {
  type: "fact" | "task" | "memory";
  id: Uuid;
  /** Statement or title. */
  text: string;
}

// --- Calendar (task 14) ------------------------------------------------------------------

/** A calendar of a connected account, read live at the provider. */
export interface RemoteCalendar {
  provider_ref: string;
  name: string;
  primary: boolean;
  read_only: boolean;
  /** Already followed: the source and the space it feeds. */
  source_id: Uuid | null;
  space_id: Uuid | null;
}

/** An occurrence synced from a followed calendar. Its texts come from third parties. */
export interface CalendarEvent {
  id: Uuid;
  space_id: Uuid;
  source_id: Uuid;
  source_name: string;
  synced_at: string | null;
  summary: string;
  description: string | null;
  location: string | null;
  all_day: boolean;
  /** Timed: ISO 8601 with the offset of the owner's time zone. */
  starts_at: string | null;
  ends_at: string | null;
  /** All-day: dates, `end_date` exclusive. */
  start_date: string | null;
  end_date: string | null;
  timezone: string | null;
  status: "confirmed" | "tentative";
  busy: boolean;
  recurring: boolean;
  response: string | null;
}

export interface CalendarEvents {
  items: CalendarEvent[];
  /** The owner's time zone, in which the times above are written. */
  timezone: string;
}
