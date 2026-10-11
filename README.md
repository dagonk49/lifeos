# LifeOS

Assistant personnel et coach, local-first : la mémoire et les décisions d'autorisation restent
sur un serveur personnel ; un LLM cloud (Gemini) n'est sollicité que ponctuellement, avec un
contexte minimal filtré localement.

- Cahier des charges et architecture : [docs/cadrage/](docs/cadrage/)
- Décisions d'architecture, modèle de données, politiques : [docs/](docs/README.md)

## État d'avancement

| Étape | Contenu | État |
|---|---|---|
| 0 — Cadrage | ADR, modèle de données, politique de données, menaces, conventions API | fait |
| 1 — Socle | backend, base, auth passkeys, CI, sauvegarde, observabilité | fait |
| 2 — MVP | client web, chat, tâches, journal, mémoire, recherche, Guardian | fait |
| 3 — Intelligence | RAG hybride (embeddings locaux), bilans, objectifs, agents spécialisés | à venir |

## Structure

```
backend/   API FastAPI, workers Dramatiq, migrations Alembic (voir backend/README.md)
apps/web/  client web Next.js (voir apps/web/README.md)
infra/     Docker Compose, Traefik, PostgreSQL, Prometheus, scripts de sauvegarde
docs/      cadrage, ADR, modèle de données, politiques, exploitation
```

## Démarrage rapide

```bash
cd infra
./scripts/init-secrets.sh && cp .env.example .env   # puis adapter .env
docker compose up -d --build
docker compose exec api lifeos-admin create-owner --email moi@example.org --name "Moi"
```

Puis ouvrir le lien d'enrôlement affiché pour créer la première passkey. Détails :
[docs/exploitation.md](docs/exploitation.md).

## Ce que fait le MVP

- Connexion par passkey uniquement, sessions révocables, liste des appareils.
- Tableau de bord du jour, tâches avec rappels respectant les plages de tranquillité.
- Assistant (Gemini) autorisé **espace par espace** ; contexte minimal, masquage local des
  données sensibles, budget mensuel ; si l'IA est indisponible, tout le reste fonctionne.
- Journal : l'assistant **propose** tâches et faits, rien n'est enregistré sans votre accord.
- Mémoire : faits avec origine (déclaré / confirmé / extrait / inféré) et historique de
  correction, souvenirs protégés, export JSON complet, effacement.
- Conversations brutes purgées à 90 jours sauf « Conserver ».
- Guardian : politique d'autonomie hors LLM, approbations aux paramètres exacts, permissions
  d'agents limitées et révocables, journal d'audit sans contenu.

## Licence

Apache 2.0 — voir [LICENSE](LICENSE).
