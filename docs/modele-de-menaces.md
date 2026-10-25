# Modèle de menaces

Version initiale (étape 0), à reprendre à chaque étape. Référence : OWASP Top 10 for LLM
Applications.

## 1. Actifs

| Actif | Sensibilité |
|---|---|
| Mémoire (faits, souvenirs, journal) | très élevée : vie privée, santé, relations |
| Conversations brutes | très élevée |
| Données financières | élevée |
| Documents (originaux chiffrés, texte extrait) | très élevée : identité, santé, banque |
| Voix (enregistrements de dictée) | très élevée : biométrique ; conservée le temps de la transcription |
| Clé de chiffrement des documents | élevée : sans elle, les originaux sont perdus ; avec elle et une sauvegarde, ils sont lisibles |
| Jetons OAuth des comptes connectés (agenda, e-mails) | très élevée : accès à l'agenda et à la boîte e-mail, hors de LifeOS |
| Clé des connecteurs (`lifeos_connectors_key`) | élevée : avec une sauvegarde, elle rend les jetons lisibles ; la perdre oblige seulement à reconnecter les comptes |
| Sessions et passkeys | élevée : accès à tout le reste |
| Clé d'API Gemini, secrets d'infrastructure | élevée : coût, accès |
| Sauvegardes | très élevée : copie complète |

## 2. Frontières de confiance

```
Navigateur / mobile ──HTTPS── Traefik ──┬── API ──┬── PostgreSQL (réseau interne)
                                        │         ├── Redis (réseau interne)
                                        │         ├── ml : embeddings, OCR (interne, sans sortie)
                                        │         ├── stt : transcription (interne, sans sortie)
                                        └── Web   ├── Passerelle cloud ──HTTPS── Gemini
                                                  └── Connecteurs ──HTTPS── Google (OAuth, Agenda, Gmail)
Contenus importés (documents, e-mails, événements, pages web) = NON FIABLES
Sorties du LLM = NON FIABLES (validées par schéma avant toute action)
Supervision (profil monitoring) : Prometheus lit les métriques sur le réseau interne ;
Alertmanager sort vers le canal d'alerte (ntfy) ; node exporter lit la racine de l'hôte
en lecture seule ; la sonde publique passe par le port 443 de la machine
```

## 3. Menaces et parades

| # | Menace | Parades | Étape |
|---|---|---|---|
| T1 | Phishing / vol d'identifiants | passkeys (résistantes au phishing), aucun mot de passe | 1 |
| T2 | Vol de cookie de session | `HttpOnly`, `Secure`, `SameSite=Strict`, empreinte seule en base, expiration absolue et d'inactivité, révocation, liste des sessions et « Déconnecter les autres sessions » (connexion récente) ; réutilisation d'une session révoquée, expirée ou inactive refusée et auditée (`auth.session.rejected`, une fois par session et par heure) ; même session vue depuis un autre navigateur ou système auditée sans blocage (`auth.session.ua_changed`) ; opérations renforcées refusées et auditées au-delà de 10 minutes après la connexion | 1 |
| T3 | CSRF | `SameSite=Strict` + vérification `Origin` sur les requêtes non sûres ; refus `origin_mismatch` journalisés, comptés et audités (`http.origin_mismatch`, un par minute) | 1 |
| T4 | Force brute / déni sur l'auth | limitation de débit Traefik par adresse du client (derrière un reverse proxy : la dernière adresse de `X-Forwarded-For`, celle que le proxy ajoute lui-même, jamais une valeur écrite par le client), jetons d'enrôlement 256 bits à durée courte | 1 |
| T5 | Exposition des services internes | réseau Docker `internal`, seul Traefik publie 80/443 (derrière un reverse proxy existant : aucun port, joignable par le seul réseau `lifeos-proxy`), `/metrics` non routé ; supervision : Prometheus sur `127.0.0.1` seulement, métriques du worker sur le réseau interne, Alertmanager et les exporteurs sans port publié | 1 |
| T6 | Secrets dans Git | `.gitignore`, secrets Docker, `.env.example` sans valeur ; configuration d'Alertmanager (jetons du canal d'alerte) générée dans `infra/secrets/`, jamais commitée, secrets lus dans des fichiers et jamais sur la ligne de commande | 1 |
| T7 | Fuite de données dans les logs, les métriques ou les alertes | logs sans corps ni prompts ; audit sans contenu ; métriques sans donnée de l'utilisateur (noms de libellés contre une liste fermée, testé) ; notifications d'alerte faites des seuls textes fixes des règles et de libellés techniques, sans les libellés eux-mêmes, sans lien ni nom de domaine (testé de bout en bout avec une alerte qui porte des données) ; rapport de recette fait de durées et de mémoire, sur des entrées synthétiques (testé) | 1 |
| T8 | Vol ou perte des sauvegardes | chiffrement restic ; clé des documents et mots de passe hors des sauvegardes (fiche de récupération, `export-keys.sh`) ; copie hors machine automatique avec la même rétention ; restauration testée chaque semaine (et chaque mois depuis la copie) ; alerte à chaque échec et signal de vie externe | 1 |
| T9 | Dépendances compromises | versions verrouillées (`uv.lock`, `pnpm-lock.yaml`), audit des dépendances en CI | 1 |
| T10 | Injection de prompt indirecte (document, e-mail, contenu mémorisé) | les contenus importés ne valent jamais autorisation ; le Guardian décide hors LLM ; sorties structurées validées (champs hors schéma ignorés) ; actions importantes soumises à approbation avec aperçu exact ; chaque donnée du contexte (mémoire, journal, tâches, objectifs, opérations) tient sur une ligne et ne peut pas imiter une section ; contexte d'écran limité à une liste fermée | 2 |
| T11 | Escalade d'agent | un agent ne peut pas s'octroyer de permission ni décider d'une approbation (refus audité `approval.approve`) ; `agent_permissions` gérées uniquement par l'utilisateur avec réauthentification récente ; agent sans permission valide (jamais accordée, retirée, expirée) refusé avant tout appel et audité (`chat.agent_reply`, `document.attach`) | 2 |
| T12 | Fuite inter-espaces | filtrage par espace dans chaque service ; objet d'un autre compte → `404` sur chaque route par identifiant ; `space_id` hors des espaces autorisés → `404` audité (`space.access`, un par minute et par compte) ; liens entre objets limités à un espace (`goal_space_mismatch`, `dependency_space_mismatch`) ; tests dédiés ([recette](recette-securite.md)) ; RLS prévu en défense en profondeur | 2 |
| T32 | Rejeu d'une approbation (requête rejouée, double clic, décision sur une proposition refusée ou expirée) | approbation à usage unique, ligne verrouillée pendant la décision ; `409 approval_not_pending` audité (`approval.replay`) ; paramètres corrigés revalidés par le schéma de l'action ; niveau renforcé : connexion récente exigée, refus audité | 2 |
| T13 | Fuite vers le fournisseur LLM | consentement par espace, paquet minimal, masquage local des identifiants (y compris espaces insécables, points, tirets, minuscules, confusions d'OCR, e-mails déguisés, numéros de pièces annoncés), contrôle avant l'envoi (ce qui sera masqué, ce qui reste local, confirmation ou annulation), budget, journal des appels | 2 |
| T14 | Exfiltration via la sortie du LLM (liens/images Markdown vers un domaine tiers) | réponses affichées en texte brut (aucun HTML ni Markdown interprété) ; CSP `img-src 'self' data:` et `connect-src 'self'` | 2 |
| T15 | Coût LLM incontrôlé (boucle, abus) | projet Gemini **sans compte de facturation** (rien ne peut être facturé, ADR 0007) ; plafonds quotidiens par palier et budget mensuel ; arrêt d'urgence ; aucune nouvelle tentative automatique ; limites de profondeur et d'appels par requête | 2, 3 |
| T16 | Hallucination présentée comme un fait | `origin` + `confidence` ; distinction fait / inférence dans l'interface ; corrections prioritaires | 2 |
| T17 | Fichiers malveillants (bombe de décompression ou de calcul, PDF, image ou audio piégés, fichiers déguisés ou polyglottes, HTML/SVG actifs) | liste blanche par **signature** (balisage refusé comme texte, même après BOM ou commentaire) ; taille bornée pendant la lecture ; analyse dans `ml` ou `stt` isolés (sans sortie, lecture seule, sans privilèges) ; **chaque analyse dans un processus enfant confiné** (`lifeos.isolation` : mémoire bornée, premier tué par le noyau si la mémoire manque, tué avec son groupe de processus au délai, Tesseract compris et récupéré par l'init du conteneur, sans fichier `core`) ; décodeur et démultiplexeur imposés par le type détecté, liste courte de codecs audio, piste vidéo jamais décodée ; contrôle des dimensions avant décodage (images, images d'un PDF) et du nombre de passes d'un JPEG ; audio décodé progressivement et refusé dès la durée maximale dépassée ; trois plantages de suite → échec définitif ; images ré-encodées à partir des pixels, jamais servies avant ; originaux servis en téléchargement (`nosniff`, `CSP sandbox`, `no-store`). **Pas d'antivirus** pour les fichiers déposés par le propriétaire (ADR 0011 : critères, mémoire, accès sortant) ; pièces jointes d'e-mail importées seulement à la demande, avec provenance et avertissement ; antivirus (`clamd` sans sortie, `freshclam` à part) si des fichiers de tiers sont un jour importés automatiquement | 4 |
| T24 | Fuite d'un document sensible vers le cloud (pièce jointe, agent) | classement local identité / santé / banque avant tout usage, retesté à chaque correction et de nouveau à l'envoi avec les règles en vigueur, reclassement des documents lus par d'anciennes règles (catégories seulement ajoutées) ; refus manuel « Toujours garder en local », jamais levé par une relecture et levé seulement avec une passkey récente ; refus côté serveur avant enregistrement du message ; agents limités aux métadonnées des tickets ; audit `document.attach` | 4 |
| T25 | Injection de prompt par le contenu d'un document joint | texte présenté comme données délimitées, délimiteurs neutralisés dans le texte (`‹`, `›`) et titre nettoyé sur une ligne : un document ne peut ni fermer son bloc ni forger un en-tête ; le Guardian décide hors LLM ; aucune action sans approbation (T10) | 4 |
| T26 | Métadonnées révélatrices (GPS, appareil) dans les photos | ré-encodage sans EXIF / XMP / ICC ; le fichier nettoyé remplace l'envoi ; une image n'est servie qu'une fois ré-encodée (`original_sanitized`, sinon 409) ; une image illisible (donc non nettoyable) n'est pas conservée, une lecture interrompue la garde pour une nouvelle lecture | 4 |
| T27 | Conservation abusive de la voix | enregistrement chiffré puis supprimé dès la transcription (ou le refus) ; dictées effacées après 24 h ; transcription locale ; lecture à voix haute avec les seules voix locales de l'appareil | 4 |
| T28 | Perte ou vol de la clé des documents | secret Docker séparé des données ; non inclus dans les sauvegardes (seule son empreinte SHA-256 tronquée accompagne chaque sauvegarde) ; copie hors du serveur via la fiche de récupération ; clé écrasée ou remplacée détectée par le test de restauration hebdomadaire (empreinte et déchiffrement des originaux), qui alerte ; une restauration avec une autre clé est refusée avant de remplacer quoi que ce soit | 4 |
| T18 | SSRF via connecteurs / URLs | hôtes de chaque fournisseur en liste blanche codée en dur (aucune URL venue d'une réponse ou de l'utilisateur), HTTPS seulement, redirections non suivies, délais explicites. Prévu pour des hôtes choisis par l'utilisateur (CalDAV, IMAP) : résolution puis refus des adresses privées, de bouclage et locales | 5 |
| T19 | Vol de jetons OAuth de connecteurs | jetons scellés (AES-256-GCM, clé `lifeos_connectors_key` séparée des données et des sauvegardes, compte et nature du jeton en données associées) ; jamais renvoyés par l'API, l'export ni l'audit ; scopes minimaux par usage, scopes réellement accordés enregistrés ; connexion renforcée (passkey récente) ; déconnexion immédiate : révocation chez le fournisseur (jeton dans le corps), effacement local même en cas d'échec ; jeton refusé → compte suspendu et jetons effacés | 5 |
| T29 | CSRF ou rejeu du retour OAuth (lier le compte d'un tiers, rejouer un code) | `state` de 256 bits à usage unique, lié au propriétaire, valable 10 minutes, seule son empreinte en base ; état consommé avant l'échange du code ; retour reçu par une page du même site qui poste le code (cookie `SameSite=Strict` conservé) | 5 |
| T30 | Interception du code d'autorisation | PKCE S256 (vérificateur scellé côté serveur) et client confidentiel ; adresse de retour HTTPS exacte enregistrée chez le fournisseur ; code retiré de la barre d'adresse et de l'historique avant l'envoi ; `Referrer-Policy: no-referrer` ; aucun journal d'accès (Traefik, Next.js). Un reverse proxy en amont peut journaliser l'URL de retour : le code y est inutilisable (usage unique, consommé aussitôt, PKCE) | 5 |
| T31 | Jetons ou codes dans les journaux | aucun corps de requête ni de réponse journalisé ; jetons jamais dans une URL (le code d'autorisation n'y passe qu'une fois, dans la redirection du fournisseur : à usage unique, inutile sans le vérificateur PKCE, retiré de la barre d'adresse dès la page de retour, voir T30) ; erreurs du fournisseur réduites à un code ; filtre de journalisation qui masque toute valeur ressemblant à un jeton (`ya29.`, `1//`, JWT, `code=`, `access_token=`…) ; journaux `httpx`/`httpcore` au niveau WARNING ; test qui parcourt connexion, renouvellement, erreurs et déconnexion en DEBUG sans retrouver aucun jeton (journaux, audit, réponses, export) | 5 |
| T20 | Compromission de l'hôte / vol physique | chiffrement des volumes, mises à jour (`update.sh` : sauvegarde préalable, contrôle de santé, retour arrière, versions déployées journalisées), pare-feu, accès distant par VPN ; node exporter sans privilège, racine de l'hôte en lecture seule, aucun port publié ; signal de vie hors de la machine (une VM arrêtée ou compromise qui se tait est signalée) | exploitation |
| T21 | Fuite par les embeddings (un vecteur permet en partie de reconstituer le texte) | calcul **local** (service `ml` sans accès sortant, modèle hors ligne), vecteurs dans la base comme le reste, filtrés par propriétaire et espace, supprimés par trigger avec l'objet source | 3 |
| T22 | Agent spécialisé qui lit trop | accès en lecture seulement aux ressources de l'agent, par espace, 90 jours, accordé avec réauthentification, révocable ; contexte construit hors LLM avec les droits de l'agent | 3 |
| T23 | Mémoire polluée par une extraction erronée (consolidation, journal) | propositions soumises à approbation avec aperçu exact ; seuls les messages du propriétaire sont analysés ; file « À vérifier » (doublons, contradictions) | 3 |

Injection de script (XSS) dans le client web, en défense en profondeur derrière l'affichage
en texte brut (T14) : la CSP est produite à chaque requête, avec un nonce aléatoire de
128 bits (`apps/web/src/proxy.ts`, règles dans `apps/web/src/lib/csp.ts`). Les scripts sont
limités par `script-src 'self' 'nonce-…' 'strict-dynamic'`, sans `'unsafe-inline'` ni, en
production, `'unsafe-eval'` : un script ou un gestionnaire d'événement injecté dans la page
n'a pas le nonce et ne s'exécute pas. Toutes les pages sont rendues à chaque requête et
jamais mises en cache (`no-store`). Limites restantes :

- `style-src` garde `'unsafe-inline'` : un attribut `style` ne peut pas porter de nonce, et
  Next.js en utilise dans ses pages 404 et 500 intégrées. Un style injecté ne peut ni
  exécuter de code ni joindre un autre site (`img-src`, `font-src`, `connect-src` limités à
  l'origine) ;
- `'strict-dynamic'` fait confiance aux scripts créés par un script déjà autorisé : une faille
  DOM dans le code même de l'application resterait exploitable (d'où l'interdiction du HTML
  interprété, T14) ;
- un reverse proxy en amont ne doit ni ajouter ni réécrire de CSP, ni mettre les pages en
  cache ([exploitation.md §3](exploitation.md#politique-de-sécurité-du-contenu-csp)).

## 4. Tests de sécurité associés

La [recette de sécurité](recette-securite.md) détaille chaque scénario interdit : résultat
attendu, code d'erreur, événement d'audit et test (`backend/tests/test_security.py` pour
l'essentiel). En résumé :

- Auth : rejeu de défi, défi expiré, jeton d'enrôlement réutilisé, session révoquée, expirée
  ou inactive, compte désactivé, attributs du cookie, requête sans `Origin` valide.
- Sessions volées (T2) : cookie copié dans un autre client signalé, inutilisable dès sa
  révocation depuis un autre appareil (refus audité une fois par heure, même en boucle) ;
  « Déconnecter les autres sessions » ; opérations renforcées refusées et auditées avec une
  connexion ancienne.
- CSRF (T3) : origines étrangère, `null`, suffixée, autre port, autre schéma ou absente,
  sur `POST`, `PATCH`, `DELETE`, envoi `multipart` et approbation → `403`, aucun changement
  d'état, refus audité et limité.
- Guardian : action soumise à approbation exécutée sans approbation → refusée et auditée ;
  agent sans permission sur un espace → refusé ; permission expirée, révoquée ou d'un autre
  espace → aucun appel au LLM, refus audité ; un agent ne peut pas approuver.
- Rejeu d'approbations (T32) : deuxième décision, décisions simultanées, proposition
  refusée ou expirée → exécutée au plus une fois, rejeu audité ; paramètres hors schéma
  refusés ; approbation renforcée sans connexion récente refusée.
- Espaces (T12) : aucune recherche ni aucun paquet de contexte (assistant général et
  chaque agent) ne contient de donnée d'un autre espace ; objets d'un autre compte → `404`
  sur chaque route par identifiant, sans modification ; `space_id` d'un autre compte →
  `404` audité.
- Injections indirectes (T10, T25) : fait mémorisé ou document qui donne des ordres → aucune
  approbation, permission ni tâche ; une donnée ne peut ni ouvrir une section du contexte,
  ni fermer son bloc, ni forger l'en-tête d'une pièce jointe ; sortie structurée réduite à
  son schéma ; contexte d'écran libre refusé.
- Cloud : espace sans consentement → aucun appel ; budget atteint → aucun appel ; données
  sensibles masquées dans le paquet envoyé ; variantes d'identifiants (espaces insécables et
  fines, points, tirets, minuscules, OCR `O`/`0` et `l`/`1`, `0033` et `+33 (0)`, e-mails
  déguisés, mots de passe et codes sans « : », numéros de pièces annoncés) masquées, textes
  ordinaires (SIRET, montants, dates) intacts ; le contrôle avant l'envoi n'enregistre et
  n'envoie rien et ne montre aucune valeur.
- Documents : formats dangereux refusés (SVG, HTML, exécutables, archives) ; bombe de
  décompression refusée avant décodage ; EXIF/GPS absents de l'image conservée ; chiffrement
  lié au document ; document sensible ou gardé en local refusé comme pièce jointe sans
  message enregistré, y compris un document classé non sensible par d'anciennes règles ;
  agent sans droit `documents` refusé ; « Toujours garder en local » conservé après
  correction, renommage et relecture, levé seulement avec une passkey récente ; catégorie
  détectée jamais retirée par le propriétaire ni par le reclassement ; corpus synthétique de
  documents français (`backend/tests/fixtures/sensitivity`, bruit d'OCR et variantes
  typographiques) ; `lifeos-admin sensitivity-check` n'affiche aucune valeur ; photo en
  cours de lecture jamais servie ; polyglotte PDF + HTML servi comme un PDF à télécharger.
- Fichiers hostiles (ADR 0011, corpus généré dans le code, job CI `ml-model`) : PDF
  corrompus ou piégés (tronqués, boucles d'arbre de pages, de table xref et de longueur,
  imbrication profonde, nombre de pages et taille de page démesurés, JavaScript, `/Launch`,
  fichier joint, chiffrés, images et instructions de dessin géantes, polyglotte HTML),
  images démesurées ou malformées (zTXt et iCCP de 1 Go, PNG tronqué ou animé, toile WebP
  géante, JPEG à 2 000 passes, en-têtes JPEG et HEIC mensongers, polyglotte JPEG + ZIP) et
  audio malformé ou déguisé (vidéos, codecs hors liste, AC-3 derrière ID3, en-têtes WAV
  mensongers, bruit après chaque signature, type déclaré faux) → échec rapide avec un code
  stable et définitif, ou résultat borné, sans processus restant ; Tesseract bloqué tué avec
  l'enfant ; décodeur qui plante → seul l'enfant meurt ; santé et embeddings répondent
  pendant une extraction lente ; le modèle de transcription reste chargé et transcrit la
  dictée suivante ; enfant dans son propre groupe, `oom_score_adj` 1000, sans `core`.
- Voix : enregistrement supprimé après transcription comme après refus ; dictées hors
  recherche et index ; une seule transcription à la fois ; enregistrement hostile refusé
  avant d'atteindre le modèle.
- Comptes connectés : connexion sans passkey récente → refusée ; état OAuth rejoué, expiré
  ou d'un autre propriétaire → refusé ; vérificateur PKCE faux ou refus chez le fournisseur
  → aucun compte ; scopes décochés chez le fournisseur → seuls les usages accordés ;
  jetons illisibles sous un autre compte ou une autre colonne ; renouvellement unique pour
  deux appelants simultanés ; jeton refusé → compte suspendu, jetons effacés, une seule
  notification ; usage sans consentement de l'espace, hors de l'espace (404) ou par un agent
  sans permission → refusé et audité ; déconnexion → révocation chez le fournisseur avec le
  jeton dans le corps, plus aucun accès ni consentement, approbations expirées, données
  gardées ou supprimées au choix ; aucun jeton dans les journaux en DEBUG, l'audit, les
  réponses ni l'export ; fournisseur Google simulé (`httpx.MockTransport`), jamais le réseau.
- Supervision : chaque libellé des métriques exposées est dans une liste fermée, sans
  adresse e-mail ni identifiant ; une alerte dont les libellés portent une adresse ou un
  titre de document produit une notification qui n'en contient rien ; les notifications
  n'ont ni lien, ni libellé, ni nom de domaine (`scripts/test-monitoring.sh`).
- Recette : le rapport et les mesures brutes de `infra/scripts/recette.sh` ne contiennent ni
  le domaine, ni l'adresse e-mail, ni la session copiée, ni un titre, ni une réponse de
  l'assistant ; le document et la conversation de la recette sont supprimés
  (`scripts/test-recette.sh`).
- Mises à jour : chaque migration redescend puis remonte, et un retour arrière garde les
  protections (un document gardé en local reste sensible) ; mise à jour, retour arrière,
  migration à descente avec perte et migration en échec joués de bout en bout
  (`backend/tests/test_migrations.py`, `scripts/test-update.sh`).
- Client web (CSP) : chaque page, publique, authentifiée ou 404, reçoit un nonce différent à
  chaque réponse, porté par tous ses scripts, sans `'unsafe-inline'` ; un nonce envoyé par le
  client est ignoré ; script en ligne, nonce deviné, script de même origine et gestionnaire
  d'événement injectés → bloqués, la page restant fonctionnelle ; aucun parcours de bout en
  bout (passkeys, navigation, assistant, documents, dictée) ne provoque de violation de CSP.
