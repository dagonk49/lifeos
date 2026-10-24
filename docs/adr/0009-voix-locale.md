# ADR 0009 — Voix : dictée transcrite en local, lecture par les voix de l'appareil

- Statut : acceptée
- Date : 2026-10-12

## Contexte

Le journal oral (UC01) et la conversation (F01, « dictée et lecture vocale si activées »)
demandent de transformer la parole en texte et, à l'inverse, de lire les réponses à voix
haute. Les contraintes sont les suivantes :

- une voix est une donnée biométrique et intime ;
- le quota Gemini gratuit doit être réservé au raisonnement (ADR 0007) ;
- la VM a 4 cœurs et 10 Go de RAM partagés, plus une GTX 1050 Ti (Pascal, 4 Go,
  sm_61) qui ne gère que CUDA 12 et qu'aucun autre service n'utilise.

## Décision

1. **Transcription locale par faster-whisper.**
   - Versions figées ensemble : faster-whisper 1.2.1, CTranslate2 4.8.2 et PyAV 18.1.0
     (PyAV 19 casse faster-whisper 1.2.1).
   - **Modèle : Whisper large-v3-turbo**, quantifié en int8. C'est le meilleur compromis
     qualité / vitesse en français à cette taille : environ 1,6 Go sur disque et 1,6 à
     1,9 Go en RAM une fois chargé.
   - **CPU** : environ 0,5 à 0,8 × le temps réel sur 4 cœurs AVX2.
   - **GPU** : sur Pascal, poids int8 et calcul en float32 (`int8_float32`), car ces cartes
     n'ont pas de float16 rapide. CTranslate2 4.8 contient encore les noyaux sm_61 et sa
     propre convolution CUDA, donc cuBLAS suffit, sans cuDNN.
   - En mode `auto`, le GPU est utilisé s'il est visible, sinon (ou s'il échoue) le CPU,
     sans intervention.
   - **Réglages de transcription** :
     - français imposé ;
     - détection d'activité vocale : silences de 700 ms, marge de 300 ms ;
     - chaque segment décodé seul (`condition_on_previous_text=False`), pour qu'une
       hallucination ne se propage pas ;
     - recherche en faisceau de 5.
   - **Nettoyage du texte** : les phrases fantômes connues de Whisper en français
     (génériques de sous-titres « Amara.org », « Sous-titrage… », « Merci d'avoir
     regardé ») et les répétitions en boucle sont retirées.
2. **Service interne `stt`, séparé de `ml`.**
   - Même isolement que `ml` : aucune route sortante, lecture seule, sans privilèges.
   - Le modèle est chargé à la **première dictée** et déchargé après 10 minutes sans
     dictée, pour rendre sa mémoire au reste.
   - Une transcription à la fois.
   - Le décodage audio (FFmpeg via PyAV) des fichiers non fiables a lieu dans ce
     conteneur, dans un processus enfant confiné, avec le démultiplexeur imposé par le
     type détecté et une liste courte de codecs
     ([ADR 0011](0011-antivirus-et-fichiers-hostiles.md)).
   - Variante GPU : image `stt-gpu` et `compose.gpu.yaml` (réservation du périphérique
     NVIDIA).
3. **Une dictée est transitoire.**
   - L'enregistrement est stocké chiffré comme un document (ADR 0008), puis transcrit en
     tâche de fond.
   - Un verrou consultatif garantit une transcription à la fois, sur un seul thread du
     worker.
   - L'enregistrement est **supprimé dès que le texte existe**, ou dès qu'il est refusé
     (trop long, illisible).
   - Le texte est rendu au client, qui le présente au propriétaire pour relecture et
     correction avant tout enregistrement :
     - dans le journal, puis « Extraire tâches et faits » avec approbation (UC01) ;
     - dans le chat, comme brouillon de message.
   - La dictée est ensuite effacée. Une dictée inutilisée l'est après 24 heures.
   - Les dictées n'apparaissent ni dans les documents, ni dans la recherche, ni dans
     l'index sémantique.
   - Durée maximale : 15 minutes (`LIFEOS_VOICE_MAX_SECONDS`).
4. **Enregistrement dans le navigateur.**
   - MediaRecorder, dans cet ordre de préférence : Opus/WebM (Chrome, Edge, Firefox),
     Opus/Ogg, puis AAC/MP4 (Safari).
   - Le serveur vérifie le type à la signature.
5. **Lecture à voix haute par l'appareil.**
   - Synthèse vocale du navigateur (`speechSynthesis`), avec **uniquement des voix
     locales** (`localService`). Certaines voix « en ligne » de Chrome enverraient le texte
     à un tiers.
   - Sans voix française locale, le bouton « Écouter » n'apparaît pas.

## Alternatives écartées

- **Transcription par Gemini (audio).** Elle enverrait la voix au cloud et consommerait le
  quota gratuit.
- **whisper.cpp.** Comparable sur CPU, mais l'intégration Python et le support CUDA de
  Pascal sont moins directs que CTranslate2.
- **Parakeet-TDT-0.6B v3 (onnx-asr).** Très rapide sur CPU et multilingue ; à comparer
  plus tard sur des enregistrements réels en français, avant tout changement de modèle.
- **Piper (synthèse vocale serveur).** De bonne qualité hors ligne, mais un service de plus
  et une voix à télécharger. Les voix locales des téléphones et des ordinateurs suffisent
  pour commencer. Piper reste l'option si aucune voix locale n'est disponible.

## Conséquences

- La CI ne télécharge pas le vrai modèle. Elle teste toute la chaîne hors ligne avec un
  Whisper minuscule à poids aléatoires : formats des navigateurs décodés, détection
  d'activité vocale, décodage CTranslate2, contrat HTTP. L'image `stt` est aussi
  démarrée sans réseau.
- Il faut télécharger une fois le modèle (`stt-download`, environ 1,6 Go). Le passage du
  GPU à la VM (VT-d/IOMMU) et le NVIDIA Container Toolkit sont décrits dans
  [exploitation.md](../exploitation.md).
- Sur CPU, une dictée de 5 minutes demande 2 à 4 minutes de transcription : l'interface
  affiche l'attente, et la page peut rester ouverte.
