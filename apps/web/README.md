# LifeOS — client web

Next.js (App Router) + TypeScript strict + Tailwind CSS, données via TanStack Query.
Toutes les pages sont rendues côté client et parlent uniquement à l'API `/v1` de la même
origine (cookie de session `SameSite=Strict`).

| Écran | Rôle |
|---|---|
| `/login`, `/enroll` | connexion et enrôlement par passkey (WebAuthn) |
| `/` | tableau de bord du jour, calculé localement (fonctionne sans IA) |
| `/chat` | assistant ; mode dégradé explicite, « Conserver » (hors purge 90 j), « Mémoriser » |
| `/tasks`, `/journal` | tâches et rappels ; journal avec extraction de propositions à valider |
| `/memory` | faits (origine, confiance, historique de correction) et souvenirs (protection, révisions), export |
| `/search` | recherche plein texte, filtres type/période/espace |
| `/approvals` | actions en attente de votre accord, paramètres exacts |
| `/settings` | consentement IA par espace, plages de tranquillité, budget IA, passkeys, sessions, permissions d'agents, audit |

Les opérations sensibles (consentement IA, permissions, passkeys) déclenchent une nouvelle
vérification par passkey si la connexion date de plus de 10 minutes.

## Développement

```bash
pnpm install
pnpm dev            # http://localhost:3000, /v1 relayé vers http://127.0.0.1:8000 (LIFEOS_API_URL)
```

Le backend doit tourner avec `LIFEOS_PUBLIC_ORIGIN=http://localhost:3000`,
`LIFEOS_WEBAUTHN_RP_ID=localhost` et `LIFEOS_COOKIE_SECURE=false` (voir `backend/README.md`).

## Tests

```bash
pnpm typecheck
pnpm test           # Vitest : client API, ré-authentification, revue d'approbation
pnpm build && pnpm e2e
```

`pnpm e2e` (Playwright) démarre l'API (fournisseur LLM factice, base `lifeos_e2e`) et le
serveur Next, recrée le schéma par les migrations, crée le propriétaire par la CLI puis
déroule le parcours MVP avec un authentificateur WebAuthn virtuel de Chromium. Prérequis :
PostgreSQL avec la base `lifeos_e2e` (extensions `vector` et `unaccent`) et Redis locaux.
Pour utiliser un Chromium déjà installé : `PLAYWRIGHT_CHROMIUM_PATH=/chemin/vers/chrome`.
