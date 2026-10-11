# Documentation LifeOS

| Document | Contenu |
|---|---|
| [cadrage/cahier-des-charges.md](cadrage/cahier-des-charges.md) | Cahier des charges v1.0 (source : document de cadrage du 11/10/2026) |
| [cadrage/architecture-specifications.md](cadrage/architecture-specifications.md) | Architecture et spécifications techniques v1.0 (source) |
| [adr/](adr/) | Décisions d'architecture (ADR) prises au démarrage |
| [modele-de-donnees.md](modele-de-donnees.md) | Modèle de données : règles transverses, tables du socle et du MVP |
| [politique-de-donnees.md](politique-de-donnees.md) | Conservation, purge, transmission cloud, effacement, sauvegardes |
| [modele-de-menaces.md](modele-de-menaces.md) | Actifs, frontières de confiance, menaces et parades |
| [conventions-api.md](conventions-api.md) | Conventions HTTP : versionnage, erreurs, pagination, idempotence, auth |
| [exploitation.md](exploitation.md) | Installation, TLS, exposition réseau, dimensionnement (VM 10 Go), sauvegarde/restauration, supervision |
| [guide-gemini.md](guide-gemini.md) | Brancher Gemini sans aucun frais (projet sans facturation, quotas) |

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

## Points encore ouverts

Ces points sont listés dans l'architecture (§14) ; les valeurs par défaut retenues ici sont
marquées « à valider » dans les documents concernés.

- ~~Modèle Gemini et mode de facturation~~ : tranché par l'ADR 0007 (Developer API, niveau
  gratuit sans compte de facturation, plafonds quotidiens).
- Catégories de données sensibles jamais transmises au cloud.
- Durées de conservation : documents originaux, journal d'audit, sauvegardes.
- ~~Matériel Proxmox~~ : i7-6700, 10 Go, GTX 1050 Ti → embeddings sur CPU (ADR 0006) ; usage
  du GPU pour la transcription à évaluer à l'étape 4.
- Périmètre Android.
