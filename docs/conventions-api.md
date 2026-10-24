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
  l'origine publique configurée (`LIFEOS_PUBLIC_ORIGIN`), sinon `403 origin_mismatch`.
- `401 unauthenticated` : session absente, expirée ou révoquée.
- `403 reauthentication_required` : opération sensible nécessitant une connexion de moins de
  10 minutes.

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
  existence.

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
- `501 action_not_available` : action déclarée dans la politique mais pas encore implémentée.

## Mode dégradé du chat

`POST /v1/chat/conversations/{id}/messages` répond `201` même si l'IA est indisponible : le
message de l'utilisateur est enregistré, `assistant_message` vaut `null` et `error` contient
le code (`cloud_consent_required`, `llm_unavailable`, `llm_budget_exceeded`…).

Pièces jointes : `attachments` (au plus 3 identifiants de documents) joint le texte extrait
localement au message. Les refus ont lieu **avant** tout enregistrement :

- `422 attachment_sensitive` (document qui reste local : jamais envoyé) avec en extension
  `document_id`, `sensitivity` (catégories enregistrées **et** trouvées à l'envoi par les
  règles en vigueur) et `reason` : `detected` (classé sensible localement) ou `owner`
  (« Toujours garder en local ») ;
- `409 attachment_not_ready` ;
- `422 attachment_unreadable` ;
- `404 document_not_found` (autre espace ou dictée) ;
- `403 attachment_not_allowed` (agent sans droit `documents`).

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

## Familles de routes

| Préfixe | Étape | Contenu |
|---|---|---|
| `/healthz`, `/readyz` | 1 | vivacité, disponibilité (base, Redis) — hors `/v1` |
| `/v1/auth` | 1 | passkeys, sessions, utilisateur courant |
| `/v1/spaces` | 2 | espaces, consentement cloud |
| `/v1/chat` | 2 | conversations, messages |
| `/v1/tasks` | 2 | CRUD, rappels, statuts |
| `/v1/journal` | 2 | entrées, analyse (propositions à valider) |
| `/v1/memory` | 2 | faits, souvenirs, correction, protection, export, suppression |
| `/v1/search` | 2, 3, 4 | recherche hybride (plein texte + sens), filtrée par espace, type (dont `documents`) et période |
| `/v1/approvals` | 2 | prévisualisation, accepter / refuser |
| `/v1/guardian` | 2 | permissions d'agents, journal d'audit, appels LLM |
| `/v1/notifications` | 2 | liste, lecture, préférences (plages de tranquillité) |
| `/v1/dashboard` | 2 | briefing du jour |
| `/v1/goals` | 3 | objectifs, jalons, bilans |
| `/v1/reviews`, `/v1/agents` | 3 | bilans quotidiens / hebdomadaires / mensuels, agents spécialisés et leurs accès |
| `/v1/documents` | 4 | envoi, liste, détail, original chiffré, correction du texte, conservation, relecture, suppression |
| `/v1/finance` | 4 | catégories et budgets, opérations, proposition depuis un ticket, budget du mois |
| `/v1/voice` | 4 | statut, dictées (transcription locale, puis suppression de l'audio) |
| `/v1/connectors` | 5 | fournisseurs, comptes connectés, consentement OAuth (début, retour), usages par espace, déconnexion |
