# ADR 0001 — Monorepo et monolithe modulaire

- Statut : acceptée ; précisée le 2026-10-30 (aucun client mobile à ce jour)
- Date : 2026-10-11

## Contexte

L'architecture (§1) demande une application monolithique modulaire au départ, sans multiplier
les microservices. Le projet comporte un backend Python, un client web, plus tard un client
mobile, et une infrastructure Docker Compose.

## Décision

Un seul dépôt :

```
backend/      API FastAPI, workers, migrations (package Python `lifeos`)
apps/web/     client web Next.js
apps/mobile/  client Expo (plus tard ; n'existe pas à ce jour, voir Conséquences)
infra/        Docker Compose, Traefik, Postgres, Prometheus, scripts d'exploitation
docs/         cadrage, ADR, modèle de données, politiques
```

Le backend est **un seul processus applicatif** (déployé en trois rôles : `api`, `worker`,
`scheduler`) découpé en modules sous `lifeos.modules` :

| Module | Rôle |
|---|---|
| `identity` | utilisateurs, espaces, passkeys, sessions, appareils |
| `audit` | journal d'audit append-only |
| `guardian` | moteur de politique hors LLM, approbations, filtrage avant cloud (Privacy Engine) |
| `llm` | abstraction des fournisseurs (Gemini, factice) |
| `chat`, `tasks`, `journal`, `memory`, `search`, `notifications`, `dashboard` | fonctionnalités MVP |

Règles :

- un module n'importe que l'interface publique (`service.py`, `schemas.py`) d'un autre module,
  jamais ses modèles SQLAlchemy ;
- tout accès aux données métier passe par un service qui reçoit le `Principal` courant
  (utilisateur, agent ou système) et applique le filtrage par espace ;
- les fournisseurs LLM ne sont jamais appelés directement par un module métier : uniquement via
  la passerelle cloud du Guardian.

Outillage retenu : Python 3.13, `uv`, `ruff` (lint + format), `mypy --strict`, `pytest`,
SQLAlchemy 2 (async, `asyncpg`), Alembic. Côté web : `pnpm`, TypeScript strict, Vitest,
Playwright. Code et identifiants en anglais, documentation en français.

## Conséquences

- Un seul déploiement, une seule base, des transactions simples entre modules.
- Les frontières de modules ne sont pas imposées par le réseau : elles doivent l'être par revue
  de code et par les tests (séparation des espaces, autorisations).
- Extraire un module en service séparé restera possible si un besoin mesuré l'impose.
- Précision (2026-10-30) : `apps/mobile/` n'a pas été créé. Le client web, utilisable dans le
  navigateur du téléphone, est le seul client ; l'existence et la forme d'un client mobile
  restent à décider (périmètre Android, [docs/README.md](../README.md#points-encore-ouverts)).

## Alternatives écartées

- Microservices dès le départ : coût d'exploitation disproportionné pour un usage personnel.
- Dépôts séparés backend/web : synchronisation des contrats d'API plus pénible.
