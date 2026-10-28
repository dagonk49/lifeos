# Documentation LifeOS

| Document | Contenu |
|---|---|
| [installation.md](installation.md) | **Installation pas à pas** de la version 1.0 sur la VM (Docker, domaine, script, Gemini, GPU, sauvegardes, alertes, mises à jour) |
| [guide-utilisateur.md](guide-utilisateur.md) | **Guide d'utilisation** : pages, principes, ce que l'assistant voit et ne voit jamais |
| [cadrage/cahier-des-charges.md](cadrage/cahier-des-charges.md) | Cahier des charges v1.0 (source : document de cadrage du 11/10/2026) |
| [cadrage/architecture-specifications.md](cadrage/architecture-specifications.md) | Architecture et spécifications techniques v1.0 (source) |
| [adr/](adr/) | Décisions d'architecture (ADR) prises au démarrage |
| [modele-de-donnees.md](modele-de-donnees.md) | Modèle de données : règles transverses, tables du socle, du MVP, de l'intelligence et du multimodal |
| [politique-de-donnees.md](politique-de-donnees.md) | Conservation, purge, transmission cloud, effacement, sauvegardes |
| [modele-de-menaces.md](modele-de-menaces.md) | Actifs, frontières de confiance, menaces et parades |
| [recette-securite.md](recette-securite.md) | **Recette de sécurité** : scénarios interdits (inter-espaces, permissions, sessions volées, CSRF, injections, rejeu d'approbations), codes, événements d'audit et tests |
| [conventions-api.md](conventions-api.md) | Conventions HTTP : versionnage, erreurs, pagination, idempotence, auth |
| [exploitation.md](exploitation.md) | Installation, TLS, exposition réseau, dimensionnement (VM 10 Go), sauvegardes automatiques (copie hors site, tests, alertes), supervision et alertes (Prometheus, Alertmanager, ntfy, signal de vie), mises à jour avec sauvegarde préalable et retour arrière |
| [recette-serveur.md](recette-serveur.md) | **Recette sur la machine cible** : mesures automatiques (temps, pointes de mémoire), liste de recette (HTTPS, passkeys sur les vrais appareils, OCR, dictée, alertes, mise à jour), journal |
| [restauration.md](restauration.md) | **Plan de reprise** : restauration complète sur une VM vierge pas à pas, originaux manquants, exercice annuel et son journal |
| [guide-gemini.md](guide-gemini.md) | Brancher Gemini sans aucun frais (projet sans facturation, quotas) |
| [guide-google.md](guide-google.md) | Connecter un compte Google (Agenda, Gmail) : projet, écran de consentement, client OAuth, révocation |

## Décisions d'architecture

| ADR | Titre | Statut |
|---|---|---|
| [0001](adr/0001-monorepo-monolithe-modulaire.md) | Monorepo et monolithe modulaire | Acceptée |
| [0002](adr/0002-authentification-integree-passkeys.md) | Authentification intégrée par passkeys | Acceptée |
| [0003](adr/0003-gemini-derriere-une-abstraction.md) | Gemini comme fournisseur LLM, derrière une abstraction | Acceptée |
| [0004](adr/0004-taches-asynchrones-dramatiq.md) | Tâches asynchrones : Dramatiq + Redis, planification APScheduler | Acceptée |
| [0005](adr/0005-deploiement-compose-traefik.md) | Déploiement Docker Compose, Traefik sans socket Docker | Acceptée |
| [0006](adr/0006-embeddings-locaux.md) | Embeddings locaux (EmbeddingGemma, ONNX) et recherche hybride | Acceptée |
| [0007](adr/0007-couts-gemini-niveau-gratuit.md) | Coûts Gemini : niveau gratuit sans facturation possible, quotas côté LifeOS | Acceptée |
| [0008](adr/0008-documents-ocr-local.md) | Documents : originaux chiffrés, lecture et OCR en local | Acceptée |
| [0009](adr/0009-voix-locale.md) | Voix : dictée transcrite en local, lecture par les voix de l'appareil | Acceptée |
| [0010](adr/0010-reservation-des-quotas-llm.md) | Plafonds IA fiables : réservation atomique des quotas avant l'appel | Acceptée |
| [0011](adr/0011-antivirus-et-fichiers-hostiles.md) | Fichiers hostiles : confinement et limites, pas d'antivirus pour l'instant | Acceptée |
| [0012](adr/0012-connecteurs-oauth.md) | Comptes connectés : OAuth 2.0 avec PKCE, jetons chiffrés, consentement par espace | Acceptée |
| [0013](adr/0013-taches-recurrentes.md) | Tâches récurrentes : utiles, mais après les dépendances et sous une forme étroite | Acceptée (mise en œuvre reportée) |
| [0014](adr/0014-finances-import-et-projections.md) | Finances : import CSV en aperçu côté serveur, clé d'import, projections locales explicables | Acceptée |
| [0015](adr/0015-notifications-adaptatives.md) | Notifications adaptatives : politique de livraison pure, suggestions regroupées, permission explicite pour les alertes critiques | Acceptée |
| [0016](adr/0016-ecritures-externes-sous-approbation.md) | Écritures chez un fournisseur : approbation liée aux paramètres, réservation unique, rapprochement | Acceptée |
| [0017](adr/0017-lecture-des-e-mails.md) | Lecture des e-mails : libellés choisis, texte brut, jamais une instruction | Acceptée |

## Points encore ouverts

Ces points sont listés dans l'architecture (§14) ; les valeurs par défaut retenues ici sont
marquées « à valider » dans les documents concernés.

- ~~Modèle Gemini et mode de facturation~~ : tranché par l'ADR 0007 (Developer API, niveau
  gratuit sans compte de facturation, plafonds quotidiens).
- ~~Catégories de données sensibles jamais transmises au cloud~~ : identité, santé, banque,
  détectées localement (ADR 0008, politique de données §3).
- Durées de conservation : ~~documents originaux~~ (90 jours par défaut, « conserver » au
  choix), journal d'audit, sauvegardes.
- ~~Matériel Proxmox~~ : i7-6700, 10 Go, GTX 1050 Ti → embeddings sur CPU (ADR 0006),
  transcription sur CPU ou sur la carte en option (ADR 0009).
- Périmètre Android.
