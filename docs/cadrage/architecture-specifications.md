LifeOS • Version de conception 1.0 • 11 octobre 2026

Statut : document de cadrage — les technologies proposées restent à confirmer par prototypage.

> Document source, conservé tel quel. Ce qui a été retenu à la place de certaines
> propositions, et l'état de réalisation de chaque exigence : [realisation.md](../realisation.md).

# 1. Principes structurants

- Architecture local-first : données et décisions d’autorisation sur le serveur personnel.

- Une application monolithique modulaire au départ ; ne pas multiplier les microservices prématurément.

- Modèles cloud interchangeables derrière une couche d’abstraction ; aucun secret côté application mobile.

- Agents spécialisés déclenchés par Core et limités à des outils et permissions explicites.

- Tâches déterministes hors LLM lorsque possible ; queues pour traitement différé et reprise.

# 2. Stack technologique recommandée

| Couche               | Technologie recommandée                                     | Justification / alternative                                                                           |
|----------------------|-------------------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| Client web           | TypeScript, React, Next.js                                  | Écosystème mature ; alternative : Vue/Nuxt.                                                           |
| Client mobile        | Client web installé sur l’écran d’accueil (ADR 0022) ; React Native + Expo seulement si un besoin natif est confirmé | Pas d’application native à ce jour : notifications Web Push, partage vers LifeOS (Android), dictée, photo et passkeys dans le navigateur ; déclencheur d’un client natif dans l’ADR 0022. |
| Design system        | Tailwind CSS + shadcn/ui (web) ; composants RN dédiés       | Interface cohérente et accessible ; ne pas forcer les composants web sur mobile.                      |
| API et orchestration | Python 3, FastAPI, Pydantic                                 | Intégrations IA, validation des schémas ; alternative : NestJS/TypeScript.                            |
| Base principale      | PostgreSQL                                                  | Transactions, relations, intégrité, JSONB et recherche textuelle.                                     |
| Recherche sémantique | pgvector dans PostgreSQL                                    | Vectorisation locale ou fournisseur autorisé ; recherche hybride texte + vecteur + filtres temporels. |
| Cache / files        | Redis + Celery (ou Dramatiq)                                | Tâches asynchrones, délais, retries ; Redis non source de vérité.                                     |
| Fichiers             | Stockage objet compatible S3 (MinIO) ou volumes chiffrés    | Originaux et pièces jointes, contrôles de durée ; commencer simple si mono-utilisateur.               |
| LLM cloud            | API fournisseur compatible outils et sorties structurées    | Choisir selon coût, qualité, rétention contractuelle et résidence des données.                        |
| Embeddings           | Modèle d’embeddings local via sentence-transformers         | Indexation sans envoyer toute la mémoire au cloud.                                                    |
| Transcription        | faster-whisper local, selon matériel                        | ASR local ; alternative cloud sur consentement et après analyse de risque.                            |
| Synthèse vocale      | Piper local ; option cloud                                  | Voix hors ligne possible ; qualité à comparer.                                                        |
| OCR / documents      | OCRmyPDF + Tesseract ; PyMuPDF / pypdf ; python-docx        | Extraction ; inspection et nettoyage des contenus non fiables.                                        |
| Automatisations      | Celery Beat / APScheduler + règles métier                   | Planifications ; gestion des fuseaux horaires et exécutions manquées.                                 |
| Auth                 | Authentik ou Keycloak (OIDC) ; passkeys/WebAuthn            | Gestion des sessions et MFA ; solution adaptée à l’échelle.                                           |
| Reverse proxy        | Traefik ou Nginx Proxy Manager                              | TLS, routage ; garder une seule solution d’entrée.                                                    |
| Conteneurs           | Docker Compose sur VM Debian dans Proxmox                   | Déploiement progressif et sauvegardes simples.                                                        |
| CI/CD                | GitHub Actions ou GitLab CI ; Docker registry               | Tests, analyse de dépendances, publication contrôlée.                                                 |
| Monitoring           | Prometheus + Grafana ; Loki (option)                        | Métriques et journaux ; minimiser les données sensibles.                                              |
| Secrets              | Variables protégées + Docker secrets ; Vault ultérieurement | Rotation et séparation ; éviter secrets dans Git.                                                     |
| Tests                | pytest, Playwright, Vitest, React Native Testing Library    | Unitaires, intégration, E2E et tests d’autorisation.                                                  |

# 3. Déploiement cible sur Proxmox

Proposition : VM Debian dédiée à LifeOS, distincte des services multimédias existants. PostgreSQL, API, worker, Redis et stockage chiffré dans un réseau Docker privé ; seul le reverse proxy est exposé. Ne pas exposer directement PostgreSQL, Redis, MinIO ou les consoles d’administration sur Internet.

Le serveur actuel peut convenir au prototypage, mais ses ressources CPU/RAM et la charge existante doivent être mesurées. Les traitements audio, OCR et embeddings peuvent exiger davantage de mémoire ou un GPU compatible. Dimensionnement indicatif à confirmer par mesures : VM de développement 4 vCPU, 8–12 Go RAM, stockage SSD 100 Go hors documents ; prévoir marge pour index et sauvegardes.

# 4. Découpage logique des composants

| Composant                 | Responsabilités                                                                  |
|---------------------------|----------------------------------------------------------------------------------|
| Gateway/API               | Auth, validation d’entrées, limites de débit, sessions, endpoints versionnés.    |
| Core Orchestrator         | Intention, plan, choix des outils/agents, synthèse finale.                       |
| Guardian / Policy Engine  | Décision d’accès hors LLM, règles de consentement, approbations, audit.          |
| Memory Service            | Événements, faits, souvenirs, résumés, index, provenance et purge.               |
| Connector Service         | Adaptateurs Gmail, calendrier, fichiers, finances ; synchronisation et webhooks. |
| Task/Notification Service | Rappels, calendrier, notifications, fuseaux, déduplication.                      |
| Multimodal Pipeline       | Quarantaine, validation MIME, antivirus, OCR, ASR, redaction.                    |
| Background Workers        | Imports, bilans, embeddings, consolidation et nettoyage.                         |
| Observability             | Métriques, erreurs, audit immuable ou append-only, diagnostics.                  |

# 5. Flux de requête sécurisé

1\. Client authentifié → API : requête et contexte d’interface explicitement sélectionné.

2\. Guardian : vérifie l’identité, le périmètre, les permissions et les règles de confidentialité.

3\. Core : choisit traitement déterministe ou agent spécialisé.

4\. Memory : recherche filtrée par espace, autorisation, temporalité et provenance.

5\. Privacy Engine : construit un paquet de contexte minimal et masque les champs sensibles.

6\. LLM cloud (si nécessaire) : reçoit uniquement le paquet autorisé ; sortie structurée validée.

7\. Action Service : simule/affiche les effets, sollicite approbation si requise, exécute et journalise.

8\. Memory : conserve les faits utiles, les corrections et les liens de provenance.

# 6. Modèle de données initial

| Entité                   | Champs essentiels                                                    |
|--------------------------|----------------------------------------------------------------------|
| users / devices          | identifiant, préférences, appareils, sessions                        |
| spaces                   | personnel/professionnel, règles et propriétaires                     |
| sources / permissions    | connecteur, finalité, périmètre, expiration, révocation              |
| conversations / messages | horodatage, espace, rôle, référence de pièces jointes, date de purge |
| events / facts           | type, date, valeur, confiance, source, version, statut corrigé       |
| memories                 | résumé, importance, protection, durée, liens aux faits, révision     |
| embeddings               | référence objet, modèle/version, vecteur, périmètre                  |
| tasks / goals            | état, échéance, responsable, jalons, historique                      |
| financial_transactions   | montant exact, devise, date, catégorie, source, statut               |
| approvals / actions      | action, paramètres exacts, décision, horodatage, résultat            |
| documents                | hachage, MIME, emplacement, classification, durée, provenance        |
| notifications            | catégorie, priorité, canal, déduplication, statut                    |

Conserver séparément : (a) faits vérifiables, (b) inférences du modèle, (c) préférences déclarées. Chaque modification importante doit préserver l’historique de correction. Les informations financières utilisent des types décimaux exacts, jamais des flottants binaires.

# 7. Mémoire Premium — règles de conception

- Brut : messages et transcriptions pendant 90 jours ; tâche de purge planifiée avec preuve d’exécution.

- Consolidation : résumés quotidiens, événements structurés, décisions, objectifs et préférences durables.

- Souvenirs protégés : jamais purgés automatiquement ; restent modifiables/supprimables sur ordre explicite.

- Récupération : combinaison de recherche SQL/texte, similarité vectorielle, filtres par date/espace et classement de pertinence.

- Qualité : provenance, score de confiance, fraîcheur, gestion des contradictions, tests de rappel et corrections utilisateur.

- Effacement : supprimer contenu, embeddings, caches et références ; documenter le cycle de purge des sauvegardes.

- Conservation : les fichiers originaux ont une politique distincte des conversations brutes ; aucune durée infinie par défaut.

# 8. IA, agents et outils

- Core définit une tâche et un budget de ressources ; un agent spécialisé reçoit un périmètre de données et une liste d’outils restreints.

- Sorties structurées validées par schémas Pydantic ; vérification déterministe avant action.

- Interdire aux agents de s’octroyer des permissions ; Guardian est la seule autorité.

- Limiter profondeur d’enchaînement, temps d’exécution, coûts, répétitions et nombre d’appels ; prévoir arrêt d’urgence.

- Défenses contre prompt injection : documents et pages web considérés comme non fiables ; ne jamais exécuter leurs instructions.

- Ne pas supposer que plusieurs agents donnent toujours de meilleurs résultats : benchmark contre un agent unique.

# 9. Sécurité, réseau et sauvegardes

- TLS partout ; chiffrement disque/volumes et clés séparées des sauvegardes ; politique de rotation.

- Authentification OIDC + MFA/passkeys ; RBAC/ABAC par espace, source et action.

- Validation des fichiers : taille, type, antivirus, sandbox si nécessaire, refus des formats dangereux.

- Accès distant via VPN ou reverse proxy durci ; pare-feu, fail2ban/rate limiting, mises à jour.

- Sauvegarde 3-2-1 adaptée au budget : base cohérente, documents, secrets chiffrés ; copie hors machine ; tests de restauration réguliers.

- Audit des accès aux données et des approbations ; logs applicatifs sans prompts complets ni pièces jointes.

- Menaces à tester : injection indirecte, fuite inter-espaces, SSRF, accès objets non autorisés, vol de jetons, escalade d’agent.

# 10. Connecteurs et contraintes externes

Intégrations prioritaires : calendrier et messagerie via OAuth et API officielles, imports de fichiers, notifications mobiles. Finance : commencer par CSV/OFX et envisager un prestataire d’open banking conforme au cadre applicable, sans conserver les identifiants bancaires. Android limite l’accès continu aux notifications, données d’autres applications et tâches d’arrière-plan ; demander des permissions explicites et prévoir des fonctions dégradées.

# 11. API et contrats fonctionnels proposés

| Famille d’API     | Exemples de capacités                                  |
|-------------------|--------------------------------------------------------|
| /v1/chat          | messages, pièces jointes, streaming, historique        |
| /v1/memory        | recherche, correction, protection, export, suppression |
| /v1/tasks         | CRUD, planification, rappels, statuts                  |
| /v1/goals         | objectifs, jalons, bilans                              |
| /v1/approvals     | prévisualisation, accepter/refuser, journal            |
| /v1/connectors    | connexion OAuth, scopes, révocation, synchronisation   |
| /v1/notifications | préférences, silence, historique                       |
| /v1/privacy       | règles, audit, demandes d’export/effacement            |

Spécifier OpenAPI, erreurs typées, pagination, identifiants idempotents pour écritures, quotas et versionnage avant implémentation.

# 12. Tests et indicateurs de qualité

- Tests unitaires : permissions, conservation, dates/fuseaux, budgets décimaux et calculs.

- Tests d’intégration : refus d’envoi non approuvé, révocation d’accès, purge des index, séparation des espaces.

- Tests de mémoire : questions de rappel datées, précision des sources, contradiction et oubli ; corpus synthétique sans données privées.

- Tests IA : prompt injection, hallucinations, qualité des recommandations, sorties invalides et erreurs des outils.

- Tests de charge : temps de réponse local, latence cloud, RAM, coût moyen par bilan, files d’attente.

- Tests de reprise : arrêt du serveur, perte réseau, réexécution idempotente et restauration de sauvegarde.

# 13. Plan de réalisation réaliste

| Étape             | Livrable / condition de passage                                                        |
|-------------------|----------------------------------------------------------------------------------------|
| 0 — Cadrage       | Maquettes, modèle de données, politique de données, menaces, budget mensuel cloud.     |
| 1 — Socle         | Repo, Docker Compose, PostgreSQL, API, auth, CI, sauvegarde et observabilité.          |
| 2 — MVP           | Client web, chat texte, tâches, journal, mémoire Premium, recherche, Guardian basique. |
| 3 — Intelligence  | RAG hybride, bilans, objectifs, agents spécialisés, évaluations de qualité.            |
| 4 — Multimodal    | Documents, OCR, images, voix et filtrage local validé.                                 |
| 5 — Connecteurs   | Calendrier/e-mail, approbations et notifications mobiles.                              |
| 6 — Stabilisation | Tests sécurité, restauration, coûts, confidentialité, documentation utilisateur.       |

# 14. Arbitrages ouverts avant développement

- Choisir fournisseur(s) LLM après comparaison de qualité, prix, latence, conditions de traitement et rétention API.

- Choisir Authentik ou Keycloak selon charge d’exploitation réelle.

- Mesurer si le matériel actuel supporte transcription et embeddings locaux ; décider d’un GPU ou d’un serveur séparé.

- Définir les horaires de bilans, catégories sensibles, durées de rétention documentaire et politique de sauvegarde.

- ~~Préciser le périmètre Android (notification, micro, partage de fichiers, widget) et les autorisations réellement disponibles.~~ Tranché par l’ADR 0022 : application web installée d’abord ; restent à confirmer sur le téléphone du propriétaire les besoins que le web ne couvre pas (alertes qui traversent « Ne pas déranger », widget, partage depuis iOS).

- Établir un budget d’inférence mensuel et des seuils d’arrêt automatiques.

# 15. Documentation officielle de référence

FastAPI — https://fastapi.tiangolo.com/

PostgreSQL — https://www.postgresql.org/docs/

pgvector — https://github.com/pgvector/pgvector

React Native — https://reactnative.dev/docs/getting-started

Expo — https://docs.expo.dev/

Next.js — https://nextjs.org/docs

Docker Compose — https://docs.docker.com/compose/

Celery — https://docs.celeryq.dev/

Authentik — https://docs.goauthentik.io/

OWASP LLM Top 10 — https://genai.owasp.org/llm-top-10/

CNIL — https://www.cnil.fr/

Les liens sont des points d’entrée documentaires ; vérifier versions, licences, compatibilités et conditions commerciales au moment de l’implémentation.
