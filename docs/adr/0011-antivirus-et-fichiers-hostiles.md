# ADR 0011 — Fichiers hostiles : confinement et limites, pas d'antivirus pour l'instant

- Statut : acceptée (précise l'alternative « Antivirus » de l'[ADR 0008](0008-documents-ocr-local.md))
- Date : 2026-10-20

## Contexte

Les fichiers importés (documents F10, tickets UC02, pièces jointes du chat F01, dictées
UC01) sont lus par de grosses bibliothèques écrites en C : PDFium, libjpeg-turbo, libpng,
libwebp, libheif et FFmpeg. Un fichier peut être abîmé par accident ou piégé exprès.

**Ce qu'un fichier hostile peut viser :**

1. **Le serveur.** Il peut épuiser la mémoire ou le processeur (bombe de décompression,
   JPEG progressif à milliers de passes), faire planter un décodeur ou laisser un
   processus tourner. Une faille de décodeur peut aussi donner l'exécution de code dans le
   service qui lit le fichier.
2. **Le navigateur du propriétaire.** Un « texte » qui est en fait du HTML, un polyglotte
   PDF + HTML ou un SVG pourraient exécuter un script sur le domaine de LifeOS.
3. **Le poste du propriétaire.** L'original téléchargé peut ensuite être ouvert dans un
   lecteur local : JavaScript d'un PDF dans Acrobat, macro, exécutable déguisé.
4. **La vie privée.** Une photo peut contenir sa position GPS.

**Les parades existaient déjà en partie** (ADR 0008 et 0009) :

- liste blanche par signature et taille bornée pendant la lecture ;
- services `ml` et `stt` isolés : sans accès sortant, en lecture seule, sans privilèges ;
- extraction dans un processus enfant à mémoire bornée ;
- images ré-encodées à partir des pixels ;
- originaux servis en téléchargement (`nosniff`, `CSP sandbox`).

L'ADR 0008 écartait l'antivirus en un paragraphe, « à reconsidérer avec les connecteurs
e-mail ». La tâche 05 demande de tester les fichiers hostiles et de trancher.

**La campagne de tests a trouvé des failles.** Elle a joué un corpus de fichiers hostiles
générés dans le code (`backend/tests_ml/test_hostile_files.py`,
`backend/tests_stt/test_hostile_audio.py`) :

- **Tesseract survivait à l'enfant.** Une extraction bloquée était abandonnée, mais le
  processus Tesseract qu'elle avait lancé continuait de tourner. Une fois tué, il restait
  à l'état de zombie dans le conteneur `ml` : orphelin, il revient au processus 1, qui est
  le service lui-même et ne le récupère pas (mesuré dans un conteneur durci comme `ml`).
- **Le noyau tuait le service, pas l'enfant**, quand la mémoire du conteneur manquait.
  Mesure dans un conteneur limité à 400 Mo, où le service tient 260 Mo (comme le modèle
  d'embeddings) et l'enfant alloue sans fin : sans réglage, le noyau tue le service ; avec
  `oom_score_adj` à 1000 sur l'enfant, il tue l'enfant et le service continue.
- **Un plantage pouvait produire un fichier `core`**, qui contient le document déchiffré
  (selon la configuration de l'hôte, recueilli hors du conteneur par son gestionnaire de
  plantages).
- **La dictée était décodée dans le processus du service**, avec le modèle chargé :
  - pas de limite de mémoire ni de délai ;
  - un plantage de FFmpeg arrêtait le service ;
  - FFmpeg devinait le format d'après le contenu : derrière une balise ID3, un flux AC-3
    était lu par l'analyseur AC-3 ;
  - n'importe lequel des 213 décodeurs audio de FFmpeg (version embarquée par PyAV)
    pouvait être ouvert ;
  - une vidéo MP4 passe pour de l'audio (marques génériques `isom`, `mp42`, que les
    navigateurs utilisent aussi), et FFmpeg ouvrait son décodeur vidéo pour sonder les
    flux.
- **Un JPEG progressif à milliers de passes** est une bombe de calcul : 50 Ko et
  2 000 passes demandent 6 s de processeur, et quelques Mo occuperaient le service
  jusqu'à la limite de temps.
- **Du HTML était accepté comme texte** quand il commençait par une marque d'ordre des
  octets (BOM), un commentaire `<!-- -->` ou une balise courante (`<p>`, `<a>`,
  `<iframe>`…).
- **L'envoi brut d'une photo restait téléchargeable** pendant sa lecture, avec ses
  métadonnées GPS, avant d'être remplacé par l'image ré-encodée.

## Décision

1. **Pas d'antivirus pour les fichiers que le propriétaire dépose lui-même.** Critères :

   | Critère | Constat |
   |---|---|
   | Provenance | Le propriétaire seul, authentifié par passkey. Il a déjà le fichier sur son poste : un antivirus côté serveur ne protégerait que ce qu'il possède déjà. |
   | Menaces couvertes | Un antivirus reconnaît surtout des maliciels **connus** : exécutables, macros, archives, PDF piégés diffusés en masse. Les exécutables, documents bureautiques et archives sont déjà refusés à la signature. Il ne protège ni un décodeur contre un fichier conçu pour sa faille (inconnue des signatures), ni contre une bombe mémoire ou de calcul, ni le navigateur contre un polyglotte. |
   | Mémoire | `clamd` garde ses signatures en mémoire : ordre de grandeur publié par le projet, 1 à 1,5 Go en permanence, et le double pendant chaque rechargement des signatures (réglage par défaut). Non mesuré ici, faute d'image et de réseau. Sur la VM de 10 Go, c'est le prix des embeddings (ADR 0006). |
   | Mises à jour | Sans signatures fraîches, un antivirus ne sert à rien. `freshclam` doit joindre les miroirs de ClamAV plusieurs fois par jour, alors que les services qui lisent les fichiers (`ml`, `stt`, worker) n'ont pas d'accès sortant. Il faudrait un service de plus sur le réseau `egress`. |
   | Surface d'attaque | ClamAV analyse lui-même des centaines de formats, et a eu ses propres failles d'exécution de code (par exemple CVE-2023-20032, analyseur HFS+). Ajouter un analyseur, c'est ajouter une surface. |
   | Faux positifs | Un document légitime bloqué ou supprimé à tort. |

   Le risque se traite par les parades directes : refuser ce qui n'a pas à être lu, lire le
   reste dans un enfant jetable et confiné, ne jamais rendre un fichier dans le navigateur.

2. **Confinement commun aux services `ml` et `stt` (`lifeos.isolation`).** Chaque analyse
   d'un fichier non fiable tourne dans un processus enfant, issu d'un serveur de fork qui a
   préchargé les bibliothèques (démarrage en quelques millisecondes) :
   - **Mémoire bornée.** La limite porte sur le segment de données et les projections
     privées (`RLIMIT_DATA`) : une allocation échoue au lieu d'épuiser le service.
     - Extraction : 1,25 Gio par défaut (`LIFEOS_EXTRACTION_MEMORY_MB`). Tesseract, que
       l'enfant lance pour l'OCR, hérite de la même limite pour lui-même : une lecture avec
       OCR peut donc occuper jusqu'à deux fois cette valeur (en pratique bien moins, voir
       les pics mesurés ci-dessous).
     - Décodage d'une dictée : 512 Mo, plus trois fois la taille des échantillons de la
       durée maximale.
   - **Premier choisi par le noyau** quand la mémoire du conteneur manque malgré tout
     (`oom_score_adj` à 1000, hérité par Tesseract) : jamais le service ni son modèle.
   - **Délai.** Au-delà, l'enfant est tué avec **tout son groupe de processus**,
     Tesseract compris : rien ne continue de tourner. Le conteneur `ml` démarre avec un
     processus d'init minimal (`init: true` dans `compose.yaml`), qui récupère ces
     orphelins : aucun zombie ne s'accumule sous le service.
   - **Aucun fichier `core`** (`RLIMIT_CORE` à 0) : un plantage n'écrit pas le contenu
     déchiffré sur le disque.
   - Pic mémoire de chaque analyse journalisé (`peak_mb`, `decode_peak_mb`), sans
     contenu : il sert à dimensionner (`docs/exploitation.md` §7).
   - **Échec stable.** Un enfant tué ou planté fait échouer ce fichier seul, avec un code
     stable et définitif ; le service continue de répondre (santé, embeddings) pendant
     l'analyse.

3. **Décodage audio durci (`lifeos.stt.engine`).**
   - Il tourne dans un enfant confiné : un plantage de FFmpeg ne décharge plus le modèle.
   - Le démultiplexeur est imposé par le type détecté à la signature (WebM → Matroska,
     Ogg, MP4, WAV, MP3) : FFmpeg ne devine jamais le format.
   - Liste courte de codecs : Opus, AAC, MP3, Vorbis, FLAC, ALAC et PCM.
     - Les autres décodeurs ne sont jamais ouverts, pas même pour sonder les flux
       (`codec_whitelist`).
     - Un autre codec est refusé (`unsupported_codec`).
   - Une piste vidéo n'est jamais décodée, et un fichier sans piste audio est refusé
     (`unreadable`).

4. **Autres corrections.**
   - **Images** : un JPEG à plus de 500 passes est refusé avant tout décodage.
   - **Texte** : un début qu'un navigateur prendrait pour du HTML ou du XML (algorithme
     de détection du WHATWG : commentaire, `<p>`, `<a>`, `<iframe>`, `<svg>`…) est
     refusé, même après une marque d'ordre des octets ou des espaces. Plus loin dans le
     fichier, le balisage reste du texte : les navigateurs ne regardent que le début, et
     l'original est servi en `text/plain` à télécharger.
   - **Photos en cours de lecture** : l'original n'est jamais servi (`409
     document_processing`). Seule l'image ré-encodée l'est, une fois lue ; l'interface
     masque le lien en attendant.
   - **Polyglottes** : un fichier garde le type de sa signature, et le décodeur est imposé
     par ce type.
     - Une image est ré-encodée à partir des pixels : ce qui suivait disparaît.
     - Un PDF n'est servi qu'en téléchargement (`attachment`, `nosniff`, `CSP sandbox`),
       sous un nom à l'extension `.pdf`.
   - **Messages en français** pour les codes `extraction_crashed`,
     `transcription_crashed`, `unsupported_codec` et `too_slow`.

5. **Le corpus fait partie de la CI (job `ml-model`).** Chaque cas doit échouer avec son
   code, ou donner un résultat borné et inoffensif, en quelques secondes, sans processus
   restant :
   - **PDF** : tronqués, bruit, arbre de pages ou table xref qui bouclent, longueur
     récursive, cent mille tableaux imbriqués, deux milliards de pages annoncées, page
     de 10^30 points, JavaScript, `/Launch` et fichier joint, PDF chiffré, image ou
     instructions de dessin géantes, polyglotte HTML ;
   - **images** : commentaires et profils de couleur compressés de 1 Go, PNG tronqué,
     PNG animé de 2 000 images, toile WebP de 16 384 px de côté, JPEG à 2 000 passes,
     en-têtes JPEG et HEIC mensongers, polyglotte JPEG + ZIP ;
   - **audio** : vidéos sans son, vidéo avec son (seul le son est décodé), codecs hors
     liste (ADPCM, MP2, AC-3), AC-3 derrière une balise ID3, en-têtes WAV mensongers
     (tailles de 4 Go, 65 535 canaux, 1 Hz), bruit derrière chaque signature, WebM
     tronqué, Ogg déclaré sous un autre type ;
   - **confinement** : enfant dans son propre groupe, `oom_score_adj`, sans `core` ; un
     Tesseract bloqué est tué avec l'enfant ; un décodeur qui plante n'emporte que
     l'enfant ; le service répond pendant une extraction lente ; le client de l'API ne
     réessaie jamais un refus.

6. **Fichiers de tiers : l'e-mail et la suite (tâches 13 à 18).** La décision est
   réexaminée pour les connecteurs, car le propriétaire ne choisit pas l'expéditeur
   (hameçonnage, pièces jointes piégées).
   - **Pièces jointes non importées par défaut.** Le connecteur lit le texte des messages
     et la liste des pièces jointes (nom, type, taille). Une pièce jointe ne devient un
     document que sur une action explicite du propriétaire, pièce par pièce. Elle suit
     alors exactement le chemin d'un envoi : signature, taille, lecture confinée,
     ré-encodage, sensibilité.
   - **Toujours pas d'antivirus pour cet import explicite.** La messagerie analyse déjà
     les pièces jointes à la réception (Gmail bloque les maliciels connus). LifeOS n'en
     exécute aucune et n'en affiche aucune dans le navigateur. Ouvrir l'original sur son
     poste revient à l'ouvrir depuis la messagerie.
   - **Exigences pour le connecteur e-mail :**
     - la provenance « pièce jointe reçue de … » reste visible sur le document ;
     - un avertissement précède le téléchargement de l'original d'un tiers ;
     - le contenu actif d'un PDF (`/JavaScript`, `/JS`, `/OpenAction`, `/AA`,
       `/Launch`, `/EmbeddedFile`, `/RichMedia`, `/XFA`, noms échappés `#xx` compris)
       est détecté localement et signalé. C'est un avertissement, pas un refus :
       beaucoup de formulaires légitimes en contiennent, et PDFium n'en exécute aucun ;
     - son texte reste des **données**, jamais des instructions (T25).
   - **Ce qui déclencherait l'antivirus :**
     - l'import **automatique** de pièces jointes, sans geste du propriétaire pour chaque
       fichier ;
     - un connecteur qui importe des fichiers de tiers en masse (dossier partagé) ;
     - une VM qui rend le coût acceptable (16 Go ou plus).
   - **Conception retenue dans ce cas :**
     - un profil Compose `antivirus`, avec `clamd` sur le réseau `internal` sans accès
       sortant ;
     - `freshclam` à part sur `egress`, qui écrit les signatures dans un volume monté en
       lecture seule par `clamd` ;
     - le worker envoie le fichier déchiffré à `clamd` (`INSTREAM`), en mémoire, **avant**
       toute extraction et avant de rendre le document visible ;
     - fichier détecté : statut `rejected` (déjà prévu par le modèle de données), code
       `malware_detected`, original supprimé, audit sans contenu (le nom de la
       signature reconnue seulement) ;
     - `clamd` indisponible : nouvel essai plus tard, jamais d'import sans analyse ;
     - mémoire prévue dans `compose.yaml` et dans `docs/exploitation.md`.

## Alternatives écartées

- **ClamAV dès maintenant.** Son coût en mémoire et en accès sortant est fixe, pour un
  gain faible sur des fichiers que le propriétaire possède déjà (critères ci-dessus).
- **Analyse en ligne (VirusTotal ou équivalent).** Elle envoie le fichier, ou au moins son
  empreinte, à un tiers. Les documents sont des pièces d'identité, des ordonnances et des
  relevés bancaires (politique de données). Et une empreinte seule ne reconnaît que des
  fichiers déjà connus.
- **Refuser les PDF qui contiennent du JavaScript ou des actions.** Des formulaires
  administratifs et des factures légitimes en contiennent, et PDFium n'en exécute aucun.
  Pour les fichiers de tiers, un avertissement suffit (point 6).
- **Supprimer l'original d'un PDF qui échoue.** Un PDF protégé par mot de passe, trop
  long, trop lent ou trop gros reste un fichier légitime du propriétaire, qui doit pouvoir
  le récupérer. LifeOS ne le sert qu'en téléchargement, jamais lu ni affiché. Il est purgé
  après 90 jours comme les autres. Une image illisible, elle, n'est pas conservée
  (ADR 0008), puisqu'elle ne peut pas être nettoyée.
- **Un bac à sable plus fort (gVisor, nsjail, filtre seccomp propre à l'enfant).**
  - gVisor demande de changer le moteur de conteneurs de l'hôte.
  - Un filtre seccomp par enfant ajoute une dépendance native, et sa liste d'appels
    système est à maintenir pour chaque bibliothèque.
  - Les conteneurs `ml` et `stt` sont déjà sans accès sortant, en lecture seule, sans
    capacités et sans secrets. Une exécution de code y trouverait peu de chose, et ne
    pourrait rien faire sortir.
  - À reconsidérer avec l'import automatique de fichiers de tiers.
- **Limiter l'espace d'adressage (`RLIMIT_AS`)** plutôt que le segment de données. Cela
  casse PDFium, qui réserve de grandes plages virtuelles dès le départ.

## Conséquences

- **Pas de mémoire en plus pour un antivirus.**
  - Un serveur de fork par service : quelques dizaines de Mo.
  - Pics mesurés pour l'extraction, Tesseract compris : PDF texte de 30 pages ≈ 25 Mo,
    page scannée ≈ 0,2 Go, photo de 12 Mpx ≈ 0,4 Go.
  - Pic mesuré pour le décodage d'une dictée de 15 minutes : ≈ 0,16 Go.
- **Un enfant à court de sa propre mémoire, ou planté par le fichier, fait échouer le
  fichier** : `too_large` pour un document, `unreadable` pour une dictée (définitif).
- **Un enfant tué par le noyau (SIGKILL) n'est pas un verdict sur le fichier.** Le noyau le
  choisit en premier quand le conteneur manque de mémoire, peut-être pendant une pointe des
  embeddings : la lecture échoue avec `extraction_crashed` ou `transcription_crashed`
  (500), l'API la relance quelques fois (`MAX_FAILURES`), et l'original est gardé, même
  celui d'une photo (non servi tant qu'il n'a pas été ré-encodé).
- **Formats de dictée.** Une dictée dans un codec hors liste est refusée
  (`unsupported_codec`). La liste couvre les navigateurs (Opus, AAC) et les fichiers
  courants. Tout nouveau codec accepté y entre avec un cas de test.
- **Originaux des PDF qui échouent.** Ils restent téléchargeables 90 jours, en pièce
  jointe, jamais affichés.
- **Le corpus grandit avec les formats.** Tout nouveau format ou décodeur (OpenCV, autre
  démultiplexeur) ajoute ses cas au corpus, et son décodeur reste imposé par le type
  détecté.
- **Revue de la décision** à l'arrivée des connecteurs e-mail (tâches 13 à 18), puis à
  chaque nouvelle source de fichiers de tiers.
