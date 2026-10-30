# LifeOS — backend

FastAPI + SQLAlchemy (async) + Alembic + Dramatiq. Python 3.13, géré avec `uv`.

```
src/lifeos/
  main.py            application FastAPI (factory), routeur /v1
  config.py          paramètres LIFEOS_* (+ secrets Docker)
  cli.py             lifeos-admin : create-owner, enrollment-token, revoke-sessions, verify-data,
                     reconcile-originals, replay-erasures, sensitivity-check
  integrity.py       contrôles de la base et des originaux (verify-data, reconcile-originals)
  isolation.py       lecture des fichiers non fiables dans un processus enfant confiné (ADR 0011)
  logging.py         journaux JSON structurés, sans contenu
  api/               dépendances, middlewares (request id, CSRF Origin), santé, refus audités
  core/              erreurs RFC 9457, jetons, chiffrement AES-256-GCM, idempotence, pagination
  db/                base déclarative, session, registre des modèles
  observability/     métriques Prometheus
  modules/
    identity/        utilisateurs, espaces, passkeys WebAuthn, sessions
    settings/        espaces (consentement IA) et préférences (/v1/spaces, /v1/settings)
    guardian/        politique d'autonomie, approbations, permissions d'agents, passerelle LLM
                     (consentement, masquage, quotas), opérations externes
    audit/           journal d'audit append-only
    llm/             abstraction LLM : gemini, fake, disabled
    agents/          agents spécialisés (planification, coaching, mémoire, finances)
    chat/            conversations, pièces jointes, contrôle avant envoi, consolidation
    tasks/           tâches, rappels, dépendances, étiquettes
    journal/         entrées et extraction de propositions
    memory/          faits, souvenirs, qualité de la mémoire
    retrieval/       embeddings et contexte (recherche par le sens)
    search/          recherche hybride
    goals/           objectifs, jalons, objectifs d'épargne
    reviews/         bilans quotidiens, hebdomadaires, mensuels
    dashboard/       briefing du jour
    documents/       documents chiffrés, lecture locale, sensibilité
    voice/           dictées
    finance/         opérations, budgets, tickets, import CSV, échéances, projection
    notifications/   notifications et politique de livraison
    privacy/         export complet (JSON, ZIP, CSV, ICS) et effacement propagé
    connectors/      comptes connectés (ADR 0012) : OAuth 2.0 + PKCE, jetons scellés,
                     consentements par espace ; fournisseurs google, fake
    calendar/        agendas suivis, synchronisation, écritures sous approbation
    mail/            libellés suivis, copies des e-mails en texte brut
    planning/        conflits d'agenda et créneaux libres
  workers/           broker Dramatiq, acteurs, planificateur APScheduler
  ml/                service ml : embeddings ONNX, extraction de texte et OCR (lifeos-ml)
  stt/               service stt : transcription locale (lifeos-stt)
migrations/          Alembic (révisions séquentielles écrites à la main)
tests/               pytest (base PostgreSQL réelle, authentificateur WebAuthn logiciel)
tests_ml/, tests_stt/  tests des services ml et stt (extras `ml`, `stt`)
```

## Développement

```bash
# PostgreSQL + Redis locaux (depuis infra/)
docker compose -f compose.yaml -f compose.dev.yaml up -d postgres redis
docker compose exec postgres createdb -U postgres -O lifeos lifeos_test
docker compose exec postgres psql -U postgres -d lifeos_test \
  -c "CREATE EXTENSION vector" -c "CREATE EXTENSION unaccent"

cd backend
uv sync
export LIFEOS_DATABASE_PASSWORD="$(cat ../infra/secrets/lifeos_database_password)"
uv run alembic upgrade head
uv run uvicorn lifeos.main:create_app --factory --reload   # API sur :8000
uv run dramatiq lifeos.workers.actors                      # worker
uv run lifeos-scheduler                                    # planificateur
```

Variables utiles en développement : `LIFEOS_COOKIE_SECURE=false`, `LIFEOS_LOG_FORMAT=console`,
`LIFEOS_LLM_PROVIDER=fake`, `LIFEOS_CONNECTORS_PROVIDER=fake` avec
`LIFEOS_CONNECTORS_KEY=$(head -c 32 /dev/urandom | base64)` (compte « Démo » sans réseau).

## Vérifications (identiques à la CI)

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
LIFEOS_DATABASE_PASSWORD=... uv run pytest      # base `lifeos_test`, Redis db 15
uv run alembic check                            # modèles et migrations alignés
```

Les tests recréent le schéma par les migrations (`downgrade base` puis `upgrade head`) et
vident les tables après chaque test.
