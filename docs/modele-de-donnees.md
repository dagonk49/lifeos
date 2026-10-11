# Modèle de données

Base : PostgreSQL (≥ 16) avec les extensions `vector` (pgvector) et `unaccent`. Les migrations
sont gérées par Alembic (`backend/migrations`). Ce document décrit les règles transverses, les
tables du socle (étape 1) et du MVP (étape 2), puis les tables prévues ensuite.

## 1. Règles transverses

| Règle | Détail |
|---|---|
| Identifiants | `id uuid` généré par l'application. Exception : `audit_events.id` (`bigint` identity, ordre total). |
| Propriétaire | Toute ligne métier porte `user_id` (FK `users`, `ON DELETE CASCADE`) : aucun mélange de données entre utilisateurs. |
| Espace | Toute ligne métier porte `space_id` (FK `spaces`). Les services filtrent **toujours** par les espaces autorisés du `Principal`. |
| Horodatage | `timestamptz` en UTC (`created_at`, `updated_at`). Les dates « calendaires » (journal) sont des `date` locales au fuseau de l'utilisateur. |
| Énumérations | `text` + contrainte `CHECK` (migrations plus simples que les types `ENUM`). |
| Montants | `numeric(14,2)` + `currency char(3)` ISO 4217. **Jamais de flottants.** |
| Provenance | `source_type` + `source_id` : d'où vient la donnée (message, entrée de journal, saisie manuelle…). |
| Origine de la connaissance | `origin` ∈ `user_declared` (déclaré), `user_confirmed` (proposé puis validé), `extracted` (extrait d'une source sans validation), `inferred` (inférence du modèle). Sépare faits vérifiables, inférences et préférences déclarées. |
| Corrections | Un fait corrigé n'est pas écrasé : nouvelle ligne avec `supersedes_id`, l'ancienne passe à `superseded`. Les souvenirs ont une table de révisions. |
| Données brutes | `purge_after timestamptz` : date de purge automatique ; `NULL` = conservation explicite. |
| Recherche plein texte | Colonne générée `search_vector tsvector` avec la configuration `lifeos_fr` (français + `unaccent`), index GIN. |
| Concurrence | `version integer` sur les entités éditables depuis plusieurs appareils (contrôle optimiste). |

Défense en profondeur prévue : politiques Row-Level Security PostgreSQL par `user_id` et
`space_id` (non activées au MVP, le filtrage est applicatif et couvert par des tests).

## 2. Socle — identité, sessions, audit (étape 1)

### `users`
| Colonne | Type | Notes |
|---|---|---|
| id | uuid PK | |
| email | text unique | normalisé en minuscules |
| display_name | text | |
| role | text | `owner` (V1 : un seul propriétaire) |
| locale | text | défaut `fr-FR` |
| timezone | text | défaut `Europe/Paris` (nom IANA) |
| disabled_at | timestamptz null | compte désactivé |
| created_at, updated_at | timestamptz | |

Ajouts MVP : `quiet_hours_start time null`, `quiet_hours_end time null`,
`llm_monthly_token_budget integer` (seuil d'arrêt).

### `spaces`
| Colonne | Type | Notes |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK | |
| kind | text | `personal` \| `professional` |
| name | text | unique par utilisateur |
| created_at, updated_at | timestamptz | |

Ajout MVP : `cloud_ai_enabled boolean default false` — consentement explicite à l'envoi
d'extraits de cet espace au fournisseur LLM.

### `passkey_credentials`
| Colonne | Type | Notes |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK | |
| credential_id | bytea unique | identifiant WebAuthn |
| public_key | bytea | clé publique COSE |
| sign_count | bigint | détection de clonage |
| transports | jsonb | indices du navigateur |
| aaguid | text | modèle d'authentificateur |
| backup_eligible, backup_state | boolean | passkey synchronisée ou non |
| label | text | nom donné par l'utilisateur (« Pixel », « PC bureau ») |
| created_at, last_used_at, revoked_at | timestamptz | |

### `enrollment_tokens`
Jetons à usage unique émis par la CLI serveur pour enregistrer une passkey.
`id`, `user_id`, `token_hash bytea unique` (SHA-256, le jeton clair n'est jamais stocké),
`purpose` (`bootstrap` \| `recovery`), `expires_at`, `used_at`, `created_at`.

### `auth_challenges`
Défis WebAuthn côté serveur (anti-rejeu). `id`, `purpose` (`registration` \|
`authentication`), `user_id null`, `enrollment_token_id null`, `challenge bytea`,
`expires_at` (5 min), `consumed_at`, `created_at`.

### `user_sessions`
| Colonne | Type | Notes |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK | |
| token_hash | bytea unique | SHA-256 du jeton de cookie |
| credential_id | uuid FK null | passkey utilisée (`ON DELETE SET NULL`) |
| authenticated_at | timestamptz | pour la réauthentification récente |
| last_seen_at | timestamptz | mis à jour au plus une fois par minute |
| expires_at | timestamptz | expiration absolue (30 j) |
| user_agent | text | tronqué à 512 caractères |
| ip | inet null | dernière adresse vue |
| revoked_at, revoked_reason | | |
| created_at | timestamptz | |

### `audit_events` (append-only)
| Colonne | Type | Notes |
|---|---|---|
| id | bigint identity PK | |
| occurred_at | timestamptz | |
| user_id | uuid null | propriétaire des données concernées (pas de FK : l'audit survit) |
| actor_type | text | `user` \| `agent` \| `system` \| `anonymous` |
| actor_id | text null | id utilisateur, nom d'agent ou de tâche |
| action | text | ex. `auth.login`, `task.create`, `memory.purge` |
| outcome | text | `success` \| `denied` \| `failure` |
| target_type, target_id | text null | |
| space_id | uuid null | |
| request_id | text null | corrélation avec les logs |
| details | jsonb | **identifiants, compteurs et codes uniquement, jamais de contenu** |

Un trigger refuse tout `UPDATE` et tout `DELETE`, sauf `DELETE` effectué par la tâche de purge
de rétention (variable de transaction `lifeos.audit_purge = on`).

## 3. MVP (étape 2)

### Conversation
- **`conversations`** : `id`, `user_id`, `space_id`, `title`, `last_message_at`, `created_at`,
  `updated_at`.
- **`messages`** : `id`, `conversation_id` (FK cascade), `user_id`, `space_id`, `role`
  (`user` \| `assistant`), `content`, `status` (`ok` \| `error`), `model`, `input_tokens`,
  `output_tokens`, `purge_after` (création + 90 j ; `NULL` si conservé explicitement),
  `created_at`, `search_vector`.

### Tâches et rappels
- **`tasks`** : `id`, `user_id`, `space_id`, `title`, `notes`, `status` (`todo` \|
  `in_progress` \| `done` \| `cancelled`), `priority` (`low` \| `normal` \| `high`), `due_at`,
  `remind_at`, `reminded_at`, `completed_at`, `source_type`, `source_id`, `version`,
  `created_at`, `updated_at`, `search_vector`.

### Journal
- **`journal_entries`** : `id`, `user_id`, `space_id`, `entry_date date`, `content`,
  `mood smallint null` (1–5, uniquement si partagé volontairement), `analyzed_at`, `version`,
  `created_at`, `updated_at`, `search_vector`.

### Mémoire Premium
- **`facts`** : `id`, `user_id`, `space_id`, `kind` (`fact` \| `event` \| `preference` \|
  `decision`), `statement`, `origin`, `confidence numeric(3,2)`, `status` (`active` \|
  `superseded` \| `retracted`), `occurred_at null` (date de l'événement), `source_type`,
  `source_id`, `supersedes_id`, `created_at`, `updated_at`, `search_vector`.
- **`memories`** : `id`, `user_id`, `space_id`, `title`, `content`, `importance` (1–5),
  `protected boolean` (jamais purgé automatiquement), `origin`, `revision`, `created_at`,
  `updated_at`, `search_vector`.
- **`memory_revisions`** : `memory_id`, `revision`, `title`, `content`, `importance`,
  `protected`, `changed_at`, `change_note` — historique de correction.
- **`memory_facts`** : `memory_id`, `fact_id` — liens souvenir ↔ faits sources.

### Guardian
- **`agent_permissions`** : `id`, `user_id`, `agent` (`core`, `planning`, `memory`,
  `coaching`, `finance`, `projects`, `documents`), `space_id`, `resource` (`tasks`, `journal`,
  `facts`, `memories`, `messages`), `access` (`read` \| `write`), `purpose`, `expires_at`,
  `revoked_at`, `created_at`. Permissions par source (espace + ressource), finalité, durée et
  agent ; révocables immédiatement.
- **`approvals`** : `id`, `user_id`, `space_id`, `action`, `params jsonb` (paramètres
  **exacts** qui seront exécutés), `preview jsonb`, `level` (`approval` \| `reinforced`),
  `status` (`pending` \| `approved` \| `rejected` \| `expired` \| `executed` \| `failed`),
  `requested_by`, `expires_at`, `decided_at`, `executed_at`, `result jsonb`, `error`,
  `created_at`.
- **`llm_calls`** : `id`, `user_id`, `space_id`, `purpose`, `provider`, `model`,
  `input_tokens`, `output_tokens`, `redactions` (nombre de masquages), `outcome` (`ok` \|
  `denied` \| `budget_exceeded` \| `error`), `duration_ms`, `created_at`. **Aucun contenu.**

### Notifications
- **`notifications`** : `id`, `user_id`, `space_id null`, `category` (`critical` \|
  `important` \| `suggestion`), `title`, `body`, `dedup_key` (unique par utilisateur),
  `deliver_after` (repoussé à la fin des plages de tranquillité sauf `critical`), `status`
  (`pending` \| `read` \| `dismissed`), `source_type`, `source_id`, `read_at`, `created_at`.

### Idempotence
- **`idempotency_keys`** : `user_id`, `key`, `method`, `path`, `request_hash`,
  `response_status`, `response_body jsonb`, `created_at`, `expires_at` (24 h). Clé primaire
  `(user_id, key)`.

## 4. Prévu ensuite

| Table | Étape | Notes |
|---|---|---|
| `embeddings` | 3 | `object_type`, `object_id`, `model`, `model_version`, `vector`, `space_id` ; supprimé avec l'objet source |
| `goals`, `goal_milestones`, `reviews` | 3 | objectifs, jalons, bilans hebdomadaires/mensuels |
| `documents` | 4 | empreinte, MIME, emplacement, classification, `retain_until`, provenance |
| `connectors`, `connector_grants` | 5 | OAuth, scopes, expiration, révocation |
| `financial_transactions`, `budgets` | 1.5 (produit) | montants exacts, devise, catégorie corrigeable, source |
| `push_devices` | 5 | jetons de notification mobile |
