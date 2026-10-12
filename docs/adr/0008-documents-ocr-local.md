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
     service, qui porte aussi le modèle d'embeddings.
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
    tiers.
- **OCR par Gemini (vision).** Plus robuste sur les photos difficiles, mais il enverrait
  l'image entière au cloud, y compris les documents sensibles avant toute détection. Il
  consommerait aussi le quota gratuit. Il pourra devenir une option explicite par document
  non sensible.
- **PaddleOCR ou docTR.** Meilleurs sur certaines mises en page, mais ils demandent PyTorch
  ou Paddle (plusieurs Go) : disproportionné ici.
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
