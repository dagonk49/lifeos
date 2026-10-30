# État de réalisation par rapport au cadrage

Les deux documents de [cadrage](cadrage/) (11/10/2026) restent la source des exigences : ils
ne sont pas réécrits. Cette page dit, à la date du 30/10/2026 (version en développement après
la 1.0.0), ce qui a été retenu à la place des technologies proposées et ce qui est réalisé
de chaque exigence. Les statuts « Réalisé », « Partiel » et « Non réalisé » sont une
appréciation **à valider par le propriétaire**.

## 1. Technologies proposées et retenues

[Architecture §2](cadrage/architecture-specifications.md#2-stack-technologique-recommandée).

| Couche | Proposé | Retenu | Décision |
|---|---|---|---|
| Client web | TypeScript, React, Next.js | Next.js (App Router), React, TypeScript strict, TanStack Query | — |
| Client mobile | React Native + Expo | **aucun** : client web dans le navigateur du téléphone ; décision à prendre (périmètre Android) | [ADR 0001](adr/0001-monorepo-monolithe-modulaire.md) |
| Design system | Tailwind CSS + shadcn/ui | Tailwind CSS et composants maison (`apps/web/src/components/ui.tsx`) | — |
| API et orchestration | Python 3, FastAPI, Pydantic | Python 3.13, FastAPI, Pydantic, SQLAlchemy asynchrone, Alembic | [ADR 0001](adr/0001-monorepo-monolithe-modulaire.md) |
| Base principale | PostgreSQL | PostgreSQL (image pgvector) | [ADR 0005](adr/0005-deploiement-compose-traefik.md) |
| Recherche sémantique | pgvector | pgvector + recherche plein texte PostgreSQL (recherche hybride) | [ADR 0006](adr/0006-embeddings-locaux.md) |
| Cache / files | Redis + Celery (ou Dramatiq) | Redis + **Dramatiq** | [ADR 0004](adr/0004-taches-asynchrones-dramatiq.md) |
| Fichiers | MinIO ou volumes chiffrés | **volume Docker**, originaux chiffrés AES-256-GCM par l'application ; pas de MinIO | [ADR 0008](adr/0008-documents-ocr-local.md) |
| LLM cloud | API compatible outils et sorties structurées | Gemini (Developer API, niveau gratuit sans facturation) derrière une abstraction | [ADR 0003](adr/0003-gemini-derriere-une-abstraction.md), [0007](adr/0007-couts-gemini-niveau-gratuit.md), [0010](adr/0010-reservation-des-quotas-llm.md) |
| Embeddings | sentence-transformers local | EmbeddingGemma-300m en ONNX Runtime (fastembed), service interne `ml`, sans PyTorch | [ADR 0006](adr/0006-embeddings-locaux.md) |
| Transcription | faster-whisper local | faster-whisper (large-v3-turbo), service interne `stt`, CPU ou GPU | [ADR 0009](adr/0009-voix-locale.md) |
| Synthèse vocale | Piper local, option cloud | **voix locales de l'appareil** (navigateur) ; pas de Piper | [ADR 0009](adr/0009-voix-locale.md) |
| OCR / documents | OCRmyPDF + Tesseract ; PyMuPDF / pypdf ; python-docx | Tesseract (français) et pypdfium2, dans un processus enfant confiné ; pas de `.docx` | [ADR 0008](adr/0008-documents-ocr-local.md), [0011](adr/0011-antivirus-et-fichiers-hostiles.md) |
| Automatisations | Celery Beat / APScheduler | APScheduler (processus `scheduler` unique) | [ADR 0004](adr/0004-taches-asynchrones-dramatiq.md) |
| Auth | Authentik ou Keycloak (OIDC) ; passkeys | **passkeys WebAuthn intégrées**, sans fournisseur d'identité | [ADR 0002](adr/0002-authentification-integree-passkeys.md) |
| Reverse proxy | Traefik ou Nginx Proxy Manager | Traefik (fichiers de configuration, sans socket Docker), ou derrière un Nginx Proxy Manager existant | [ADR 0005](adr/0005-deploiement-compose-traefik.md) |
| Conteneurs | Docker Compose sur VM Debian (Proxmox) | idem | [ADR 0005](adr/0005-deploiement-compose-traefik.md) |
| CI/CD | GitHub Actions ou GitLab CI ; registre Docker | GitHub Actions ; **pas de registre** : les images sont construites sur la VM (`update.sh`) | — |
| Monitoring | Prometheus + Grafana ; Loki | Prometheus, Alertmanager, node exporter, blackbox exporter ; **ni Grafana ni Loki** | [exploitation.md §5](exploitation.md#5-supervision-et-alertes) |
| Secrets | Docker secrets ; Vault plus tard | Docker secrets (`infra/secrets/`) ; pas de Vault | — |
| Tests | pytest, Playwright, Vitest, RN Testing Library | pytest, Playwright, Vitest ; pas de tests mobiles (pas de client mobile) | — |
| Antivirus | (non cité) | aucun pour les fichiers du propriétaire | [ADR 0011](adr/0011-antivirus-et-fichiers-hostiles.md) |

## 2. API proposée et réalisée

[Architecture §11](cadrage/architecture-specifications.md#11-api-et-contrats-fonctionnels-proposés).
Familles réellement servies : [conventions-api.md](conventions-api.md#familles-de-routes).

| Famille proposée | Réalisé |
|---|---|
| `/v1/chat` | messages, pièces jointes, historique ; **pas de streaming** (réponse complète) |
| `/v1/memory` | réalisé (recherche dans `/v1/search`) |
| `/v1/tasks` | réalisé, avec dépendances et étiquettes ; pas de tâches récurrentes ([ADR 0013](adr/0013-taches-recurrentes.md)) |
| `/v1/goals` | objectifs, jalons, points d'étape ; les bilans sont dans `/v1/reviews` |
| `/v1/approvals` | réalisé ; le journal est dans `/v1/guardian` |
| `/v1/connectors` | réalisé (Google) ; agenda et e-mails dans `/v1/calendar` et `/v1/mail` |
| `/v1/notifications` | historique et préférences par catégorie ; plages de tranquillité dans `/v1/settings` |
| `/v1/privacy` | **export** seulement (JSON, archive ZIP) ; l'effacement se fait objet par objet dans chaque famille ; l'audit est dans `/v1/guardian` |

## 3. Étapes du plan de réalisation

[Architecture §13](cadrage/architecture-specifications.md#13-plan-de-réalisation-réaliste).

| Étape | État |
|---|---|
| 0 à 4 | réalisées (version 1.0.0) |
| 5 — Connecteurs | en cours : comptes Google, lecture de l'agenda, modifications d'agenda sous approbation, lecture de Gmail, envoi d'e-mails sous approbation, notifications adaptatives dans l'application. **Prévu** : notifications push sur le téléphone |
| 6 — Stabilisation | en cours : recette de sécurité, CSP à nonces, exercices de restauration scriptés, alertes, mises à jour avec retour arrière, documentation d'exploitation et de dépannage. **Reste** : exercice de restauration réel sur une VM vierge (journal vide), recette sur la machine cible, accessibilité |

## 4. Couverture du cahier des charges

### Fonctionnalités (§4)

| Exigence | Statut | Ce qui est réalisé, ce qui manque |
|---|---|---|
| F01 — Conversation multimodale | Partiel | texte, pièces jointes (texte extrait), dictée, lecture vocale, correction des documents, historique, recherche, export, purge à 90 jours. Manque : conversation liée à l'écran courant (seul « Discuter de cet e-mail » existe), streaming |
| F02 — Accueil et tableau de bord | Partiel | briefing calculé localement : priorités, agenda du jour et conflits, semaine, journal, approbations, mémoire, budget, usage IA, heure de calcul. Manque : cartes configurables, suggestions ; **prévu** (tableau de bord) |
| F03 — Tâches et calendrier | Réalisé | création, étiquettes, échéances, rappels, dépendances et état bloqué ; conflits et créneaux calculés localement ; lecture de l'agenda Google et modifications sous approbation. Hors F03 strict : pas de tâches récurrentes |
| F04 — Finances | Réalisé | saisie, tickets, import CSV en aperçu, catégorisation apprise et corrigeable, budgets, échéances récurrentes, objectifs d'épargne, projections marquées connu / prévu / estimé. Aucune connexion bancaire (import de fichiers seulement), aucun paiement |
| F05 — Journal et coaching | Réalisé | bilans quotidiens, hebdomadaires, mensuels ; faits séparés de la lecture de l'assistant |
| F06 — Objectifs et projets | Partiel | objectifs mesurables ou qualitatifs, jalons, points d'étape, archivage, tâches rattachées. Manque : suivi de projets avec décisions et raisons, suggestions adaptatives |
| F07 — Mémoire Premium | Réalisé | purge à 90 jours, extraction validée, recherche hybride et temporelle, souvenirs protégés, contradictions, préférences anciennes, origine des faits |
| F08 — Automatisation et agents | Partiel | agents Planification, Coaching, Mémoire et Finances à la demande, accès explicites ; audit et journal des appels LLM. Manque : agents Projets et Documents |
| F09 — Notifications | Partiel | catégories critique / importante / suggestion, créneaux, maximum par jour, regroupement, tranquillité, anti-doublons, permission pour les alertes critiques ([ADR 0015](adr/0015-notifications-adaptatives.md)). Manque : notifications push sur le téléphone (**prévu**) |
| F10 — Gestion documentaire | Partiel | détection locale des documents sensibles, originaux chiffrés à durée définie, pièces jointes d'e-mails importées avec leur provenance. Manque : rattachement d'un document à un événement |

### Politique d'autonomie (§5) et vie privée (§6)

| Exigence | Statut | Remarque |
|---|---|---|
| §5 — Quatre niveaux, moteur d'autorisation hors IA | Réalisé | Guardian ; approbation aux paramètres exacts ; modification d'un événement partagé avec passkey. Envoi d'un e-mail : approbation du message exact, passkey pour un nouveau destinataire ou une pièce sensible. Partage de document : pas encore d'action réelle |
| §5 — Instructions des contenus importés jamais autorisation | Réalisé | e-mails, documents et événements présentés comme des données ([recette-securite.md](recette-securite.md)) |
| §6 — Minimisation, masquage, séparation des espaces, consentement, audit, conservation, export complet | Réalisé | [politique-de-donnees.md](politique-de-donnees.md) |
| §6 — Sauvegardes chiffrées, test de restauration | Réalisé | sauvegardes restic quotidiennes, copie hors site, tests automatiques ; exercice réel à faire |
| §6 — Procédure de perte ou de vol d'appareil | Réalisé | [guide d'utilisation](guide-utilisateur.md#appareil-perdu-ou-volé) |

### Parcours (§7)

| Parcours | Statut | Remarque |
|---|---|---|
| UC01 — Journal oral | Réalisé | dictée transcrite localement, tâches et faits proposés puis validés, échéances proposées |
| UC02 — Ticket de caisse | Réalisé | marchand, date et total avec leur ligne d'origine, vérifiés avant l'ajout |
| UC03 — Achat important | Partiel | l'agent Finances voit budget et projection ; pas d'orchestration Finances + Coaching en une recommandation |
| UC04 — Action externe (e-mail) | Réalisé | brouillon, approbation du message exact (passkey pour un nouveau destinataire ou une pièce sensible), jamais envoyé deux fois, issue incertaine vérifiée dans Gmail ([ADR 0018](adr/0018-envoi-des-e-mails-sous-approbation.md)) |
| UC05 — Droit à l'oubli | Réalisé | effacement propagé aux index, copies, sauvegardes (sous 23 jours) et rejoué à la restauration |

### Exigences non fonctionnelles (§8)

| Critère | Statut | Remarque |
|---|---|---|
| Disponibilité | Réalisé | mode dégradé sans IA |
| Performance | Partiel | traitements locaux sans cloud ; mesures à faire sur la machine cible ([recette-serveur.md](recette-serveur.md)) |
| Fiabilité | Réalisé | idempotence, reprise des tâches, aucune double écriture chez le fournisseur ([ADR 0016](adr/0016-ecritures-externes-sous-approbation.md)) |
| Observabilité | Réalisé | journaux JSON sans contenu, métriques, alertes |
| Accessibilité | Partiel | pas encore auditée (clavier, lecteurs d'écran, contrastes) ; audit et corrections **prévus** |
| Interopérabilité | Réalisé | API `/v1`, export JSON, CSV, ICS, Markdown |
| Réversibilité | Réalisé | fournisseur IA derrière une abstraction, export complet |

### Critères d'acceptation (§9)

| Critère | Statut |
|---|---|
| Consulter, corriger, exporter, supprimer ses souvenirs | Réalisé |
| Purge des conversations à 90 jours, index compris | Réalisé |
| Envoi de mail sans confirmation bloqué et journalisé | Réalisé (testé) : aucun envoi sans approbation du message exact |
| Pièce jointe professionnelle inaccessible à l'agent Finance sans permission | Réalisé |
| Notification non critique dans les plages de tranquillité | Réalisé |
| Indisponibilité cloud sans effet sur les tâches et le budget | Réalisé |
| Recommandation financière avec hypothèses et données | Réalisé (projections) |
| Bilans : faits distincts des interprétations | Réalisé |

## 5. Arbitrages ouverts du cadrage

[Architecture §14](cadrage/architecture-specifications.md#14-arbitrages-ouverts-avant-développement).

| Arbitrage | Décision |
|---|---|
| Fournisseur LLM | Gemini, niveau gratuit ([ADR 0003](adr/0003-gemini-derriere-une-abstraction.md), [0007](adr/0007-couts-gemini-niveau-gratuit.md)) |
| Authentik ou Keycloak | ni l'un ni l'autre : passkeys intégrées ([ADR 0002](adr/0002-authentification-integree-passkeys.md)) |
| Matériel (transcription, embeddings) | embeddings sur CPU, transcription sur CPU ou GTX 1050 Ti ([ADR 0006](adr/0006-embeddings-locaux.md), [0009](adr/0009-voix-locale.md)) |
| Horaires des bilans, catégories sensibles, rétentions, sauvegardes | [politique-de-donnees.md](politique-de-donnees.md) ; restent **à valider** : journal d'audit (1 an), appels LLM (sans limite à ce jour) |
| Périmètre Android | **ouvert** |
| Budget d'inférence et seuils d'arrêt | plafonds quotidiens et arrêt d'urgence ([ADR 0007](adr/0007-couts-gemini-niveau-gratuit.md), [0010](adr/0010-reservation-des-quotas-llm.md)) |
