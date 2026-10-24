Docker secrets (one value per file, never committed). Create them with
`infra/scripts/init-secrets.sh`:

| File | Content |
|---|---|
| `postgres_superuser_password` | PostgreSQL superuser (init, backups) |
| `lifeos_database_password` | application role `lifeos` |
| `lifeos_documents_key` | AES-256-GCM key of the document originals: base64 of 32 random bytes. Keep a copy outside the backups (lost key = unreadable originals; never rotate it by overwriting the file) |
| `lifeos_gemini_api_key` | Gemini Developer API key (empty if unused) |
| `lifeos_hf_token` | Hugging Face read token, only to download a gated embedding model (usually empty) |
| `lifeos_connectors_key` | AES-256-GCM key of the OAuth tokens of the connected accounts: base64 of 32 random bytes. Losing it only forces reconnecting the accounts; rotation: `2:<new>,1:<old>` (docs/exploitation.md) |
| `lifeos_google_client_secret` | secret of the Google OAuth client (empty unless `LIFEOS_CONNECTORS_PROVIDER=google`) |
| `alertmanager.yml` | Alertmanager configuration with the alert channel's tokens (ntfy, SMTP, Telegram) and the dead man's switch URL: written by `infra/scripts/install-monitoring.sh` (run it again rather than editing it), not by `init-secrets.sh`; profile `monitoring` only |

Backups never hold these files. On a new machine (docs/restauration.md), only
`lifeos_documents_key` must be put back, from the recovery sheet (`infra/scripts/export-keys.sh`),
**before** `install.sh` or `init-secrets.sh` run: otherwise a new key is generated and the
restored originals cannot be decrypted. The other files are created anew (the database dump
holds no role passwords; the Gemini key can be created again).
