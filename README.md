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
| 3 — Intelligence | recherche hybride (embeddings locaux), qualité de la mémoire, bilans, objectifs, agents spécialisés, maîtrise des coûts Gemini | fait |

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
[docs/exploitation.md](docs/exploitation.md). Recherche par le sens :
`docker compose --profile setup run --rm ml-download` (une fois). Assistant sans frais :
[docs/guide-gemini.md](docs/guide-gemini.md).

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

## Ce qu'ajoute l'étape 3

- **Recherche hybride** : par les mots et par le sens (modèle d'embeddings **local**, aucun
  envoi au cloud), filtres d'espace et de période, expressions comme « la semaine dernière ».
- **Mémoire** : file « À vérifier » (doublons, contradictions possibles, préférences
  anciennes à confirmer), consolidation d'une conversation en faits proposés puis approuvés.
- **Objectifs** mesurables ou qualitatifs, jalons, points d'étape, progression calculée
  localement, tâches rattachées.
- **Bilans** quotidiens, hebdomadaires, mensuels : faits calculés localement, séparés de la
  lecture de l'assistant (hypothèses présentées comme telles) ; automatiques sur demande.
- **Agents spécialisés à la demande** (planification, coaching, mémoire) avec accès explicite,
  limité aux ressources utiles, 90 jours, révocable.
- **Coûts maîtrisés** : Gemini sans compte de facturation (zéro frais possible), plafonds
  quotidiens par palier, arrêt d'urgence, suivi de la consommation.

## Licence

Apache 2.0 — voir [LICENSE](LICENSE).
