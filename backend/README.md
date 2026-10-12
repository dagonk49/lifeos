# LifeOS — backend

FastAPI + SQLAlchemy (async) + Alembic + Dramatiq. Python 3.13, géré avec `uv`.

```
src/lifeos/
  main.py            application FastAPI (factory)
  config.py          paramètres LIFEOS_* (+ secrets Docker)
  cli.py             lifeos-admin : create-owner, enrollment-token, revoke-sessions
  api/               dépendances, middlewares (request id, CSRF Origin), santé
  core/              erreurs RFC 9457, jetons
  db/                base déclarative, session, registre des modèles
  modules/
    identity/        utilisateurs, espaces, passkeys WebAuthn, sessions
    audit/           journal d'audit append-only
    llm/             abstraction LLM : gemini, fake, disabled
  workers/           broker Dramatiq, acteurs, planificateur APScheduler
migrations/          Alembic
tests/               pytest (base PostgreSQL réelle, authentificateur WebAuthn logiciel)
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
`LIFEOS_LLM_PROVIDER=fake`.

## Vérifications (identiques à la CI)

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
LIFEOS_DATABASE_PASSWORD=... uv run pytest      # base `lifeos_test`, Redis db 15
uv run alembic check                            # modèles et migrations alignés
```

Les tests recréent le schéma par les migrations (`downgrade base` puis `upgrade head`) et
vident les tables après chaque test.
