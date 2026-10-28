# Conventions de l'API

## Généralités

- Préfixe de version : `/v1`. Une rupture de contrat impose `/v2` ; les ajouts compatibles
  (champs optionnels, nouvelles routes) restent en `v1`.
- JSON en `snake_case`, encodage UTF-8.
- Dates : ISO 8601 avec fuseau (`2026-10-11T08:30:00Z`). Dates calendaires : `YYYY-MM-DD`.
- Montants : chaîne décimale (`"12.50"`) + `currency` ; jamais de nombre flottant.
- Identifiants : UUID.
- Spécification OpenAPI générée par FastAPI (`/v1/openapi.json`), désactivable en production
  (`LIFEOS_EXPOSE_OPENAPI=false`).

## Authentification

- Cookie de session `lifeos_session` (`HttpOnly`, `Secure`, `SameSite=Strict`).
- Toute requête `POST`, `PUT`, `PATCH`, `DELETE` doit porter un en-tête `Origin` égal à
  l'origine publique configurée (`LIFEOS_PUBLIC_ORIGIN`), sinon `403 origin_mismatch`. Le
  refus est journalisé, compté et audité (`http.origin_mismatch`, un événement par minute,
  sans l'origine écrite par le client).
- `401 unauthenticated` : session absente, expirée, inactive ou révoquée, ou compte
  désactivé. La réutilisation d'une session connue mais refusée est auditée
  (`auth.session.rejected`, une fois par session et par heure) ; un jeton inconnu ne l'est
  pas.
- Une session utilisée depuis un autre navigateur ou système que celui de sa connexion (les
  numéros de version mis à part) reste valide, mais l'événement est audité
  (`auth.session.ua_changed`, une fois par session et par heure).
- `403 reauthentication_required` : opération sensible nécessitant une connexion de moins de
  10 minutes. Le refus est audité sous le nom de l'opération (`permission.grant`,
  `space.cloud_consent`, `auth.passkey.revoke`…).
- `POST /v1/auth/sessions/revoke-others` (renforcée) révoque toutes les sessions sauf la
  session courante et répond `200 {"revoked": n}` ; audité `auth.session.revoke_others`.

## Erreurs (RFC 9457 « Problem Details »)

`Content-Type: application/problem+json`

```json
{
  "type": "urn:lifeos:problem:task_not_found",
  "title": "Task not found",
  "status": 404,
  "code": "task_not_found",
  "detail": "No task with this id in the allowed spaces.",
  "request_id": "6f1c…"
}
```

- `code` est stable et sert au client pour la traduction.
- Erreurs de validation : `422 validation_error` avec un tableau `errors`
  (`loc`, `msg`, `type`).
- Une ressource d'un espace non autorisé répond `404` (et non `403`) pour ne pas révéler son
  existence. Un `space_id` explicite (paramètre, corps ou chemin) hors des espaces autorisés
  répond `404 space_not_found` et est audité (`space.access`, un événement par minute et par
  compte, avec le gabarit de la route) ; un `404` sur l'identifiant d'un objet ne l'est pas
  (indiscernable d'un objet supprimé).

## Pagination

Par curseur : `?limit=50&cursor=<opaque>` → `{ "items": [...], "next_cursor": "..." | null }`.
`limit` maximum : 200.

## Idempotence des écritures

Les créations (`POST`) acceptent l'en-tête `Idempotency-Key` (UUID conseillé). Rejouer la même
clé avec le même corps renvoie la réponse initiale ; avec un corps différent : `409
idempotency_key_reused`. Clés conservées 24 heures.

## Concurrence

Les entités éditables exposent `version`. Une mise à jour (`PATCH`) peut envoyer
`If-Match: <version>` ; en cas de conflit : `409 version_conflict`.

## Limites

- `429 rate_limited` avec `Retry-After` (Traefik sur `/v1/auth`).
- `503 llm_unavailable` : fournisseur LLM désactivé ou injoignable (mode dégradé ; les données
  locales restent consultables).
- `429 llm_budget_exceeded` : budget mensuel de jetons LLM atteint ou, en facturation
  payante, budget en dollars du jour ou du mois atteint.
- `429 llm_daily_quota_reached` : plafond quotidien de requêtes du palier atteint
  (facturation gratuite). Extensions `tier` (`fast` \| `deep`) et `limit` (limite appliquée,
  marge déduite).
- Ces deux refus ont lieu **avant** tout envoi, dès que la réservation de l'appel ne tient
  plus dans la limite, appels en cours compris (ADR 0010).
- `GET /v1/guardian/llm-usage` :
  - `requests_today` compte les appels en cours ; `in_flight` les détaille par palier ;
  - `daily_request_caps`, `daily_budget_usd` et `monthly_budget_usd` sont les limites
    appliquées, marges déduites (`daily_request_margin`, `budget_margin_ratio`).

## Approbations

- Une action soumise à approbation crée une ressource `approval` dont `params` sont
  **exactement** ce qui sera exécuté et `preview` leur présentation lisible.
- `POST /v1/approvals/{id}/approve` accepte un corps `{"params": {...}}` facultatif : le
  propriétaire peut corriger les paramètres (ex. décocher une proposition) avant d'approuver ;
  ils sont revalidés par le même schéma. L'exécution suit immédiatement l'accord.
- Toute proposition issue du LLM (ex. analyse du journal) passe par une approbation, quel que
  soit le niveau d'autonomie de l'action.
- `403 approval_required` : action exécutée sans approbation valide (refus audité).
- Une approbation sert une fois : une deuxième décision (requête rejouée, double clic,
  proposition refusée ou expirée) répond `409 approval_not_pending` avec l'extension
  `approval_status`, et est auditée (`approval.replay`). Seul le propriétaire décide : un
  agent reçoit `403 action_denied` (audité). Des paramètres corrigés hors du schéma
  répondent `422 invalid_action_params`, l'approbation restant en attente.
- `501 action_not_available` : action déclarée dans la politique mais pas encore implémentée.
- Paramètres liés (ADR 0016) : l'empreinte SHA-256 des paramètres approuvés est
  conservée ; exécuter d'autres paramètres répond `403 approval_params_mismatch` (audité).
  Certaines actions ne se corrigent pas à l'approbation, leur aperçu venant de l'état du
  fournisseur : `422 approval_not_editable`.
- Statut `invalidated` : la proposition ne décrit plus la réalité, `error` dit pourquoi
  (`event_changed`, `event_gone`, `superseded`). Elle ne peut plus être approuvée
  (`409 approval_not_pending`, `approval_status: "invalidated"`). La même proposition
  encore en attente est renvoyée au lieu d'en créer une seconde ; une nouvelle proposition
  sur le même objet remplace l'ancienne.
- Approbation `level: "reinforced"` (ex. événement partagé) : l'approuver demande une
  connexion par passkey récente, sinon `403 reauthentication_required` (audité) ; le web
  relance alors la passkey puis l'accord, une fois.
- Écriture chez un fournisseur : l'accord répond `200` avec l'approbation `executed`, ou
  `failed` et un code dans `error` :
  - `outcome_unknown` : le fournisseur n'a pas confirmé (délai dépassé après l'envoi).
    Rien n'est renvoyé ; LifeOS vérifie chez lui dans les minutes qui suivent, puis
    l'approbation passe `executed`, ou garde `failed` avec `not_applied` ;
  - `event_changed`, `event_gone`, `connector_reauth_required`, `connector_unavailable`,
    `too_many_attempts` : rien n'a été écrit.
  Une nouvelle tentative après un crash ne réécrit jamais : l'opération déjà réservée est
  d'abord vérifiée chez le fournisseur.

## Mode dégradé du chat

`POST /v1/chat/conversations/{id}/messages` répond `201` même si l'IA est indisponible : le
message de l'utilisateur est enregistré, `assistant_message` vaut `null` et `error` contient
le code (`cloud_consent_required`, `llm_unavailable`, `llm_budget_exceeded`…). Le refus
`agent_permission_required` (agent sans permission valide dans l'espace) est audité
(`chat.agent_reply`) et rien n'est envoyé au LLM.

`context.screen` n'accepte qu'une valeur d'une liste fermée (aujourd'hui `"chat"`), jamais
un texte libre : toute autre valeur répond `422 validation_error`.

Pièces jointes : `attachments` (au plus 3 identifiants de documents) joint le texte extrait
localement au message. Les refus ont lieu **avant** tout enregistrement :

- `422 attachment_sensitive` (document qui reste local : jamais envoyé) avec en extension
  `document_id`, `sensitivity` (catégories enregistrées **et** trouvées à l'envoi par les
  règles en vigueur) et `reason` : `detected` (classé sensible localement) ou `owner`
  (« Toujours garder en local ») ;
- `409 attachment_not_ready` ;
- `422 attachment_unreadable` ;
- `404 document_not_found` (autre espace ou dictée) ;
- `403 attachment_not_allowed` (agent sans droit `documents`), audité (`document.attach`).

Contrôle avant l'envoi : `POST /v1/chat/preview` (`space_id`, `content`, `agent`,
`attachments`) applique les mêmes règles **sans rien enregistrer ni envoyer** et répond
`200` :

```json
{
  "masked": {"iban": 1, "email": 1},
  "attachments": [
    {"document_id": "…", "sent": true, "reason": null, "sensitivity": [], "masked": {"phone": 1}},
    {"document_id": "…", "sent": false, "reason": "sensitive", "sensitivity": ["bank"], "masked": {}}
  ]
}
```

- `masked` compte par type les identifiants qui seront masqués (`email`, `phone`, `iban`,
  `card`, `nir`, `secret`, `passport`, `id_number`, `tax_number`), jamais leurs valeurs ;
- `reason` d'une pièce jointe laissée de côté : `sensitive`, `local_only`, `not_ready`,
  `unreadable`, `not_found`, `not_allowed` (droits de l'agent qui répond) ;
- un espace inconnu ou hors des espaces autorisés répond `404 space_not_found`.

Le client n'affiche le récapitulatif que si quelque chose sera masqué ou laissé de côté,
puis envoie le message avec les seules pièces jointes `sent`.

## Envois de fichiers (documents, dictées)

- `multipart/form-data` avec un champ `file` et des champs de formulaire. La taille est
  contrôlée **pendant** la lecture : `413 document_too_large` (`max_bytes` en extension).
  Un fichier vide répond `422 document_empty`.
- Le type est reconnu à la signature du contenu ; un type non accepté répond
  `415 unsupported_type`. Sans clé de chiffrement configurée : `503 documents_unavailable`.
  Une image, sans lecture locale configurée (`LIFEOS_EXTRACTION_PROVIDER=disabled`), répond
  `415 image_reading_disabled` : elle ne serait ni lue ni téléchargeable.
- `POST /v1/documents` répond `201` avec `status: "pending"`. La lecture (texte du PDF, OCR)
  a lieu en tâche de fond : suivre `GET /v1/documents/{id}` jusqu'à `ready` ou `failed`.
  L'`Idempotency-Key` est liée aux champs **et** à l'empreinte SHA-256 du fichier.
- `GET /v1/documents/{id}/original` sert l'original déchiffré en pièce jointe, avec
  `nosniff`, `CSP sandbox` et `no-store`. Une fois l'original purgé : `410 original_purged`.
  Une image n'est servie qu'une fois ré-encodée à partir de ses pixels (`original_downloadable`
  dans le document) : pendant sa lecture, `409 document_processing` ; sinon (lecture en
  échec ou interrompue, texte saisi à la main), `409 original_not_sanitized`. L'original
  reste conservé pour une nouvelle lecture.
- `PATCH /v1/documents/{id}` (`If-Match`) : titre, correction du texte extrait (la
  sensibilité est recalculée), `keep_original`, `local_only` (« Toujours garder en local » :
  `true` immédiat ; `false` est une opération renforcée qui répond
  `403 reauthentication_required` sans passkey récente, et ne retire jamais une catégorie
  détectée).
- `POST /v1/documents/{id}/reprocess` relance la lecture et remplace une correction.
- `POST /v1/voice/dictations` (champs `file`, `space_id`) répond `202` avec une dictée
  `pending` :
  - suivre `GET /v1/voice/dictations/{id}` jusqu'à `ready` (texte) ou `failed` (`too_long`,
    `unreadable`…) ;
  - puis `DELETE` une fois le texte utilisé ;
  - `503 transcription_unavailable` si la dictée est désactivée (`GET /v1/voice/status`).

## Comptes connectés (OAuth, ADR 0012)

- `GET /v1/connectors/providers` : fournisseurs configurés (`[]` si
  `LIFEOS_CONNECTORS_PROVIDER=disabled`).
- `GET /v1/connectors/accounts` : comptes, statut (`active` \| `reauth_required` \|
  `revoked`), `capabilities` (usages que le fournisseur a **réellement** accordés), `grants`
  (consentements actifs par espace), `sources`. Jamais un jeton, même chiffré.
- `POST /v1/connectors/oauth/start` `{provider, space_id, capabilities, account_id?}` →
  `{authorization_url}`. Opération renforcée : `403 reauthentication_required` sans passkey
  récente. `account_id` sert au consentement incrémental et à la reconnexion.
- Le fournisseur renvoie le navigateur vers la **page web** `/connexions/retour`, pas vers
  l'API : le cookie `SameSite=Strict` n'accompagne pas sa redirection. La page retire le
  code de l'adresse et envoie, une seule fois, `POST /v1/connectors/oauth/callback`
  `{state, code | null, error | null}` → `{account, space_id, requested, granted, missing}`.
  - `400 oauth_state_invalid` : état inconnu, expiré (10 minutes), déjà utilisé ou d'un autre
    propriétaire ;
  - `400 connector_consent_denied` (`reason` en extension, ex. `access_denied`) : refus chez
    le fournisseur ;
  - `400 oauth_code_invalid` : code refusé par le fournisseur (expiré, déjà utilisé, PKCE) ;
  - `502 connector_provider_error` : fournisseur injoignable ou réponse inexploitable.
- `PUT /v1/connectors/accounts/{id}/grants` `{space_id, capabilities}` : les usages de ce
  compte dans cet espace, exactement. Ajouter est renforcé ; retirer est immédiat.
  `409 connector_scope_missing` (`capabilities` en extension) si le fournisseur ne les a pas
  accordés : il faut un nouveau consentement (`oauth/start` avec `account_id`).
- `DELETE /v1/connectors/grants/{id}` : retrait immédiat d'un consentement.
- `DELETE /v1/connectors/accounts/{id}?purge=false|true` → `{account_id, remote_revocation,
  purged}`. Révocation chez le fournisseur (`done` \| `failed` \| `not_needed`), jetons
  effacés quoi qu'il arrive, consentements retirés, approbations en attente expirées.
  `purge=true` supprime aussi le compte et les données synchronisées ; sinon le compte reste
  visible, `revoked`.
- Utilisation par les autres modules (synchronisation, actions) : `409 connector_reauth_required`
  ou `409 connector_disconnected` si le compte n'est pas actif ; `403
  connector_capability_missing` sans consentement de l'espace pour cet usage ; `403
  agent_permission_required` pour un agent sans permission `calendar` ou `email`.
- `503 connectors_unavailable` : connecteurs désactivés ou clé des jetons absente.
- Agendas (tâche 14) :
  - `GET /v1/connectors/accounts/{id}/calendars` → les agendas du compte, lus en direct
    (`provider_ref`, `name`, `primary`, `read_only`, `source_id`/`space_id` s'il est déjà
    suivi). `503 connector_unavailable` si le fournisseur ne répond pas.
  - `POST /v1/connectors/sources` `{account_id, provider_ref, space_id}` → `201` la source ;
    exige le consentement `calendar.read` de cet espace (`403
    connector_capability_missing`, audité) ; `409 calendar_already_mapped` si l'agenda
    alimente déjà un autre espace. La première lecture est mise en file.
  - `DELETE /v1/connectors/sources/{id}` : ne plus suivre ; les occurrences copiées sont
    supprimées. `POST /v1/connectors/sources/{id}/sync` → `202` : lecture demandée.
  - `GET /v1/calendar/events?start&end&space_id?` → `{items, timezone}` : occurrences qui
    chevauchent `[start, end)` (62 jours au plus, sinon `422 invalid_window`), dans les
    espaces du principal. Une date sans décalage est lue dans le fuseau du propriétaire ;
    les instants rendus portent le décalage de ce fuseau, les journées entières sont des
    dates (fin exclue). Chaque occurrence indique sa source et sa date de lecture, et
    `editable` : son compte a le consentement `calendar.write` dans cet espace.
- Modifier l'agenda (tâche 16, ADR 0016) : chaque route **propose** et répond `201` avec
  l'approbation (aperçu exact relu chez le fournisseur) ; rien n'est écrit avant l'accord
  (`POST /v1/approvals/{id}/approve`). `Idempotency-Key` est accepté. Une heure sans
  décalage est lue dans le fuseau du propriétaire.
  - `GET /v1/calendar/calendars?space_id` → `[{source_id, name, account_id}]` : agendas
    suivis de l'espace qu'on peut modifier.
  - `POST /v1/calendar/events` `{space_id, source_id?, summary, description?, location?,
    starts_at, ends_at | start_date, end_date, guests?, notify?}`. Sans `source_id` : le
    premier agenda modifiable de l'espace.
  - `POST /v1/calendar/events/{id}/move` `{starts_at, ends_at | start_date, end_date,
    notify?}`.
  - `POST /v1/calendar/events/{id}/cancel` `{notify?}`.
  - Action `calendar.create_event`, `calendar.move_event` ou `calendar.cancel_event`
    (approbation simple) ; pour un événement partagé (autres participants, événement d'un
    autre organisateur, agenda d'un autre propriétaire), `calendar.update_shared_event`
    (approbation renforcée). Paramètres non modifiables à l'approbation.
  - `preview` : `{operation, calendar, summary, summary_line, timezone, before?, after?,
    location?, attendees, notify, shared, recurring?, notes}`, heures écrites dans le
    fuseau du propriétaire.
  - `notify` : les participants ne sont prévenus (`sendUpdates=all`) que si c'est coché.
  - Erreurs : `409 calendar_write_unavailable` (aucun agenda modifiable dans l'espace),
    `409 calendar_read_only`, `409 calendar_gone`, `409 event_gone`, `409
    event_not_organizer` (seul l'organisateur déplace un événement), `422 event_in_past`,
    `422 event_unchanged`, `422 invalid_event_times` (fin avant le début, plus de 14 jours,
    ou 31 jours entiers), `422 invalid_guest` (adresse non renvoyée), `404
    event_not_found`, `403 connector_capability_missing` sans consentement `calendar.write`
    de l'espace, `403 agent_permission_required` pour un agent sans permission `calendar`
    en écriture.
- Planification (tâche 15), calculée sur le serveur, sans IA, en lecture seule :
  - `GET /v1/planning/conflicts?space_id&start?&end?` (14 jours par défaut, 31 au plus) →
    `{items: [{first, second, start, end}], synced_at, calendars}`. Les occurrences de tous
    les espaces du principal bloquent du temps, mais seules celles de `space_id` ont un
    titre et un identifiant : ailleurs, `label = "Occupé (autre espace)"`, `event_id = null`.
  - `POST /v1/planning/slots` `{space_id, duration_minutes? | task_id?, not_before?,
    not_after?}` → au plus trois créneaux (un par jour), chacun avec ses `assumptions`
    (durée et son origine, disponibilités, marge, provisoires, fraîcheur de l'agenda) et ses
    `events_used`. Sans durée : l'estimation de la tâche, sinon une heure ; avec une tâche
    datée, avant son échéance. Rien n'est écrit dans l'agenda : « Bloquer ce créneau » propose
    un `calendar.create_event` à approuver.

## Tâches : dépendances, état bloqué, étiquettes (tâche 22)

- `POST /v1/tasks/{id}/dependencies` `{"depends_on_id": "<uuid>"}` : la tâche `{id}` attend
  `depends_on_id`. Réponse `201` avec la tâche à jour. `Idempotency-Key` est accepté.
  Ajouter un lien qui existe déjà ne change rien (même `version`). Un nouveau lien
  incrémente la `version` de la tâche (`If-Match`).
- `DELETE /v1/tasks/{id}/dependencies/{depends_on_id}` : `204`, ou `404
  dependency_not_found`. Retirer un lien incrémente aussi la `version`.
- `POST /v1/tasks` accepte `depends_on` (20 identifiants au plus, même espace) et `tags`.
  `PATCH` accepte `tags`.
- `TaskOut` ajoute :
  - `tags` ;
  - `depends_on` : toutes les tâches attendues, terminées ou non ;
  - `blocked_by` : `[{id, title, status}]`, celles encore ouvertes ;
  - `blocked` : vrai si la tâche est ouverte et attend au moins une tâche ouverte.
  Ces champs figurent aussi dans `GET /v1/dashboard`.
- Filtres de `GET /v1/tasks` : `blocked=true|false` et `tag=<étiquette>`. L'étiquette est
  normalisée comme à l'enregistrement ; vide ou invalide : `422 validation_error`.
- Erreurs :
  - `422 dependency_cycle` : le lien fermerait une boucle, la dépendance d'une tâche à
    elle-même comprise. L'extension `path` donne la chaîne des identifiants, de la tâche
    à elle-même (`[C, A, B, C]`). Rien n'est enregistré ;
  - `422 dependency_space_mismatch` : les deux tâches ne sont pas du même espace ;
  - `422 too_many_dependencies` : la tâche attend déjà 20 tâches (extension `max`) ;
  - `404 task_not_found` : l'une des deux tâches est hors de vos espaces ;
  - `409 task_blocked` : passage à `in_progress` ou `done` d'une tâche qui attend encore une
    tâche ouverte. L'extension `blocked_by` (`[{id, title, status}]`) dit pourquoi. Passer
    à `cancelled` reste possible.
- Une dépendance passée à `done` ou `cancelled` libère les tâches qui n'attendaient plus
  qu'elle. Chacune reçoit une notification « Débloquée : <titre> », une seule fois.
  L'audit enregistre `task.dependency.add` et `task.dependency.remove` (identifiants
  seulement) et `released` (un nombre) dans `task.update`.
- Pour un agent, lier deux tâches relève de `task.update` (niveau `rule` : permission
  `tasks` en écriture dans l'espace).

## Export et réversibilité (tâche 33)

- `GET /v1/privacy/export` : le JSON complet (`application/json`, pièce jointe
  `lifeos-export-AAAAMMJJ.json`).
- `GET /v1/privacy/export.zip[?originals=true]` : l'archive (`application/zip`, pièce
  jointe `lifeos-export-AAAAMMJJ.zip`), écrite pendant l'envoi fichier par fichier (les
  lignes sont lues avant, dans la transaction de la requête). Tout est dans un dossier
  `lifeos-export-AAAAMMJJ/`.
- Autorisation : `data.export` (propriétaire, jamais un agent) en validation renforcée,
  pour les deux routes, originaux ou non : `403 reauthentication_required` (refus audité)
  si la connexion par passkey n'est pas récente. Sans cookie : `401`.
  Succès audité `privacy.export` avec `format` (`json`, `zip`), `rows` et `originals`
  (nombre prévu) : jamais de contenu. `503 documents_unavailable` si des originaux sont
  demandés sans clé de stockage.
- Chaque ligne est choisie par son propriétaire, déduit du schéma (`user_id`, ou celui de
  la ligne parente : révisions d'un souvenir, jalons d'un objectif).

**JSON, format `lifeos-export` version 2** (la version 1 imbriquait les révisions dans les
souvenirs et écrivait les montants en nombres) : `format`, `version`, `app_version`,
`exported_at`, `excluded` (`tables` et `columns` non exportées, avec leur raison), `user`
(le compte et ses réglages), puis une clé par table, nommée comme la table (`tasks`,
`task_dependencies`, `memory_revisions`, `memory_facts`, `goal_milestones`,
`calendar_events`, `audit_events`…), chaque ligne avec ses colonnes sauf `user_id` et
`search_vector`. Types : UUID et adresses IP en chaînes, dates et instants ISO 8601,
**décimaux en chaînes exactes** (`"12.50"`, jamais de flottant ; dérogation assumée au
`jsonable_encoder` de FastAPI), JSONB tel quel. `documents[].original_available` remplace
l'emplacement du fichier chiffré. L'encodeur refuse les octets bruts : aucune colonne
binaire (matériel de clé) ne peut partir par mégarde.

**CSV** (`tasks.csv`, `transactions.csv`) : RFC 4180 (virgule, guillemets doubles, CRLF),
UTF-8 avec BOM, point décimal, en-têtes anglais en snake_case (à reprendre par l'import CSV
de la tâche 25). Instants ISO 8601 dans le fuseau du propriétaire, avec leur décalage.
Une cellule de texte qui commence par `=`, `+`, `-`, `@`, tabulation ou retour chariot est
préfixée d'une apostrophe (injection de formules : les tickets lus par OCR et les agendas
synchronisés sont du texte tiers) ; les colonnes numériques ne le sont jamais.
`transactions.csv` : `amount` toujours positif, `signed_amount` négatif pour une dépense,
`category` (nom), `category_confirmed` (`true`/`false`). `tasks.csv` : `tags` séparés par
`, ` (une étiquette ne contient jamais de virgule), `depends_on` (identifiants séparés par
une espace), `goal` (titre).

**iCalendar** (RFC 5545, écrit sans dépendance par `privacy/ics.py`) : CRLF, lignes pliées
à 75 octets sans couper un caractère UTF-8, TEXT échappé (`\\`, `;`, `,`, saut de ligne) et
débarrassé des caractères de contrôle, instants en UTC (`Z`). `calendar.ics` : un VEVENT
par occurrence synchronisée (`UID:calendar-<id>@lifeos`, journée entière en
`VALUE=DATE` avec fin exclusive, `STATUS`, `TRANSP`). `tasks.ics` : un VTODO par tâche
(`UID:task-<id>@lifeos`, `DUE`, `STATUS`, `PRIORITY` 1/5/9, `COMPLETED`, `CATEGORIES`
= étiquettes, `RELATED-TO;RELTYPE=DEPENDS-ON` pour les dépendances, VALARM au rappel) ;
Google Agenda ignore les VTODO. Les UID sont stables : un nouvel import met à jour.
L'espace figure en `X-LIFEOS-SPACE`.

**Markdown** (`journal/AAAA-MM-JJ-<id8>.md`) : en-tête YAML (valeurs JSON : `id`, `date`,
`space`, `mood`, `created_at`, `updated_at`), puis le texte de l'entrée.

**Originaux** (`originaux/<date>-<id8>-<titre>.<ext>`, sur demande) : déchiffrés, stockés
sans recompression ; titre réduit aux lettres, chiffres, `-` et `_`. Un original absent ou
illisible est nommé dans le README, jamais omis en silence ; les images jamais nettoyées de
leurs métadonnées (T26) ne sont pas incluses.

## Notifications adaptatives (F09, tâches 20 et 21, ADR 0015)

- `GET /v1/notifications` liste les notifications **dues** : `deliver_after` passé, hors
  suggestions groupées. Une suggestion n'y figure jamais seule : elle arrive dans une
  **synthèse** (`source_type = "digest"`, `category = "suggestion"`) dont `items_count` donne
  le nombre de suggestions. La synthèse ne cite aucun titre (un objet effacé ne doit pas y
  survivre).
- `GET /v1/notifications/{id}/items` : les suggestions d'une synthèse, les plus anciennes
  d'abord (`404 notification_not_found` pour une notification qui n'est pas une synthèse).
- `PATCH /v1/notifications/{id}` `{"status": "read" | "dismissed"}` : sur une synthèse,
  s'applique aussi à ses suggestions. `POST /v1/notifications/read-all` ne marque jamais une
  suggestion qui attend encore sa synthèse.
- `GET /v1/notifications/preferences` :
  `{"categories": [{category, enabled, max_per_day, windows, digest_times}],
  "critical_bypass_quiet_hours": bool}` pour `important` et `suggestion` (valeurs par défaut
  sans ligne enregistrée : `important` actif, sans maximum ni créneau ; `suggestion` active,
  3 par jour, synthèse à `18:00`).
- `PUT /v1/notifications/preferences` `{"categories": [...]}` : règle les catégories
  données ; un champ omis garde sa valeur. `windows` (`important` seulement, 4 au plus) :
  `[{start, end}]` en `HH:MM` locales, `start < end`, sans chevauchement ; vide : immédiat
  hors tranquillité. `digest_times` (`suggestion` seulement) : 1 à 4 heures `HH:MM`.
  `max_per_day` : 1 à 100, ou `null` (sans limite). Les notifications pas encore livrées sont
  reprogrammées ; celles d'une catégorie désactivée passent à `dismissed`. Audit
  `notification.preferences.update` (noms des champs modifiés, nombres reprogrammés et
  écartés).
- Erreur `422 notification_preferences_invalid`, extension `field` (par ex.
  `important.windows`), sans la saisie.
- Alertes critiques : `PATCH /v1/settings {"critical_bypass_quiet_hours": true}` est une
  opération **renforcée** (`notification.critical_bypass` : `403 reauthentication_required`
  sans passkey récente, refus audité) ; le retrait (`false`) est immédiat. Les deux sont
  audités (`notification.critical_bypass`, `{enabled}`). Sans cette permission, une alerte
  critique attend la fin des plages de tranquillité. Changer le fuseau, les plages ou cette
  permission reprogramme aussi les notifications en attente.
- Pour un envoi push (tâche 19) : `notifications.service.due(now)` donne les conditions
  d'une notification à envoyer (livrée, hors synthèse, pas une suggestion en attente).

## Familles de routes

| Préfixe | Étape | Contenu |
|---|---|---|
| `/healthz`, `/readyz` | 1 | vivacité, disponibilité (base, Redis) — hors `/v1` |
| `/v1/auth` | 1 | passkeys, sessions, utilisateur courant |
| `/v1/spaces` | 2 | espaces, consentement cloud |
| `/v1/chat` | 2 | conversations, messages |
| `/v1/tasks` | 2 | CRUD, rappels, statuts, dépendances et étiquettes (tâche 22) |
| `/v1/journal` | 2 | entrées, analyse (propositions à valider) |
| `/v1/memory` | 2 | faits, souvenirs, correction, protection, suppression |
| `/v1/privacy` | 2 | export complet : JSON (`/export`), archive ZIP (`/export.zip`, originaux déchiffrés en option) |
| `/v1/search` | 2, 3, 4 | recherche hybride (plein texte + sens), filtrée par espace, type (dont `documents`) et période |
| `/v1/approvals` | 2 | prévisualisation, accepter / refuser |
| `/v1/guardian` | 2 | permissions d'agents, journal d'audit, appels LLM |
| `/v1/notifications` | 2, V1 | liste, lecture, synthèses de suggestions, préférences par catégorie (activation, maximum par jour, créneaux, heures de regroupement) |
| `/v1/dashboard` | 2 | briefing du jour |
| `/v1/goals` | 3 | objectifs, jalons, bilans |
| `/v1/reviews`, `/v1/agents` | 3 | bilans quotidiens / hebdomadaires / mensuels, agents spécialisés et leurs accès |
| `/v1/documents` | 4 | envoi, liste, détail, original chiffré, correction du texte, conservation, relecture, suppression |
| `/v1/finance` | 4 | catégories et budgets, opérations, proposition depuis un ticket, budget du mois |
| `/v1/voice` | 4 | statut, dictées (transcription locale, puis suppression de l'audio) |
| `/v1/connectors` | 5 | fournisseurs, comptes connectés, consentement OAuth (début, retour), usages par espace, agendas suivis, déconnexion |
| `/v1/calendar` | 5 | occurrences des agendas suivis, propositions de création, de déplacement et d'annulation (approbation) |
| `/v1/planning` | 5 | conflits d'agenda et créneaux libres, calculés localement (lecture seule) |
