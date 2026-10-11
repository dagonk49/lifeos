Docker secrets (one value per file, never committed). Create them with
`infra/scripts/init-secrets.sh`:

| File | Content |
|---|---|
| `postgres_superuser_password` | PostgreSQL superuser (init, backups) |
| `lifeos_database_password` | application role `lifeos` |
| `lifeos_gemini_api_key` | Gemini Developer API key (empty if unused) |
| `lifeos_hf_token` | Hugging Face read token, only to download a gated embedding model (usually empty) |
