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

- `422 attachment_sensitive` (document sensible : jamais envoyé) ;
- `409 attachment_not_ready` ;
- `422 attachment_unreadable` ;
- `404 document_not_found` (autre espace ou dictée) ;
- `403 attachment_not_allowed` (agent sans droit `documents`).

## Envois de fichiers (documents, dictées)

- `multipart/form-data` avec un champ `file` et des champs de formulaire. La taille est
  contrôlée **pendant** la lecture : `413 document_too_large` (`max_bytes` en extension).
  Un fichier vide répond `422 document_empty`.
- Le type est reconnu à la signature du contenu ; un type non accepté répond
  `415 unsupported_type`. Sans clé de chiffrement configurée : `503 documents_unavailable`.
- `POST /v1/documents` répond `201` avec `status: "pending"`. La lecture (texte du PDF, OCR)
  a lieu en tâche de fond : suivre `GET /v1/documents/{id}` jusqu'à `ready` ou `failed`.
  L'`Idempotency-Key` est liée aux champs **et** à l'empreinte SHA-256 du fichier.
- `GET /v1/documents/{id}/original` sert l'original déchiffré en pièce jointe, avec
  `nosniff`, `CSP sandbox` et `no-store`. Une fois l'original purgé : `410 original_purged`.
- `PATCH /v1/documents/{id}` (`If-Match`) : titre, correction du texte extrait (la
  sensibilité est recalculée), `keep_original`.
- `POST /v1/documents/{id}/reprocess` relance la lecture et remplace une correction.
- `POST /v1/voice/dictations` (champs `file`, `space_id`) répond `202` avec une dictée
  `pending` :
  - suivre `GET /v1/voice/dictations/{id}` jusqu'à `ready` (texte) ou `failed` (`too_long`,
    `unreadable`…) ;
  - puis `DELETE` une fois le texte utilisé ;
  - `503 transcription_unavailable` si la dictée est désactivée (`GET /v1/voice/status`).

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
| `/v1/connectors` | 5 | OAuth, scopes, révocation, synchronisation |
