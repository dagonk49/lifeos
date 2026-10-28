# LifeOS

Assistant personnel et coach, local-first : la mémoire et les décisions d'autorisation restent
sur un serveur personnel ; un LLM cloud (Gemini) n'est sollicité que ponctuellement, avec un
contexte minimal filtré localement.

**Version 1.0.0** — première version livrable (étapes 0 à 4) : voir le
[journal des versions](CHANGELOG.md).

- **Installer** : [docs/installation.md](docs/installation.md) (VM Debian/Proxmox, script
  `infra/scripts/install.sh`)
- **Utiliser** : [docs/guide-utilisateur.md](docs/guide-utilisateur.md)
- Cahier des charges et architecture : [docs/cadrage/](docs/cadrage/)
- Décisions d'architecture, modèle de données, politiques : [docs/](docs/README.md)

## État d'avancement

| Étape | Contenu | État |
|---|---|---|
| 0 — Cadrage | ADR, modèle de données, politique de données, menaces, conventions API | fait |
| 1 — Socle | backend, base, auth passkeys, CI, sauvegarde, observabilité | fait |
| 2 — MVP | client web, chat, tâches, journal, mémoire, recherche, Guardian | fait |
| 3 — Intelligence | recherche hybride (embeddings locaux), qualité de la mémoire, bilans, objectifs, agents spécialisés, maîtrise des coûts Gemini | fait |
| 4 — Multimodal | documents chiffrés et OCR local, tickets de caisse et finances, dictée et lecture vocale, pièces jointes, filtrage local des documents sensibles | fait |
| 5 — Connecteurs | calendrier et e-mail (OAuth), approbations d'envoi, notifications mobiles | en cours : comptes Google connectés (OAuth, jetons chiffrés, consentement par espace) |
| 6 — Stabilisation | tests de sécurité complets, CSP à nonces, exercices de restauration, documentation utilisateur avancée | reporté |

## Structure

```
backend/   API FastAPI, workers Dramatiq, migrations Alembic (voir backend/README.md)
apps/web/  client web Next.js (voir apps/web/README.md)
infra/     Docker Compose, Traefik, PostgreSQL, Prometheus, scripts de sauvegarde
docs/      cadrage, ADR, modèle de données, politiques, exploitation
```

## Démarrage rapide

```bash
infra/scripts/install.sh --domain lifeos.mondomaine.fr --email moi@example.org --name "Moi"
```

Le script crée les secrets et la configuration, construit et démarre les services, télécharge
les modèles locaux (recherche par le sens, dictée), vérifie l'OCR et crée le compte. Ouvrir
ensuite le lien d'enrôlement affiché pour créer la première passkey. Pas à pas :
[docs/installation.md](docs/installation.md) ; exploitation :
[docs/exploitation.md](docs/exploitation.md) ; assistant sans frais :
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

## Ce qu'ajoute l'étape 4

- **Documents** : PDF, photos (JPEG, PNG, WebP, HEIC) et textes.
  - Originaux **chiffrés** sur le serveur, conservés 90 jours par défaut (ou sans limite si
    vous le choisissez).
  - Texte lu **localement** (couche texte des PDF, OCR Tesseract en français), corrigeable,
    retrouvable dans la recherche.
  - Photos nettoyées de leurs métadonnées (GPS, appareil).
- **Documents sensibles** (identité, santé, banque) reconnus localement : leur contenu n'est
  **jamais** envoyé à l'assistant IA.
- **Tickets de caisse** (UC02) : marchand, date et total lus sur la photo, chaque valeur avec
  sa ligne d'origine, à vérifier avant l'ajout au budget.
- **Finances** :
  - catégories et budgets mensuels ;
  - opérations en montants exacts ;
  - classement appris de vos corrections ;
  - budget du mois calculé localement ;
  - import de relevés CSV de la banque : aperçu, choix des colonnes, doublons signalés,
    rien d'enregistré avant validation, annulation possible ;
  - échéances récurrentes, objectifs d'épargne et projection des prochains mois, chaque
    montant marqué connu, prévu ou estimé, avec ses hypothèses ;
  - agent Finances à la demande.
  - Aucune connexion bancaire, aucun paiement.
- **Voix** : dictée dans le journal (UC01) et dans le chat.
  - Elle est transcrite **sur votre serveur** (Whisper, sur CPU ou GTX 1050 Ti), puis
    l'enregistrement est supprimé.
  - « Écouter » lit les réponses avec les voix de l'appareil.
- **Pièces jointes du chat** : seul le texte extrait d'un document non sensible accompagne le
  message.

## Licence

Apache 2.0 — voir [LICENSE](LICENSE).
