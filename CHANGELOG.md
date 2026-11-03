# Journal des versions

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ; numérotation
[SemVer](https://semver.org/lang/fr/).

## [Non publié]

Travaux restants après la 1.0.0 (plan « LifeOS — travaux restants », tâches 01 à 35).

### Sécurité

- **CSP à nonce par requête** dans le client web, sans `'unsafe-inline'` pour les scripts ;
  un script injecté sans nonce est bloqué (testé de bout en bout).
- **Recette de sécurité** ([recette-securite.md](docs/recette-securite.md)) : accès entre
  espaces, permissions révoquées ou expirées, cookie volé, CSRF, injections indirectes et
  rejeu d'approbations sont refusés **et audités** (audit limité par Redis, métrique
  `lifeos_security_events_total`). Bouton « Déconnecter les autres sessions ».
- **Fichiers hostiles** ([ADR 0011](docs/adr/0011-antivirus-et-fichiers-hostiles.md)) :
  chaque fichier non fiable est lu dans un processus enfant confiné (mémoire bornée, délai,
  tué avec Tesseract) ; démultiplexeur et codecs audio imposés ; une image n'est servie
  qu'une fois nettoyée de ses métadonnées. Pas d'antivirus pour les fichiers du
  propriétaire (critères documentés pour les pièces jointes de tiers).
- **Données sensibles** : filtrage local validé sur un corpus, refus manuel (« Toujours
  garder en local »), contrôle avant envoi à l'assistant (`POST /v1/chat/preview`),
  `lifeos-admin sensitivity-check`.
- Dépendances mises à jour pour des failles publiées : Next.js 16.4.3, Pillow 13.0,
  filelock, certifi, pyasn1 0.6.5.
- **Écritures chez un fournisseur** ([ADR 0016](docs/adr/0016-ecritures-externes-sous-approbation.md)) :
  approbation liée au SHA-256 de ses paramètres, invalidée si la cible change ; réservation
  unique avant l'appel, issue incertaine rapprochée, jamais renvoyée à l'aveugle.
- **Export de toutes les données** réservé à une connexion par passkey récente.

### Données et vie privée

- **Effacement complet** : supprimer un objet efface aussi ses vecteurs, son original, les
  réponses gardées par idempotence, les propositions, rappels et bilans qui le citent ; un
  fait part avec tout son historique de corrections ; ce qui a été tiré d'une entrée de
  journal ou d'une conversation est montré et gardé, sauf choix contraire.
- Approbations décidées et notifications lues purgées après 90 jours.
- Délai de disparition des sauvegardes explicite : **sous 23 jours** (35 au plus si des
  nuits manquent), copie hors site comprise ; `restore.sh` rejoue de lui-même les
  effacements faits depuis la sauvegarde restaurée.
- **Quotas IA fiables en concurrence** : chaque appel réserve sa part des plafonds avant de
  partir ([ADR 0010](docs/adr/0010-reservation-des-quotas-llm.md)).

### Exploitation

- **Derrière un reverse proxy existant** (Nginx Proxy Manager) : `install.sh
  --behind-proxy`, aucun port publié, adresse réelle du client conservée.
- **Sauvegardes automatiques** (timers systemd) avec copie hors machine, paires base +
  originaux cohérentes, alertes d'échec (ntfy), exercice de restauration scripté
  ([restauration.md](docs/restauration.md)), `lifeos-admin verify-data`.
- **Alertes opérationnelles** (profil `monitoring`) : indisponibilité, disque, workers,
  file en retard, sauvegardes, certificats, signal de vie ; notifications sans donnée
  personnelle.
- **Mises à jour** : `infra/scripts/update.sh` (sauvegarde préalable, contrôle de santé,
  retour arrière, y compris avec migration) ; chaque migration redescend puis remonte.
- **Recette sur le serveur cible** : `infra/scripts/recette.sh` mesure temps et pointes de
  mémoire ([recette-serveur.md](docs/recette-serveur.md)).

- **Tests de bout en bout sur une pile de production** (`scripts/test-e2e-stack.sh`,
  workflow `e2e-stack`) : Traefik, TLS, cookies sécurisés, planificateur, puis sauvegarde,
  restauration sur volumes vides et reconnexion avec la même passkey.
  `lifeos-admin run-job` pour lancer une tâche de fond à la demande.
- Documentation complétée et vérifiée contre le code : [dépannage](docs/depannage.md),
  [réalisation du cahier des charges](docs/realisation.md), appareil perdu ou volé.

### Comptes connectés : agenda et e-mails (étape 5)

- **Socle OAuth** ([ADR 0012](docs/adr/0012-connecteurs-oauth.md)), Google en premier
  fournisseur : jetons chiffrés, consentement par espace et par usage, renouvellement,
  reconnexion, déconnexion avec révocation chez le fournisseur, aucun jeton dans les
  journaux ([guide-google.md](docs/guide-google.md)).
- **Agenda** : agendas choisis lus toutes les 15 minutes, juste aux changements d'heure ;
  page Agenda ; conflits et créneaux libres calculés localement. **Créer, déplacer,
  annuler** un événement seulement après approbation de l'aperçu exact (passkey pour un
  événement partagé), jamais en double.
- **E-mails** ([ADR 0017](docs/adr/0017-lecture-des-e-mails.md)) : libellés choisis,
  copiés en texte brut dans leur espace, jamais traités comme une instruction, supprimés
  après 90 jours ; pièces jointes importées seulement sur demande. **Envoi**
  ([ADR 0018](docs/adr/0018-envoi-des-e-mails-sous-approbation.md)) seulement après
  approbation du message exact (passkey pour un nouveau destinataire ou une pièce
  sensible), jamais deux fois.

### Organisation et notifications

- **Accueil** : agenda du jour, vue Semaine, suggestions avec leur source, cartes
  personnalisables, calculé localement.
- **Tâches** : dépendances, état bloqué expliqué, étiquettes, aucun cycle accepté.
- **Notifications adaptatives** ([ADR 0015](docs/adr/0015-notifications-adaptatives.md)) :
  créneaux, maximum par jour, suggestions regroupées, alertes critiques pendant la
  tranquillité seulement sur autorisation ; **sur le téléphone** par Web Push
  ([ADR 0019](docs/adr/0019-notifications-mobiles-web-push.md)), texte générique par défaut.
- **Application installable** sur le téléphone ([ADR 0022](docs/adr/0022-client-mobile-web-dabord.md)) :
  partage d'une photo ou d'un PDF vers LifeOS, page hors ligne sans donnée, pas
  d'application native.

### Assistant et agents

- **Discuter d'une tâche ou d'un objectif** ([ADR 0020](docs/adr/0020-conversation-liee-a-un-element.md)) :
  l'élément est relu par le serveur dans son espace et transmis comme une donnée.
- **Agents Projets et Documents** ; chaque agent ne lit plus que ses propres ressources,
  même avec une permission accordée pour un autre usage.
- **Évaluation d'un achat important** ([ADR 0021](docs/adr/0021-coordination-des-agents.md)) :
  Finances et Coaching calculés localement, une seule synthèse IA plafonnée, chaque raison
  rattachée à son agent et à ses chiffres.

### Finances, documents et voix

- **Import de relevés CSV** ([ADR 0014](docs/adr/0014-finances-import-et-projections.md)) en
  aperçu, sans doublon au réimport, annulable ; **échéances récurrentes**, objectifs
  d'épargne et **projections** où chaque montant est connu, prévu ou estimé.
- **Photos et tickets mieux lus** : orientation et redressement en seconde passe, dans le
  processus confiné (corpus synthétique : champs justes 57 % → 93 %) ; `lifeos-ml bench`.
- **Dictée** : progression, temps restant et annulation ; mesures du moteur ;
  `lifeos-stt bench`.

### Accessibilité

- Contrôles WCAG 2.2 AA à chaque CI (contrastes clair et sombre, noms, cibles, clavier) et
  parcours au téléphone à 360 px ([accessibilite.md](docs/accessibilite.md)) ; lien
  « Aller au contenu », titres de page, Échap, Entrée sur écran tactile.

### Changements à connaître

- Export : format JSON **version 2** (une clé par table, décimaux en chaînes exactes).
- Toute écriture dans l'agenda et tout envoi d'e-mail passent par une approbation.
- Sur un écran tactile, Entrée va à la ligne dans l'assistant ; le bouton envoie.

## [1.0.0] — 2026-10-12

Première version livrable. Elle couvre les étapes 0 à 4 de la feuille de route
([architecture §13](docs/cadrage/architecture-specifications.md)). Les connecteurs (étape 5)
et la stabilisation complète (étape 6) sont reportés : voir « Limites connues ».

### Socle et sécurité

- Connexion par **passkey** uniquement (WebAuthn), sans mot de passe.
  - Sessions révocables, liste des appareils.
  - Réauthentification pour les opérations sensibles.
  - Lien d'enrôlement à usage unique et récupération en ligne de commande (`lifeos-admin`).
- **Guardian** :
  - autorisations décidées hors IA, politique d'autonomie (automatique, sous règle,
    validation, validation renforcée) ;
  - approbations aux paramètres exacts ;
  - journal d'audit en ajout seul, sans contenu.
- **Espaces** personnel et professionnel cloisonnés ; accès des agents par espace et par
  ressource, limité à 90 jours et révocable.
- Déploiement Docker Compose derrière Traefik (TLS, aucun service interne exposé).
  - Conteneurs sans root, en lecture seule, sans capacités.
  - Sauvegardes chiffrées restic, avec test de restauration.
  - Supervision Prometheus.

### Assistant et mémoire (étapes 2 et 3)

- **Assistant** Gemini, autorisé espace par espace.
  - Contexte minimal, masquage local (e-mail, téléphone, IBAN, carte, NIR).
  - Mode dégradé : sans IA, tout le reste fonctionne.
- **Coûts nuls par construction** : projet Gemini sans compte de facturation, plafonds
  quotidiens par palier, arrêt d'urgence, suivi de la consommation
  ([ADR 0007](docs/adr/0007-couts-gemini-niveau-gratuit.md)).
- **Mémoire** :
  - faits avec origine et confiance, souvenirs protégés, corrections historisées ;
  - file « À vérifier » : doublons, contradictions possibles, préférences anciennes ;
  - consolidation d'une conversation en propositions à approuver.
- **Recherche hybride** par mots et par sens, avec un modèle d'embeddings **local**
  (EmbeddingGemma, sans cloud) et des expressions de temps en français
  ([ADR 0006](docs/adr/0006-embeddings-locaux.md)).
- Tâches et rappels (plages de tranquillité), journal avec extraction de tâches et de faits à
  valider, objectifs mesurables avec jalons, bilans quotidiens, hebdomadaires et mensuels.
- **Agents spécialisés à la demande** (planification, coaching, mémoire, finances), avec
  accès explicite.

### Multimodal (étape 4)

- **Documents** (F10) : PDF, images (JPEG, PNG, WebP, HEIC) et textes.
  - Type reconnu par signature, originaux **chiffrés** (AES-256-GCM) et conservés 90 jours
    par défaut ou sans limite au choix.
  - Lecture **locale** : couche texte des PDF, OCR Tesseract en français.
  - Photos ré-encodées sans métadonnées.
  - Texte corrigeable, documents retrouvables dans la recherche
    ([ADR 0008](docs/adr/0008-documents-ocr-local.md)).
- **Filtrage local des documents sensibles** (identité, santé, banque) : jamais transmis à
  l'IA, ni comme pièce jointe ni par un agent.
- **Tickets de caisse** (UC02) : marchand, date et total lus localement, chacun avec sa ligne
  d'origine, puis vérifiés avant l'ajout au budget.
- **Finances** (F04) : catégories et budgets mensuels, opérations en montants exacts,
  classement appris des corrections, budget du mois calculé localement et affiché sur
  l'accueil, agent Finances. Aucune connexion bancaire, aucun paiement.
- **Voix** (UC01, F01) ([ADR 0009](docs/adr/0009-voix-locale.md)) :
  - dictée dans le journal et dans le chat, transcrite **sur le serveur** (Whisper
    large-v3-turbo, sur CPU ou sur GPU NVIDIA Pascal) ;
  - enregistrement supprimé dès la transcription ;
  - lecture à voix haute avec les seules voix locales de l'appareil.
- **Pièces jointes du chat** : seul le texte extrait d'un document non sensible accompagne
  le message, présenté comme des données et audité.
- Sauvegarde et restauration des originaux chiffrés ; script d'installation
  `infra/scripts/install.sh`.

### Qualité

- Tests :
  - backend : 266 tests sur une vraie base PostgreSQL, plus 26 pour la lecture (OCR) et la
    transcription ;
  - web : 234 tests unitaires et de composants ;
  - 3 parcours de bout en bout Playwright : MVP, intelligence, multimodal.
- Chaîne complète d'OCR et de transcription testée hors ligne en CI ; images Docker
  construites et démarrées sans réseau.
- Deux revues adversariales (étapes 3 et 4), corrections incluses. Pour l'étape 4 :
  - détection locale des documents sensibles élargie ;
  - extraction isolée dans un processus à mémoire bornée (PDF piégés) ;
  - décodage audio progressif ;
  - échec définitif après trois plantages ;
  - restauration qui vérifie les archives avant de remplacer quoi que ce soit ;
  - dictée arrêtée avant la limite ;
  - pièces jointes jamais perdues ni envoyées à moitié.

### Limites connues

- **Pas de connecteurs** calendrier ni e-mail, et pas de notifications push mobiles
  (étape 5) : les rappels s'affichent dans l'application.
- **Pas d'application mobile native** : le client web fonctionne sur mobile (navigateur).
- CSP : `'unsafe-inline'` est encore permis pour les scripts (hydratation Next.js). Le
  passage aux nonces est prévu à l'étape 6, avec les tests de sécurité complets.
- OCR : pas de redressement de perspective ; une photo de travers se lit moins bien
  (confiance affichée, texte corrigeable).
- Transcription sur CPU : 2 à 4 minutes pour une dictée de 5 minutes.
- Mono-utilisateur (le propriétaire).
