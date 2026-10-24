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
| Sessions et passkeys | élevée : accès à tout le reste |
| Clé d'API Gemini, secrets d'infrastructure | élevée : coût, accès |
| Sauvegardes | très élevée : copie complète |

## 2. Frontières de confiance

```
Navigateur / mobile ──HTTPS── Traefik ──┬── API ──┬── PostgreSQL (réseau interne)
                                        │         ├── Redis (réseau interne)
                                        │         ├── ml : embeddings, OCR (interne, sans sortie)
                                        │         ├── stt : transcription (interne, sans sortie)
                                        └── Web   └── Passerelle cloud ──HTTPS── Gemini
Contenus importés (documents, e-mails, pages web) = NON FIABLES
Sorties du LLM = NON FIABLES (validées par schéma avant toute action)
```

## 3. Menaces et parades

| # | Menace | Parades | Étape |
|---|---|---|---|
| T1 | Phishing / vol d'identifiants | passkeys (résistantes au phishing), aucun mot de passe | 1 |
| T2 | Vol de cookie de session | `HttpOnly`, `Secure`, `SameSite=Strict`, empreinte seule en base, expiration absolue et d'inactivité, révocation, liste des sessions | 1 |
| T3 | CSRF | `SameSite=Strict` + vérification `Origin` sur les requêtes non sûres | 1 |
| T4 | Force brute / déni sur l'auth | limitation de débit Traefik par adresse du client (derrière un reverse proxy : la dernière adresse de `X-Forwarded-For`, celle que le proxy ajoute lui-même, jamais une valeur écrite par le client), jetons d'enrôlement 256 bits à durée courte | 1 |
| T5 | Exposition des services internes | réseau Docker `internal`, seul Traefik publie 80/443 (derrière un reverse proxy existant : aucun port, joignable par le seul réseau `lifeos-proxy`), `/metrics` non routé | 1 |
| T6 | Secrets dans Git | `.gitignore`, secrets Docker, `.env.example` sans valeur | 1 |
| T7 | Fuite de données dans les logs | logs sans corps ni prompts ; audit sans contenu | 1 |
| T8 | Vol ou perte des sauvegardes | chiffrement restic ; clé des documents et mots de passe hors des sauvegardes (fiche de récupération, `export-keys.sh`) ; copie hors machine automatique avec la même rétention ; restauration testée chaque semaine (et chaque mois depuis la copie) ; alerte à chaque échec et signal de vie externe | 1 |
| T9 | Dépendances compromises | versions verrouillées (`uv.lock`, `pnpm-lock.yaml`), audit des dépendances en CI | 1 |
| T10 | Injection de prompt indirecte (document, e-mail, contenu mémorisé) | les contenus importés ne valent jamais autorisation ; le Guardian décide hors LLM ; sorties structurées validées ; actions importantes soumises à approbation avec aperçu exact | 2 |
| T11 | Escalade d'agent | un agent ne peut pas s'octroyer de permission ; `agent_permissions` gérées uniquement par l'utilisateur avec réauthentification récente | 2 |
| T12 | Fuite inter-espaces | filtrage par espace dans chaque service ; tests dédiés ; RLS prévu en défense en profondeur | 2 |
| T13 | Fuite vers le fournisseur LLM | consentement par espace, paquet minimal, masquage local des identifiants (y compris espaces insécables, points, tirets, minuscules, confusions d'OCR, e-mails déguisés, numéros de pièces annoncés), contrôle avant l'envoi (ce qui sera masqué, ce qui reste local, confirmation ou annulation), budget, journal des appels | 2 |
| T14 | Exfiltration via la sortie du LLM (liens/images Markdown vers un domaine tiers) | réponses affichées en texte brut (aucun HTML ni Markdown interprété) ; CSP `img-src 'self' data:` et `connect-src 'self'` | 2 |
| T15 | Coût LLM incontrôlé (boucle, abus) | projet Gemini **sans compte de facturation** (rien ne peut être facturé, ADR 0007) ; plafonds quotidiens par palier et budget mensuel ; arrêt d'urgence ; aucune nouvelle tentative automatique ; limites de profondeur et d'appels par requête | 2, 3 |
| T16 | Hallucination présentée comme un fait | `origin` + `confidence` ; distinction fait / inférence dans l'interface ; corrections prioritaires | 2 |
| T17 | Fichiers malveillants (bombe de décompression ou de calcul, PDF, image ou audio piégés, fichiers déguisés ou polyglottes, HTML/SVG actifs) | liste blanche par **signature** (balisage refusé comme texte, même après BOM ou commentaire) ; taille bornée pendant la lecture ; analyse dans `ml` ou `stt` isolés (sans sortie, lecture seule, sans privilèges) ; **chaque analyse dans un processus enfant confiné** (`lifeos.isolation` : mémoire bornée, premier tué par le noyau si la mémoire manque, tué avec son groupe de processus au délai, Tesseract compris et récupéré par l'init du conteneur, sans fichier `core`) ; décodeur et démultiplexeur imposés par le type détecté, liste courte de codecs audio, piste vidéo jamais décodée ; contrôle des dimensions avant décodage (images, images d'un PDF) et du nombre de passes d'un JPEG ; audio décodé progressivement et refusé dès la durée maximale dépassée ; trois plantages de suite → échec définitif ; images ré-encodées à partir des pixels, jamais servies avant ; originaux servis en téléchargement (`nosniff`, `CSP sandbox`, `no-store`). **Pas d'antivirus** pour les fichiers déposés par le propriétaire (ADR 0011 : critères, mémoire, accès sortant) ; pièces jointes d'e-mail importées seulement à la demande, avec provenance et avertissement ; antivirus (`clamd` sans sortie, `freshclam` à part) si des fichiers de tiers sont un jour importés automatiquement | 4 |
| T24 | Fuite d'un document sensible vers le cloud (pièce jointe, agent) | classement local identité / santé / banque avant tout usage, retesté à chaque correction et de nouveau à l'envoi avec les règles en vigueur, reclassement des documents lus par d'anciennes règles (catégories seulement ajoutées) ; refus manuel « Toujours garder en local », jamais levé par une relecture et levé seulement avec une passkey récente ; refus côté serveur avant enregistrement du message ; agents limités aux métadonnées des tickets ; audit `document.attach` | 4 |
| T25 | Injection de prompt par le contenu d'un document joint | texte présenté comme données délimitées ; le Guardian décide hors LLM ; aucune action sans approbation (T10) | 4 |
| T26 | Métadonnées révélatrices (GPS, appareil) dans les photos | ré-encodage sans EXIF / XMP / ICC ; le fichier nettoyé remplace l'envoi ; l'envoi brut n'est jamais servi pendant la lecture (409) ; une image illisible (donc non nettoyable) n'est pas conservée | 4 |
| T27 | Conservation abusive de la voix | enregistrement chiffré puis supprimé dès la transcription (ou le refus) ; dictées effacées après 24 h ; transcription locale ; lecture à voix haute avec les seules voix locales de l'appareil | 4 |
| T28 | Perte ou vol de la clé des documents | secret Docker séparé des données ; non inclus dans les sauvegardes (seule son empreinte SHA-256 tronquée accompagne chaque sauvegarde) ; copie hors du serveur via la fiche de récupération ; clé écrasée ou remplacée détectée par le test de restauration hebdomadaire (empreinte et déchiffrement des originaux), qui alerte ; une restauration avec une autre clé est refusée avant de remplacer quoi que ce soit | 4 |
| T18 | SSRF via connecteurs / URLs | liste d'hôtes autorisés, pas de requête vers des adresses privées | 5 |
| T19 | Vol de jetons OAuth de connecteurs | chiffrement au repos, scopes minimaux, révocation | 5 |
| T20 | Compromission de l'hôte / vol physique | chiffrement des volumes, mises à jour, pare-feu, accès distant par VPN | exploitation |
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

- Auth : rejeu de défi, défi expiré, jeton d'enrôlement réutilisé, session révoquée ou expirée,
  requête sans `Origin` valide.
- Guardian : action soumise à approbation exécutée sans approbation → refusée et auditée ;
  agent sans permission sur un espace → refusé ; permission expirée ou révoquée → refusée.
- Espaces : aucune recherche ni aucun paquet de contexte ne contient de donnée d'un autre
  espace.
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
- Client web (CSP) : chaque page, publique, authentifiée ou 404, reçoit un nonce différent à
  chaque réponse, porté par tous ses scripts, sans `'unsafe-inline'` ; un nonce envoyé par le
  client est ignoré ; script en ligne, nonce deviné, script de même origine et gestionnaire
  d'événement injectés → bloqués, la page restant fonctionnelle ; aucun parcours de bout en
  bout (passkeys, navigation, assistant, documents, dictée) ne provoque de violation de CSP.
