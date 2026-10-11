#!/bin/bash
# Runs once, on first start with an empty data directory, as the superuser.
# The application role owns the database but is not a superuser (ADR 0005).
set -euo pipefail

app_password="$(cat /run/secrets/lifeos_database_password)"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_password="$app_password" <<'EOSQL'
CREATE ROLE lifeos LOGIN PASSWORD :'app_password';
ALTER DATABASE lifeos OWNER TO lifeos;
ALTER SCHEMA public OWNER TO lifeos;
-- `vector` is not a trusted extension: create it here, as superuser.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;
EOSQL
