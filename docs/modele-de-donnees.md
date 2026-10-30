# Modèle de données

Base : PostgreSQL (≥ 16) avec les extensions `vector` (pgvector) et `unaccent`. Les migrations
sont gérées par Alembic (`backend/migrations`). Ce document décrit les règles transverses, les
tables du socle (étape 1), du MVP (étape 2) et de l'étape 3, puis les tables prévues ensuite.

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

Ajout V1 (tâche 21) : `critical_bypass_quiet_hours boolean` (défaut `false`) : permission
explicite et révocable pour les alertes critiques de passer pendant les plages de
tranquillité. L'accorder est une opération renforcée (passkey récente), auditée.

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
  `remind_at`, `reminded_at`, `completed_at`, `source_type`, `source_id`, `tags text[]`,
  `version`, `created_at`, `updated_at`, `search_vector`.
- **`tasks.tags`** (classification F03, tâche 22) : étiquettes libres, 10 au plus, de
  40 caractères au plus chacune. L'API les normalise : sans `#` initial, espaces réduits,
  minuscules, sans doublon et sans virgule. Index GIN `ix_tasks_tags` pour le filtre
  `?tag=` (`@>`).
- **`task_dependencies`** (tâche 22) : `task_id` (FK `tasks` cascade), `depends_on_id` (FK
  `tasks` cascade, indexé), `user_id`, `space_id`, `created_at`. Clé primaire
  `(task_id, depends_on_id)`. Contrainte `ck_task_dependencies_not_self` : une tâche ne
  dépend pas d'elle-même.
  - `task_id` attend `depends_on_id`. Les deux tâches sont du même espace.
  - Le graphe reste **sans cycle**. Le contrôle se fait sous un verrou consultatif par
    utilisateur (`pg_advisory_xact_lock(TASK_DEPS_LOCK_CLASS, hashtext(user_id))`), par un
    parcours récursif des liens.
  - Une tâche ouverte est **bloquée** tant qu'elle attend une tâche ouverte (`todo` ou
    `in_progress`). Une dépendance terminée **ou annulée** ne bloque plus.
  - Supprimer l'une des deux tâches supprime le lien.
  - Il n'y a pas de colonne « bloquée » : l'état se calcule à la lecture (`blocked`,
    `blocked_by`).
- Les tâches récurrentes ne sont pas encore stockées (voir [ADR 0013](adr/0013-taches-recurrentes.md)).

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
  `protected boolean` (jamais purgé automatiquement), `origin`, `source_type`, `source_id`,
  `revision`, `created_at`, `updated_at`, `search_vector`.
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
  **exacts** qui seront exécutés), `params_sha256` (empreinte du JSON canonique de
  `params`, migration 0017 : d'autres paramètres ne s'exécutent pas ; nullable pour que la
  version précédente puisse encore écrire, une empreinte absente est refusée), `preview
  jsonb`, `target null` (objet visé, ex. `calendar_event:<id>`, `mail_draft:<id>` : une proposition par objet,
  invalidée quand il change ; index partiel sur les approbations en attente), `level`
  (`approval` \| `reinforced`), `status` (`pending` \| `approved` \| `rejected` \|
  `expired` \| `executed` \| `failed` \| `invalidated`), `requested_by`, `expires_at`,
  `decided_at`, `executed_at`, `result jsonb`, `error` (code : raison de l'échec ou de
  l'invalidation), `created_at`.
- **`external_operations`** (migration 0017, ADR 0016) : une écriture chez un fournisseur
  pour une approbation, réservée **avant** l'appel.
  - `id`, `user_id`, `space_id`, `approval_id` (unique, sans clé étrangère : insérée
    pendant que la décision verrouille l'approbation ; purgée avec elle), `action`,
    `account_id null` (compte connecté, `SET NULL`).
  - `idempotency_key` (unique, dérivée de l'approbation : identifiant de l'événement créé).
  - `state` (`claimed` \| `succeeded` \| `unknown` \| `failed`), `provider_ref`
    (identifiant chez le fournisseur), `result jsonb` (identifiants seulement), `error_code`,
    `attempts` (3 au plus), `claimed_at`, `finished_at`. **Aucun contenu.**
- **`llm_calls`** : `id`, `user_id`, `space_id`, `purpose`, `provider`, `model`,
  `input_tokens`, `output_tokens`, `redactions` (nombre de masquages), `outcome` (`ok` \|
  `denied` \| `budget_exceeded` \| `error`), `error_code`, `duration_ms`, `created_at`.
  **Aucun contenu.**

### Notifications
- **`notifications`** : `id`, `user_id`, `space_id null`, `category` (`critical` \|
  `important` \| `suggestion`), `title`, `body`, `dedup_key` (unique par utilisateur),
  `deliver_after`, `status` (`pending` \| `read` \| `dismissed`), `source_type`, `source_id`,
  `digest_id null` (FK `notifications`, `SET NULL`, indexée : la synthèse qui regroupe cette
  suggestion), `read_at`, `created_at`.
- `deliver_after` (`notifications/policy.py`) : jamais pendant les plages de tranquillité,
  y compris pour `critical` sauf si `users.critical_bypass_quiet_hours` ; `important` dans un
  créneau de livraison s'il y en a ; `suggestion` à la prochaine heure de regroupement hors
  tranquillité. Le maximum par jour (jour local) repousse le surplus au jour suivant qui a de
  la place. Recalculé pour les notifications en attente quand les réglages changent.
- Synthèse (`source_type = 'digest'`, `category = 'suggestion'`, `dedup_key =
  digest:suggestion:<première suggestion>`) : créée par la tâche `build_suggestion_digests`
  (chaque minute) avec les suggestions dues ; son corps ne donne qu'un nombre. Effacer la
  dernière suggestion d'une synthèse efface la synthèse.
- **`notification_preferences`** : clé primaire (`user_id`, `category`), `category`
  (`important` \| `suggestion`), `enabled boolean` (défaut vrai), `max_per_day integer null`
  (1 à 100), `windows jsonb` (`[{start, end}]` en `HH:MM`, `important`), `digest_times jsonb`
  (`["HH:MM"]`, `suggestion`), `updated_at`. Sans ligne : valeurs par défaut (`important`
  immédiat sans maximum ; `suggestion` regroupées à 18:00, 3 par jour).
- **`dashboard_preferences`** (tâche 23, migration 0021) : clé primaire `user_id` (cascade),
  `cards jsonb` (`[{id, visible}]` dans l'ordre d'affichage ; identifiants `priorities`,
  `agenda`, `week`, `suggestions`, `journal`, `approvals`, `memory`, `budget`, `assistant`),
  `default_view` (`day` \| `week`, défaut `day`), `week_start` (`monday` \| `today`, défaut
  `monday`), `updated_at`. Sans ligne : toutes les cartes dans cet ordre. À la lecture, les
  identifiants inconnus sont ignorés et les cartes absentes ajoutées à la fin. Exportée
  (`/v1/privacy/export`), effacée avec le propriétaire.
- Tâche débloquée (tâche 22) : quand une tâche passe à `done` ou `cancelled`, chaque tâche
  ouverte qui n'attendait plus qu'elle reçoit une notification `important`. Son titre est
  « Débloquée : <titre> » ; elle porte `source_type = 'task'`, `source_id` = la tâche
  débloquée, et `dedup_key = task:<débloquée>:unblocked:<dépendance>`. Rouvrir puis terminer
  de nouveau la dépendance ne la répète pas. Le rappel d'une tâche bloquée dit combien de
  tâches elle attend, sans les nommer. Ainsi, aucune notification ne garde le titre d'une
  autre tâche, qui survivrait à son effacement.

### Idempotence
- **`idempotency_keys`** : `user_id`, `key`, `method`, `path`, `request_hash`,
  `response_status`, `response_body jsonb`, `created_at`, `expires_at` (24 h). Clé primaire
  `(user_id, key)`.

## 4. Étape 3 — intelligence

### Objectifs (F06)
- **`goals`** : `id`, `user_id`, `space_id`, `title`, `description`, `motivation`, `kind`
  (`measurable` \| `qualitative`), `metric_unit`, `start_value`, `target_value`,
  `current_value` (`numeric(14,2)`, jamais de flottant), `start_date`, `due_date`, `status`
  (`active` \| `paused` \| `achieved` \| `abandoned` \| `archived`), `review_cadence`,
  `achieved_at`, `version`, `created_at`, `updated_at`, `search_vector`.
  Objectif d'épargne (migration 0014, tâche 26) : `currency` (ISO 4217, `null` sinon ;
  `kind = 'measurable'` obligatoire) et `monthly_contribution numeric(14,2) ≥ 0` (versement
  mensuel prévu, facultatif, exige `currency`). Le montant épargné **connu** est
  `current_value` (dernier relevé, `goal_checkins`).
- **`goal_milestones`** : `goal_id` (cascade), `title`, `due_date`, `done_at`, `position`.
- **`goal_checkins`** : `goal_id` (cascade), `value numeric(14,2) null`, `note`, `created_at`.
- **`tasks.goal_id`** : tâche rattachée à un objectif (progression : tâches ouvertes/faites).

### Bilans (F05)
- **`reviews`** : `id`, `user_id`, `space_id`, `period` (`daily` \| `weekly` \| `monthly`),
  `period_start`, `period_end`, `facts jsonb` (**calculés localement**, déterministes),
  `narrative jsonb null` (lecture du modèle : synthèse, progrès, obstacles, suggestions,
  hypothèses, questions — séparée des faits), `narrative_status` (`none` \| `generated` \|
  `unavailable`), `narrative_error`, `model`, `generated_by` (`user` \| `agent:coaching`),
  `generated_at`. Un bilan par espace et par période (régénérer le remplace).
- **`spaces.auto_reviews`** : bilans automatiques activés pour l'espace.

### Recherche hybride et qualité de la mémoire (F07, ADR 0006)
- **`embeddings`** : `id`, `user_id`, `space_id`, `object_type` (`fact`, `memory`, `journal`,
  `task`, `goal`, `message`), `object_id`, `chunk`, `content_hash` (md5 du texte source),
  `model`, `embedding halfvec(768)`, `created_at` ; unique (`object_type`, `object_id`,
  `chunk`, `model`) ; index HNSW cosinus. Supprimés par **trigger** avec l'objet source.
- **`facts.last_confirmed_at`** : dernière confirmation explicite (vieillissement des
  préférences).
- **`fact_pair_dismissals`** : (`fact_a` < `fact_b`, cascade), `user_id`, `space_id`,
  `created_at` — paires « garder les deux » qui ne sont plus signalées.
- **`conversations.consolidated_until`** : messages du propriétaire déjà consolidés en
  propositions de mémoire.

### Agents et coûts (F08, ADR 0007)
- **`messages.agent`** : agent spécialisé ayant rédigé une réponse (`planning`, `coaching`,
  `memory`, ou `NULL` pour l'assistant général).
- **`agent_permissions.resource`** : s'étend à `goals` et `reviews`.
- **`users.llm_paused`** : arrêt d'urgence de tout appel cloud.
- **`llm_calls`** : + `tier` (`fast` \| `deep`), `cost_usd numeric(12,6)` (estimé),
  `cached_tokens`. Toujours **aucun contenu**.
- **`llm_calls`**, migration 0007 ([ADR 0010](adr/0010-reservation-des-quotas-llm.md)) :
  - `outcome` s'étend à `reserved` (quota réservé pendant l'appel) et `abandoned`
    (réservation libérée après la mort de son processus ; la requête reste comptée) ;
  - `reserved_tokens integer` et `reserved_cost_usd numeric(12,6)` : estimation réservée
    avant l'appel, comptée seulement tant que la ligne est `reserved`, conservée ensuite
    pour comparaison ;
  - `settled_at timestamptz null` : rapprochement avec la consommation réelle, ou
    libération ;
  - `correlation_id uuid null` : exécution à l'origine de l'appel (sans clé étrangère) ;
  - index partiel `ix_llm_calls_reserved (user_id, created_at) WHERE outcome = 'reserved'`.

## 5. Étape 4 — multimodal (migrations 0004 et 0005)

### Documents (F10, ADR 0008)
- **`documents`** : colonnes regroupées par rôle.
  - Identification : `id`, `user_id`, `space_id`, `title`.
  - Fichier : `kind` (`image` \| `pdf` \| `text` \| `audio`), `mime` (détecté par
    signature), `size_bytes`, `sha256`.
  - Usage : `purpose` (`document` \| `receipt` \| `attachment` \| `dictation`), `status`
    (`pending` \| `processing` \| `ready` \| `failed` \| `rejected`), `error`.
  - Lecture locale :
    - `sensitivity text[]` (`identity`, `health`, `bank`, classées localement) ;
    - `local_only boolean NOT NULL DEFAULT false` (migration 0008) : « Toujours garder en
      local », le refus du propriétaire, en plus de la détection ; jamais modifié par une
      relecture, une correction ou un reclassement. Le lever est une opération renforcée
      (`document.allow_cloud`). Au retour à 0007, un document gardé en local sans catégorie
      reçoit `sensitivity = {identity}` pour rester protégé ;
    - `extracted_text`, `text_source` (`pdf` \| `ocr` \| `text` \| `speech`),
      `text_confidence numeric(4,3)`, `text_corrected_at`, `pages` ;
    - `analysis jsonb` (raisons de sensibilité, `sensitivity_version` = version des règles
      de détection appliquées, lecture du ticket ; jamais d'autorité), `processed_at`.
  - Original : `storage_key` (fichier chiffré ; `NULL` une fois purgé), `original_until`
    (`NULL` = conservé sans limite), `original_sanitized boolean NOT NULL DEFAULT false`
    (migration 0009) : pour une image, le fichier conservé est la copie ré-encodée à partir
    des pixels, et non l'envoi brut. Seule une image dans ce cas est servie (ADR 0011).
    La migration le pose sur les images dont le remplacement figure au journal d'audit
    (`document.process`, `sanitized: true`).
  - Provenance : `source_type` / `source_id` (message, conversation, entrée de journal,
    tâche, objectif, opération, `mail_message` pour une pièce jointe d'e-mail).
  - Fichier de tiers (migration 0018) : `received_from text null` (expéditeur de l'e-mail,
    gardé après la purge de l'e-mail), `active_content text[] NOT NULL DEFAULT '{}'`
    (contenu actif trouvé dans un PDF : `javascript`, `open_action`, `automatic_action`,
    `launch`, `embedded_file`, `rich_media`, `xfa_form` ; un avertissement, pas un refus).
  - `version`, `search_vector` (titre + texte extrait), `created_at`, `updated_at`.
  - Les fichiers : `<documents_dir>/<user>/<document>.bin`, au format `LOS1 | id de clé |
    nonce | AES-256-GCM`, avec l'identifiant du document en données associées.
  - Les dictées (`purpose = 'dictation'`) sont transitoires (ADR 0009) : audio supprimé après
    transcription, ligne effacée après 24 h. Elles sont exclues de la liste, de la recherche
    et des embeddings.
- **`messages.attachments uuid[]`** : documents joints par le propriétaire (seul leur texte
  extrait a accompagné le message ; références conservées même si le document est supprimé).
- **`embeddings.object_type`** : s'étend à `document` et `transaction` (suppression par
  trigger).

### Finances (F04, UC02)
- **`finance_categories`** : `id`, `user_id`, `space_id`, `name` (unique par espace), `kind`
  (`expense` \| `income`), `monthly_budget numeric(14,2)`, `archived`, horodatages.
- **`transactions`** :
  - `id`, `user_id`, `space_id`, `tx_date` ;
  - `amount numeric(14,2)` **> 0** (le sens est `direction` : `expense` \| `income`) ;
  - `currency` (ISO 4217), `merchant`, `label`, `notes` ;
  - `category_id` (FK `SET NULL`) et `category_confirmed` (`false` : choisie par une règle
    apprise) ;
  - `source_type` (`manual` \| `receipt` \| `import`) et `document_id` (FK `SET NULL`, le
    ticket) ;
  - `import_id` (FK `finance_imports`, `SET NULL`) et `import_key` (empreinte SHA-256 de
    l'opération bancaire : compte, date, sens, montant, devise, libellé normalisé, puis la
    référence de la banque ou le rang de l'opération identique dans la journée), avec
    l'index unique partiel **`uq_transactions_import_key`** `(space_id, import_key) WHERE
    import_key IS NOT NULL` : la même opération ne peut pas être importée deux fois dans un
    espace, même par deux validations simultanées (migration 0013, tâche 25) ;
  - `recurring_id` (FK `finance_recurring`, `SET NULL`) : l'échéance que l'opération règle
    (exclue de la moyenne des projections, migration 0014) ;
  - `version`, `search_vector`, horodatages.
- **`finance_imports`** (migration 0013) : un relevé CSV importé. `status` (`preview` \|
  `committed` \| `undone`), `filename` (tronqué), `file_sha256` (le fichier lui-même n'est
  **jamais** conservé), `account_label`, `encoding` (`utf-8` \| `cp1252`), `delimiter`,
  `header jsonb` (noms de colonnes seulement, jamais le préambule : numéro de compte, solde),
  `mapping jsonb` (colonnes choisies), `row_count`, `imported_count`, `skipped_count`,
  `edited_count` (opérations corrigées perdues à l'annulation), `expires_at` (aperçu : 24 h),
  `committed_at`, `undone_at`, `version`. Un aperçu abandonné est supprimé (pas de statut).
- **`finance_import_rows`** (migration 0013) : les lignes d'un **aperçu** seulement,
  clé `(import_id, line_no)`, cascade : `cells jsonb`, `status` (`pending` \| `valid` \|
  `invalid` \| `in_file_duplicate` \| `already_imported` \| `possible_duplicate`), `reason`,
  `parsed jsonb` (date ISO, montant décimal exact, sens, devise, libellé, marchand, catégorie
  proposée), `import_key`, `match_id` (opération saisie ou ticket qui ressemble, sans clé
  étrangère). **Effacées** à la validation, à l'abandon ou à l'expiration
  (`purge_finance_imports`, toutes les heures).
- **`finance_recurring`** (migration 0014) : échéance récurrente ou ponctuelle déclarée par
  le propriétaire. `label`, `merchant`, `amount numeric(14,2) > 0`, `amount_is_estimate`
  (montant variable : « estimé »), `amount_low` / `amount_high` (fourchette facultative,
  `low ≤ amount ≤ high`), `direction`, `currency`, `category_id` (FK `SET NULL`), `cadence`
  (`once` \| `weekly` \| `monthly` \| `quarterly` \| `yearly` ; un jour 31 tombe le dernier
  jour des mois plus courts), `start_date`, `end_date` (≥ début), `active`, `version`.
- Les **projections** (`GET /v1/finance/projection`) ne sont **pas stockées** : recalculées
  à chaque lecture à partir de ces tables.
- **`finance_category_rules`** : `merchant_key` (nom de marchand normalisé, unique par
  espace) → `category_id` (cascade), `hits`. Apprises des corrections du propriétaire
  (« classer une dépense » : automatique et corrigeable).
- **`agent_permissions.resource`** : s'étend à `documents` et `finances`.

## 6. Étape 5 — comptes connectés (migration 0010, ADR 0012)

Les jetons ne sont jamais en clair : AES-256-GCM avec la clé `lifeos_connectors_key`, au
format `LOS1 | id de clé | nonce | chiffré`, avec en données associées l'identifiant du
compte (ou de l'état) et la nature du jeton (`refresh`, `access`, `pkce`). Aucune de ces
colonnes ne sort par l'API ni par l'export.

- **`connector_accounts`** : un compte externe relié par OAuth, commun à tous les espaces.
  - `id`, `user_id`, `provider` (`google` \| `fake`), `subject` (identifiant stable du compte
    chez le fournisseur, `sub` OpenID ; unique avec `user_id` et `provider`), `email`
    (affichée au propriétaire).
  - `status` : `active` ; `reauth_required` (renouvellement refusé ou clé perdue : jetons
    effacés, notification) ; `revoked` (déconnecté par le propriétaire : jetons effacés).
  - `scopes text[]` : scopes **réellement accordés** par le fournisseur.
  - `refresh_token_enc bytea`, `access_token_enc bytea`, `access_expires_at` : jetons
    scellés, effacés à la déconnexion.
  - `last_error` (code de motif, jamais un message du fournisseur), `connected_at`,
    `revoked_at`, horodatages.
- **`connector_grants`** : le consentement du propriétaire. Ce compte peut servir cet usage
  dans cet espace.
  - `id`, `user_id`, `account_id` (cascade), `space_id` (cascade).
  - `capability` : `calendar.read` \| `calendar.write` \| `mail.read` \| `mail.send`.
  - `expires_at`, `revoked_at`, `created_at`. Index unique partiel `(account_id, space_id,
    capability) WHERE revoked_at IS NULL` : un seul consentement actif, les retirés restent
    en historique.
- **`connector_sources`** : un agenda ou un libellé de messagerie du compte, rattaché par le
  propriétaire à **un** espace.
  - `id`, `user_id`, `space_id`, `account_id` (cascade), `kind` (`calendar` \|
    `mail_label`), `provider_ref` (unique par compte et type), `name`, `enabled`,
    `last_synced_at`, `last_error`, horodatages.
  - Synchronisation (migration 0011) : `sync_generation` (numéro du dernier passage),
    `page_token` (page à reprendre d'un passage interrompu), `lease_until` (un seul passage
    à la fois). Les lignes synchronisées référencent leur source avec `ON DELETE CASCADE` :
    retirer la source ou supprimer le compte supprime ses données.
  - `sync_cursor text null` (migration 0018, libellés e-mail) : curseur de l'historique du
    fournisseur (`historyId` Gmail) après le dernier passage complet ; `page_token` porte
    alors le type du passage en cours (`full:` ou `history:` suivi de la page).
- **`calendar_events`** (migration 0011, tâche 14) : une occurrence d'un agenda suivi, dans
  l'espace de sa source.
  - `id`, `user_id`, `space_id`, `source_id` (cascade), `provider_event_id` (unique par
    source : une resynchronisation met à jour la ligne, sans doublon), `series_id`
    (événement récurrent).
  - `status` (`confirmed` \| `tentative`), `summary`, `description` (coupée à 2 000
    caractères), `location` : texte de tiers, donnée et jamais instruction.
  - Occurrence avec horaire : `starts_at`, `ends_at` (instants UTC, le fournisseur ayant
    résolu le fuseau et le changement d'heure de la série) ; journée entière : `start_date`,
    `end_date` (fin exclue). Une contrainte impose l'un ou l'autre.
  - `timezone` (affichage), `busy` (occupé ou disponible), `organizer_self`, `response`
    (réponse du propriétaire à une invitation), `attendees` (nombre seulement), `etag`,
    `provider_updated_at`, `seen_generation` (passage qui l'a vue : après un passage complet,
    les plus anciennes ont disparu chez le fournisseur).
  - Une écriture approuvée (tâche 16) met la ligne à jour aussitôt (nouvel etag) ; un etag
    changé ou une occurrence disparue à la synchronisation invalide les propositions en
    attente qui la visent. Jamais l'adresse d'un participant.
- **`mail_messages`** (migration 0018, tâche 17, [ADR 0017](adr/0017-lecture-des-e-mails.md)) :
  un e-mail d'un libellé suivi, dans l'espace de sa source **seulement** (un même message
  dans deux libellés rattachés à deux espaces = deux lignes).
  - `id`, `user_id`, `space_id`, `source_id` (cascade), `provider_message_id` (unique par
    source), `thread_id`.
  - `from_address`, `from_name`, `to_addresses text[]`, `cc_addresses text[]` (50 au plus),
    `subject` (300 caractères), `body_text` (texte brut, HTML converti sans rendu, 20 000
    caractères au plus), `body_truncated`, `received_at` : texte de tiers, donnée et jamais
    instruction.
  - `attachments jsonb` : `[{part_id, filename, mime, size, document_id?}]`, métadonnées
    seulement ; `document_id` une fois la pièce importée par le propriétaire.
  - `sensitivity text[]` (identité, santé, banque, détectés localement à la copie),
    `purge_after` (réception + 90 jours, indexé), `seen_generation` (passage complet qui l'a
    vu), horodatages. Index `(space_id, received_at)`.
  - `message_id_header null` (migration 0019) : son Message-ID, auquel une réponse se
    rattache (`In-Reply-To`, `References`) ; inconnu pour les copies plus anciennes.
- **`mail_drafts`** (migration 0019, tâche 18, [ADR 0018](adr/0018-envoi-des-e-mails-sous-approbation.md)) :
  un e-mail écrit par le propriétaire (ou proposé par un agent), envoyé seulement après une
  approbation qui montre exactement ce contenu.
  - `id`, `user_id`, `space_id`, `account_id null` (compte d'envoi, `SET NULL`).
  - `to_addresses`, `cc_addresses`, `bcc_addresses text[]` (en minuscules, 50 adresses au
    plus en tout), `subject` (une ligne, 300 caractères), `body_text` (texte brut, 100 000
    caractères), `attachment_ids uuid[]` (documents du même espace, 10 au plus).
  - `in_reply_to_id null` (`mail_messages`, `SET NULL`), `reply_message_id_header`,
    `reply_thread_id` (copiés : la réponse reste dans le fil même si la copie est purgée).
  - `status` (`draft` \| `awaiting_approval` \| `sent`), `approval_id null` (dernière
    approbation d'envoi, `SET NULL`) : l'état affiché (`sending`, `unknown`, `failed`…) se
    déduit de cette approbation et de sa réservation (`external_operations`).
  - Une fois envoyé : `message_id_header` (unique, dérivé de l'approbation),
    `provider_message_id`, `provider_thread_id`, `sent_at` (indexé, purge à 90 jours).
  - `version` (`If-Match`) : modifier le brouillon invalide l'approbation en attente.
- **`oauth_states`** : un consentement en cours, à usage unique.
  - `id`, `user_id`, `provider`, `state_hash bytea` (unique : empreinte SHA-256 du `state`,
    jamais le `state` lui-même), `verifier_enc bytea` (vérificateur PKCE scellé).
  - `space_id`, `capabilities text[]` demandées, `account_id null` (consentement
    incrémental ou reconnexion d'un compte connu).
  - `expires_at` (10 minutes), `used_at`, `created_at`. Purgé un jour après son expiration
    ou son usage.
- **`agent_permissions.resource`** : s'étend à `calendar` et `email`. Un agent a besoin de
  cette permission **en plus** du consentement du compte dans l'espace.

## 7. Migrations

Fichiers `backend/migrations/versions/AAAAMMJJ_NNNN_sujet.py` écrits à la main, numéros
séquentiels (`0009` après `0008`), contraintes nommées par la convention (`ck_<table>_<nom>`).
La CI vérifie `alembic upgrade head` puis `alembic check` (modèles et schéma identiques), et
`backend/tests/test_migrations.py`, sur une base à part (`<base de test>_migrations`, créée par
un superutilisateur pour l'extension `vector`), fait redescendre puis remonter chaque migration
et vérifie qu'un retour arrière garde les données et leurs protections.
Comme une mise à jour peut revenir en arrière ([exploitation.md §6](exploitation.md#6-mise-à-jour-et-retour-arrière)) :

- **Compatibles avec la version précédente** (*expand / contract*) : une migration ajoute
  (table, colonne avec valeur par défaut ou nullable, index) ; ce qui disparaît (colonne,
  table, contrainte resserrée) ne disparaît qu'à une version suivante, une fois que plus aucun
  code déployé ne s'en sert. Un retour arrière fait tourner l'ancien code sur le schéma
  redescendu, et la version précédente doit aussi supporter le nouveau schéma pendant la
  mise à jour.
- **`downgrade()` toujours écrit**, et qui préserve les protections et les comptes : une
  protection que l'ancien schéma ne connaît pas se reporte sur la plus proche qu'il connaît
  (0008 : un document « toujours gardé en local » devient sensible), un appel compté le reste
  (0007 : une réservation devient un appel en échec).
- **`Downgrade: lossy`** dans la docstring quand la descente perd des données du propriétaire
  (une colonne ou une table qu'il remplit et que l'ancien schéma ne peut pas garder) :
  `update.sh --rollback` restaure alors la sauvegarde préalable au lieu de redescendre le
  schéma (ce qui a été écrit depuis la mise à jour est perdu).
- Les migrations d'une même mise à jour s'appliquent dans une seule transaction : une
  migration qui échoue laisse le schéma tel qu'il était (retour arrière automatique,
  `scripts/test-update.sh`).

## 8. Prévu ensuite

| Table | Étape | Notes |
|---|---|---|
| `push_devices` | 5 | jetons de notification mobile |
| connexion bancaire (agrégateur agréé) | — | projet séparé : aujourd'hui, import de fichiers CSV seulement (tâche 25) |
