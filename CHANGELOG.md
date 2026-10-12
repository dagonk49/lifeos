# Journal des versions

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ; numérotation
[SemVer](https://semver.org/lang/fr/).

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
