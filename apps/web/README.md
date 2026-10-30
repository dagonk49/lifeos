# LifeOS — client web

Next.js (App Router) + TypeScript strict + Tailwind CSS, données via TanStack Query.
Toutes les pages sont rendues côté client et parlent uniquement à l'API `/v1` de la même
origine (cookie de session `SameSite=Strict`).

| Écran | Rôle |
|---|---|
| `/login`, `/enroll` | connexion et enrôlement par passkey (WebAuthn) |
| `/` | tableau de bord du jour, calculé localement (fonctionne sans IA) |
| `/chat` | assistant et agents spécialisés ; pièces jointes, dictée, contrôle avant envoi, mode dégradé explicite, « Conserver » (hors purge 90 j), « Mémoriser » |
| `/tasks` | tâches, rappels, dépendances et étiquettes |
| `/agenda` | semaine des agendas suivis, conflits, créneaux libres, modifications sous approbation |
| `/mail` | e-mails copiés des libellés suivis, en texte brut ; import d'une pièce jointe dans Documents |
| `/journal` | journal (et dictée) avec extraction de propositions à valider |
| `/goals` | objectifs, jalons, points d'étape, objectifs d'épargne |
| `/documents` | envoi, lecture locale, correction du texte, sensibilité, conservation de l'original |
| `/finances` | budgets, opérations, tickets, import de relevés CSV, échéances, projection |
| `/reviews` | bilans quotidiens, hebdomadaires, mensuels |
| `/memory` | faits (origine, confiance, historique de correction), souvenirs (protection, révisions), file « À vérifier » |
| `/search` | recherche plein texte et par le sens, filtres type/période/espace |
| `/approvals` | actions en attente de votre accord, paramètres exacts |
| `/settings` | consentement IA par espace, préférences et plages de tranquillité, notifications, usage IA, agents, comptes connectés, passkeys, sessions, permissions d'agents, audit, « Mes données » (archive ZIP JSON/CSV/ICS/Markdown, originaux déchiffrés sur confirmation par passkey) |
| `/connexions/retour` | retour du consentement OAuth du fournisseur (transmet le code à l'API) |

Les opérations sensibles (consentement IA, permissions, passkeys) déclenchent une nouvelle
vérification par passkey si la connexion date de plus de 10 minutes.

## Sécurité : Content Security Policy

La CSP est produite **à chaque requête** par `src/proxy.ts` (règles dans `src/lib/csp.ts`) :
un nonce aléatoire de 128 bits, transmis à Next.js par l'en-tête de requête
`Content-Security-Policy`, que Next.js pose sur tous ses scripts (framework, morceaux de
page, données d'hydratation). Avec `script-src 'self' 'nonce-…' 'strict-dynamic'`, un script
injecté dans la page, sans ce nonce, ne s'exécute pas, pas plus qu'un gestionnaire
d'événement en ligne (`onerror="…"`). `'unsafe-eval'` n'est ajouté qu'en développement
(`pnpm dev`).

- Toutes les pages sont rendues à chaque requête (`await connection()` dans
  `src/app/layout.tsx`) : une page prérendue au build n'aurait pas le nonce et ses scripts
  seraient bloqués. `pnpm build` doit marquer chaque page `ƒ`. Seuls `/icon.svg` et la page
  d'erreur 500 intégrée de Next.js restent statiques ; servie telle quelle, celle-ci perd ses
  scripts mais garde son texte et son bouton de rechargement (un simple formulaire).
- Jamais de script en ligne écrit à la main (ni `dangerouslySetInnerHTML`, déjà exclu par
  T14). Si un `<Script>` devient nécessaire, lui passer le nonce lu dans l'en-tête de requête
  `x-nonce` (`headers()` dans un composant serveur).
- `style-src` garde `'unsafe-inline'`, sans nonce (qui l'annulerait) : un attribut
  `style="…"` ne peut pas porter de nonce, et Next.js en met dans ses pages 404 et 500
  intégrées. Un style injecté ne peut ni exécuter de code ni joindre un autre site.
- Pas de CSP dans `next.config.ts` : deux politiques s'appliquent ensemble, et une politique
  sans le nonce bloquerait tous les scripts. Pour la même raison, aucun reverse proxy en
  amont ne doit en ajouter (voir `docs/exploitation.md` §3).
- Le proxy ignore `/v1` (l'API, qui pose ses propres en-têtes) et `/_next/static`.

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
pnpm test           # Vitest : fonctions de src/lib et composants (fichiers *.test.ts(x) voisins)
pnpm build && pnpm e2e
```

`pnpm e2e` (Playwright) démarre l'API (fournisseurs factices pour le LLM, la dictée, les
embeddings et les comptes connectés ; base `lifeos_e2e`) et le serveur Next, recrée le schéma
par les migrations, puis déroule les parcours en série, chacun avec sa propre passkey
(`lifeos-admin enrollment-token`, authentificateur WebAuthn virtuel de Chromium) :
`mvp.spec.ts` (MVP), `step3.spec.ts` (intelligence), `step4.spec.ts` (multimodal),
`step5.spec.ts` (compte connecté, usages par espace, agenda suivi, déconnexion) et
`security.spec.ts` (CSP, session d'un appareil perdu).
`e2e/security.spec.ts` vérifie la CSP sur le build de production : un nonce neuf sur chaque
script de chaque page, un script injecté bloqué, les passkeys, la navigation et l'assistant
fonctionnels. Tous les parcours échouent à la moindre violation de CSP (`e2e/fixtures.ts`).
Prérequis : PostgreSQL avec la base `lifeos_e2e` (extensions `vector` et `unaccent`) et Redis
locaux.
Pour utiliser un Chromium déjà installé : `PLAYWRIGHT_CHROMIUM_PATH=/chemin/vers/chrome`.
