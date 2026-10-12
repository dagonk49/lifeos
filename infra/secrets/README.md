Docker secrets (one value per file, never committed). Create them with
`infra/scripts/init-secrets.sh`:

| File | Content |
|---|---|
| `postgres_superuser_password` | PostgreSQL superuser (init, backups) |
| `lifeos_database_password` | application role `lifeos` |
| `lifeos_documents_key` | AES-256-GCM key of the document originals: base64 of 32 random bytes. Keep a copy outside the backups (lost key = unreadable originals; never rotate it by overwriting the file) |
| `lifeos_gemini_api_key` | Gemini Developer API key (empty if unused) |
| `lifeos_hf_token` | Hugging Face read token, only to download a gated embedding model (usually empty) |
