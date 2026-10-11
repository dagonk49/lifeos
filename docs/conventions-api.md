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
- `429 llm_budget_exceeded` : budget mensuel de jetons LLM atteint.

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
| `/v1/search` | 2 | recherche plein texte filtrée par espace et période |
| `/v1/approvals` | 2 | prévisualisation, accepter / refuser |
| `/v1/guardian` | 2 | permissions d'agents, journal d'audit, appels LLM |
| `/v1/notifications` | 2 | liste, lecture, préférences (plages de tranquillité) |
| `/v1/dashboard` | 2 | briefing du jour |
| `/v1/goals` | 3 | objectifs, jalons, bilans |
| `/v1/connectors` | 5 | OAuth, scopes, révocation, synchronisation |
