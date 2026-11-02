# ADR 0008 — Documents : originaux chiffrés, lecture et OCR en local

- Statut : acceptée
- Date : 2026-10-12

## Contexte

L'étape 4 ajoute les documents (F10), les tickets de caisse (UC02) et les pièces jointes du
chat (F01). Les contraintes sont les suivantes :

- **Confidentialité.** Pièces d'identité, ordonnances, relevés bancaires : le cahier des
  charges demande une « extraction locale préalable des informations sensibles » et une
  conservation des originaux « seulement si utile, autorisée et avec durée définie ».
- **Fichiers non fiables.** Un fichier importé peut être malformé ou piégé (bombe de
  décompression, PDF hostile, métadonnées GPS d'une photo).
- **Matériel.** Même VM que l'ADR 0006 : 4 cœurs, 10 Go de RAM partagés, pas de GPU utile
  pour l'OCR.
- **Langue.** Documents surtout en français : accents, tickets de caisse étroits.

## Décision

1. **Types acceptés par signature (liste blanche).**
   - Formats : PDF, JPEG, PNG, WebP, HEIC et texte UTF-8 ; l'audio est réservé à la dictée
     (ADR 0009).
   - Le type est reconnu à la signature du contenu, jamais au nom ni au type déclaré.
   - Refusés : HTML, SVG, XML, scripts, exécutables, archives et documents bureautiques.
   - La taille est limitée pendant la lecture du flux (20 Mio par défaut), et le contenu
     n'est jamais écrit en clair sur le disque.
2. **Originaux chiffrés, durée limitée.**
   - Chiffrement AES-256-GCM, avec l'identifiant du document en données associées : un
     fichier recopié sur un autre document ne se déchiffre pas.
   - Format `LOS1 | id de clé | nonce | chiffré`, écriture atomique.
   - La clé est un secret Docker (`lifeos_documents_key`), séparée des données et des
     sauvegardes.
   - L'original est purgé après 90 jours par défaut, sauf si le propriétaire choisit
     « conserver ». Le texte extrait et les métadonnées restent.
   - Une purge quotidienne supprime aussi les fichiers orphelins.
3. **Lecture dans le service `ml`, isolé.**
   - Le service `ml` est le même que celui des embeddings : sans accès sortant, en lecture
     seule, sans privilèges. Les fichiers non fiables y sont analysés, jamais dans l'API.
   - **PDF** : la couche texte est lue par PDFium (`pypdfium2`). Une page qui a moins de
     20 caractères est jugée scannée : elle est rendue à 300 dpi puis passée à l'OCR.
     Un PDF trop long (plus de 30 pages) est refusé, pas tronqué. Un PDF protégé par mot de
     passe est signalé comme tel.
   - **Images** : le décodeur est imposé par la signature. La taille est contrôlée sur
     l'en-tête avant tout décodage (50 Mpx au plus, garde contre les bombes de
     décompression). L'orientation EXIF est appliquée.
   - **Photos difficiles (tâche 29).** Une première lecture se fait telle quelle, exactement
     comme avant. Ensuite seulement :
     - si elle est mauvaise (confiance moyenne sous 0,75) et que l'image n'a pas d'orientation
       EXIF (capture d'écran, scan, téléphone qui ne l'écrit pas), la détection
       d'orientation de Tesseract (OSD) propose un quart de tour. L'image tournée est relue,
       et gardée seulement si elle se lit nettement mieux. L'image conservée est alors
       tournée elle aussi, par quarts de tour exacts uniquement ;
     - si les lignes penchent de 4° ou plus, ou si la lecture reste mauvaise, une seconde
       lecture se fait sur une copie nettoyée (Pillow et numpy) : éclairage égalisé
       (division par le fond estimé), contraste étiré, netteté renforcée si l'image est
       floue (variance du laplacien), puis redressement de l'inclinaison mesurée par
       profils de projection (±15°). Elle est gardée si sa confiance est nettement
       meilleure ; pour une page inclinée, si elle n'est pas nettement moins bonne.
       L'image conservée n'est jamais déformée ;
     - chaque passe supplémentaire demande deux fois le temps de la première encore
       disponible : elle ne fait jamais échouer en `too_slow` un document lisible à temps.
     - Ce qui a été fait (quart de tour, redressement, passe gardée) est renvoyé à l'API
       (`preprocessing`) et conservé dans `Document.analysis["reading"]`, sans contenu.
     - Les pages scannées des PDF suivent la même règle.
   - **Ré-encodage des images.** L'image est ré-encodée à partir des seuls pixels :
     EXIF, GPS, XMP, ICC et commentaires disparaissent. Le résultat (4 096 px au plus,
     HEIC converti en JPEG) **remplace** le fichier envoyé. Une image illisible ne peut pas
     être nettoyée : son original n'est pas conservé.
   - **OCR** : Tesseract 5 avec le modèle français, dans un sous-processus avec un budget
     de temps.
     - Mode « colonne » pour les tickets.
     - Les petites images sont agrandies avant lecture.
     - La confiance moyenne des mots est conservée, et l'interface invite à vérifier le
       texte quand elle est basse.
   - Une extraction à la fois, avec une file bornée : au-delà, `503 busy` et nouvel essai
     plus tard.
   - **Chaque extraction tourne dans un processus enfant à mémoire bornée** (1,25 Gio par
     défaut, `LIFEOS_EXTRACTION_MEMORY_MB`). Un fichier hostile, par exemple une image
     énorme décrite en quelques kilo-octets dans un PDF, échoue au lieu d'épuiser le
     service, qui porte aussi le modèle d'embeddings. Le confinement (groupe de
     processus, choix du noyau en cas de manque de mémoire, pas de fichier `core`) est
     décrit dans l'[ADR 0011](0011-antivirus-et-fichiers-hostiles.md).
     - Les images trop grandes d'un PDF sont refusées avant le rendu.
     - Les grandes photos JPEG sont décodées à échelle réduite.
   - Un document sur lequel le service plante trois fois de suite échoue définitivement
     (`extraction_crashed`) au lieu d'être réessayé sans fin. La règle vaut aussi pour les
     dictées (`transcription_crashed`).
   - Un document illisible échoue définitivement (`unreadable`, `too_many_pages`,
     `encrypted`, `too_slow`…) au lieu d'être réessayé sans fin.
   - Le propriétaire peut toujours corriger le texte ou le saisir lui-même.
4. **Traitement en tâche de fond, sans transaction ouverte.**
   - Le document est réclamé atomiquement, déchiffré, lu, puis relu sous verrou de ligne.
     Le résultat n'est écrit que s'il est toujours en cours de traitement.
   - Une suppression n'attend jamais une lecture.
   - Une tâche périodique rattrape les messages perdus et les nouvelles tentatives.
5. **Sensibilité détectée localement.**
   - Trois catégories : identité, santé, banque. Le titre est classé avec le texte.
   - Signaux pondérés et explicables :
     - signaux forts : bande MRZ, IBAN valide, numéro de carte (contrôle de Luhn), NIR ;
     - indices faibles qui s'additionnent pour le reste : une ordonnance qui ne dit pas
       « ordonnance » (RPPS, posologie, dosage), un compte rendu d'imagerie, un relevé
       « de vos comptes », un acte d'état civil, un avis d'impôt.
   - Les raisons affichées ne contiennent jamais les valeurs détectées.
   - **Un document sensible n'est jamais envoyé au cloud**, ni comme pièce jointe, ni dans
     le contexte d'un agent. La détection penche du côté de la prudence.
6. **Tickets de caisse lus localement.**
   - Le marchand, la date, le total TTC et la TVA sont lus avec la ligne d'origine de chaque
     valeur et une confiance globale.
   - L'analyse tolère les mots collés par l'OCR (« TOTALTTC »).
   - Rien n'est ajouté au budget sans validation (UC02).
7. **Pièces jointes du chat (F01).**
   - Le fichier devient un document, lu localement.
   - Seul son texte accompagne le message, et seulement pour ce message :
     - borné à 12 000 caractères par document, 3 documents au plus ;
     - présenté comme des **données**, jamais des instructions ;
     - masqué par la passerelle comme le reste.
   - Refus avant tout enregistrement si le document est sensible, en cours de lecture ou
     hors de l'espace, ou si l'agent n'a pas le droit `documents`.
   - Chaque envoi est audité.

## Alternatives écartées

- **OCRmyPDF.** Il produit un PDF/A avec couche texte, ce qui est utile pour archiver, mais
  ajoute Ghostscript, une surface d'attaque connue, et une dépendance lourde. PDFium suffit
  pour lire, et le texte est stocké à part.
- **Antivirus (ClamAV).**
  - Les signatures occupent 1 à 1,5 Go de RAM en permanence, soit le prix de la recherche
    sémantique sur cette VM.
  - Pour des fichiers déposés par leur seul propriétaire, le risque est porté par des
    parades plus directes :
    - liste blanche par signature ;
    - ré-encodage des images ;
    - analyse isolée dans `ml` ;
    - originaux servis en téléchargement avec `nosniff` et `CSP sandbox`, jamais affichés
      en ligne ni exécutés.
  - À reconsidérer avec les connecteurs e-mail (étape 5), dont les fichiers viennent de
    tiers : critères, décision et conception en cas d'import de fichiers de tiers dans
    l'[ADR 0011](0011-antivirus-et-fichiers-hostiles.md).
- **OCR par Gemini (vision).** Plus robuste sur les photos difficiles, mais il enverrait
  l'image entière au cloud, y compris les documents sensibles avant toute détection. Il
  consommerait aussi le quota gratuit. Il pourra devenir une option explicite par document
  non sensible.
- **PaddleOCR ou docTR.** Meilleurs sur certaines mises en page, mais ils demandent PyTorch
  ou Paddle (plusieurs Go) : disproportionné ici.
- **OpenCV pour le redressement de perspective (tâche 29).** Un détecteur de contour du
  ticket puis une transformation à 4 points corrigeraient les photos prises de biais, au
  prix d'une dépendance native de plus (`opencv-python-headless`, ≈ 60 Mo, un décodeur
  d'images de plus à tenir à l'écart des fichiers non fiables). Sur le corpus synthétique,
  Tesseract lit déjà un ticket dont le haut est 40 % plus étroit que le bas : écarté tant
  que `lifeos-ml bench` sur de vraies photos ne montre pas d'échecs dus à la perspective.
- **MinIO.** Un volume chiffré au niveau applicatif suffit pour un seul utilisateur (le
  cadrage dit « commencer simple »).

## Conséquences

- L'image `ml` embarque Tesseract avec le modèle français uniquement (environ 30 Mo) ;
  `lifeos-ml selftest` vérifie l'OCR sans réseau, et la CI le fait aussi.
- Sur un i7-6700, une photo de ticket se lit en moins d'une seconde, et une page A4 scannée
  en quelques secondes. Un PDF scanné de 30 pages peut atteindre la limite de temps : il
  échoue alors en `too_slow`, et le propriétaire peut relancer la lecture.
- Les sauvegardes contiennent les originaux **chiffrés** : sans `lifeos_documents_key`,
  conservée à part, ils sont illisibles.
- La qualité de l'OCR sur les photos de travers ou mal éclairées reste limitée (pas de
  redressement de perspective) : l'interface montre la confiance et permet la correction.
- **Tâche 29 : mesures.** `lifeos-ml bench` (voir [exploitation](../exploitation.md) §8)
  compare la lecture simple et la lecture améliorée sur un corpus annoté. Sur le corpus
  synthétique (`--synthetic`, 18 tickets : droit, inclinés de ±2, ±5 et ±10°, tournés de
  90, 180 et 270° sans EXIF, perspective, flou, éclairage inégal, JPEG très compressé),
  machine de développement à 4 cœurs :

  | | Lecture simple | Lecture améliorée |
  |---|---|---|
  | Champs justes (total, date, marchand) | 57,4 % | 92,6 % |
  | Total juste | 55,6 % | 94,4 % |
  | CER | 39,5 % | 4,9 % |
  | Confiance moyenne | 0,58 | 0,89 |
  | Temps total (18 fichiers) | 6,5 s | 14,0 s (1,3 s au plus par fichier) |
  | Pic mémoire de l'enfant | 143 Mo | 167 Mo |

  Les tickets droits et nets se lisent à l'identique (même texte, aucune passe de plus). Un
  ticket tourné ou incliné de 7 à 10° passait de illisible à entièrement lu ; à 5° avec un
  éclairage inégal, le total était manqué et est retrouvé. Restent manqués : un flou fort
  (rayon 3,5 px, le masque de netteté ne suffit pas) et un marchand sous éclairage très
  inégal. Pour une photo de 12 Mpx, la lecture améliorée coûte ≈ +0,25 s sur une image
  droite et ≈ +1,9 s sur une image inclinée (deux passes) ; pic de 324 à 405 Mo (323 à
  378 Mo en lecture simple).
  **Ces chiffres sont synthétiques** : les seuils (`RETRY_CONFIDENCE`, `SKEW_PREFER_DEG`,
  `OSD_MIN_CONFIDENCE`, `MIN_GAIN`) sont à confirmer sur un corpus de vraies photos du
  propriétaire, sur la VM i7-6700, et à reporter ici.
- L'image `ml` embarque aussi les données d'orientation de Tesseract
  (`tesseract-ocr-osd`, ≈ 10 Mo) ; numpy, déjà présent par fastembed, est une dépendance
  explicite de l'extra `ml`.
