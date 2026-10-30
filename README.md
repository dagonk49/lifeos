# LifeOS

Assistant personnel et coach, local-first : la mémoire et les décisions d'autorisation restent
sur un serveur personnel ; un LLM cloud (Gemini) n'est sollicité que ponctuellement, avec un
contexte minimal filtré localement.

**Version 1.0.0** — première version livrable (étapes 0 à 4) ; les étapes 5 et 6 sont en
cours (section « Non publié » du [journal des versions](CHANGELOG.md)).

- **Installer** : [docs/installation.md](docs/installation.md) (VM Debian/Proxmox, script
  `infra/scripts/install.sh`)
- **Utiliser** : [docs/guide-utilisateur.md](docs/guide-utilisateur.md)
- **Dépanner** : [docs/depannage.md](docs/depannage.md)
- Cahier des charges et architecture : [docs/cadrage/](docs/cadrage/) ; ce qui en est
  réalisé : [docs/realisation.md](docs/realisation.md)
- Décisions d'architecture, modèle de données, politiques : [docs/](docs/README.md)

## État d'avancement

| Étape | Contenu | État |
|---|---|---|
| 0 — Cadrage | ADR, modèle de données, politique de données, menaces, conventions API | fait |
| 1 — Socle | backend, base, auth passkeys, CI, sauvegarde, observabilité | fait |
| 2 — MVP | client web, chat, tâches, journal, mémoire, recherche, Guardian | fait |
| 3 — Intelligence | recherche hybride (embeddings locaux), qualité de la mémoire, bilans, objectifs, agents spécialisés, maîtrise des coûts Gemini | fait |
| 4 — Multimodal | documents chiffrés et OCR local, tickets de caisse et finances, dictée et lecture vocale, pièces jointes, filtrage local des documents sensibles | fait |
| 5 — Connecteurs | calendrier et e-mail (OAuth), approbations d'envoi, notifications mobiles | en cours : comptes Google connectés, lecture de l'agenda, modifications d'agenda sous approbation, lecture de Gmail, notifications adaptatives dans l'application ; prévu : envoi d'e-mails sous approbation, notifications push |
| 6 — Stabilisation | tests de sécurité complets, CSP à nonces, exercices de restauration, documentation utilisateur avancée | en cours : recette de sécurité, CSP à nonces, sauvegardes hors site et restauration scriptée, alertes, mises à jour avec retour arrière, export complet, guides d'exploitation et de dépannage ; reste : exercice de restauration réel, recette sur la machine cible, accessibilité |

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
  correction, souvenirs protégés, export complet, effacement.
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

## Ce qu'ajoutent les étapes 5 et 6 (en cours, non publié)

- **Comptes Google connectés** (OAuth, jetons chiffrés, usages autorisés espace par espace) :
  - **agenda** lu toutes les 15 minutes, page Agenda, conflits et créneaux libres calculés
    localement ;
  - **modifications d'agenda** (créer, déplacer, annuler) seulement après approbation de
    l'aperçu exact, jamais en double ;
  - **e-mails** des libellés choisis copiés en texte brut, jamais traités comme des
    instructions, supprimés après 90 jours.
- **Tâches** : dépendances, état bloqué, étiquettes.
- **Notifications adaptatives** dans l'application : créneaux, maximum par jour,
  suggestions regroupées, alertes critiques pendant la tranquillité sur autorisation.
- **Export complet** : archive ZIP (JSON, CSV, ICS, Markdown, originaux sur demande).
- **Exploitation** : sauvegardes hors site, restauration scriptée, alertes sur le téléphone,
  mises à jour avec retour arrière, derrière un reverse proxy existant.
- **Sécurité** : CSP à nonce, recette de sécurité, fichiers hostiles lus dans un processus
  confiné.

Pas encore : envoi d'e-mails, notifications push sur le téléphone, application mobile
native ([guide d'utilisation](docs/guide-utilisateur.md#ce-que-cette-version-ne-fait-pas-encore)).

## Licence

Apache 2.0 — voir [LICENSE](LICENSE).
