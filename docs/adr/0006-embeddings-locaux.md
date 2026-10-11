# ADR 0006 — Embeddings locaux et recherche hybride

- Statut : acceptée
- Date : 2026-10-11

## Contexte

L'étape 3 demande une recherche « hybride et temporelle » (F07) : retrouver une note par son
sens et pas seulement par ses mots, répondre à « qu'ai-je décidé la semaine dernière ? », et
repérer les doublons et les contradictions de la mémoire. Les contraintes sont les suivantes :

- **Confidentialité.** Les vecteurs sont dérivés du contenu. Ils ne doivent pas quitter le
  serveur, et les calculer dans le cloud consommerait aussi le quota Gemini (ADR 0007).
- **Matériel cible.**
  - VM Proxmox de 8 vCPU sur un i7-6700 (4 cœurs / 8 threads, AVX2 sans VNNI).
  - 10 Go de RAM partagés avec PostgreSQL, l'API, le worker et le client web.
  - GTX 1050 Ti (Pascal, 4 Go).
  - Plusieurs To de disque.
- **Langue.** Les données sont surtout en français.

## Décision

1. **Modèle : `google/embeddinggemma-300m`.**
   - Export ONNX `onnx-community/embeddinggemma-300m-ONNX`, exécuté par **fastembed 0.9**
     (ONNX Runtime, CPU, sans PyTorch). Le dépôt n'est pas restreint, mais un jeton Hugging
     Face optionnel reste possible pour un modèle restreint (`lifeos_hf_token`).
   - 768 dimensions, préfixes de requête et de passage imposés par le modèle.
   - Le modèle est entraîné pour la troncature (Matryoshka), ce qui permet de passer plus tard
     à moins de dimensions.
   - Licence : conditions d'utilisation de Gemma (usage personnel compatible).
2. **Service interne `ml`.**
   - Le modèle est chargé une seule fois, pour l'API et le worker.
   - Le service est joignable uniquement sur le réseau Docker interne, sans route vers
     l'extérieur.
   - Le modèle est téléchargé une fois (`ml-download`, profil `setup`), dans un volume monté
     en lecture seule pour le service, qui fonctionne ensuite **hors ligne**
     (`HF_HUB_OFFLINE=1`).
   - Une inférence à la fois, en petits lots (8 passages), pour qu'une recherche n'attende pas
     longtemps derrière l'indexation.
   - Échec rapide si le modèle manque ; délai de 8 s pour une requête de recherche.
3. **Stockage : pgvector 0.8.7.**
   - Colonne `halfvec(768)` (moitié moins de place qu'un `vector`, perte négligeable) avec un
     index HNSW `halfvec_cosine_ops` (m = 16, ef_construction = 64).
   - Les filtres (propriétaire, espace, type, modèle) s'appliquent **après** le parcours de
     l'index. Chaque recherche élargit donc `hnsw.ef_search` (≥ 100) et active
     `hnsw.iterative_scan = relaxed_order`.
4. **Indexation en tâche de fond** (worker, toutes les 2 minutes, idempotente) :
   - **Objets indexés** :
     - faits actifs, souvenirs, journal, tâches, objectifs ;
     - messages d'au moins 15 caractères.
   - **Découpage** : segments d'au plus 1 200 caractères, 8 au maximum.
   - **Ré-indexation** : quand le contenu change (empreinte md5) ou quand l'objet change
     d'espace.
   - **Retrait du bon index** : les objets qui ne qualifient plus (fait retiré) sont retirés.
   - **Effacement** : la suppression d'un objet supprime ses vecteurs par **trigger**, quel
     que soit le chemin de suppression.
   - **Concurrence** : un verrou consultatif empêche deux indexations simultanées.
   - **Changement de modèle** : les anciens vecteurs, non comparables, sont supprimés.
5. **Recherche hybride.**
   - Le plein texte français (`lifeos_fr` : racinisation et accents ignorés) et la similarité
     vectorielle sont fusionnés par **Reciprocal Rank Fusion** (k = 60).
   - Les deux méthodes passent par les **mêmes filtres SQL**. Au-delà d'une distance cosinus
     de 0,65, un résultat vectoriel est jugé sans rapport.
   - Un analyseur déterministe d'expressions temporelles françaises (« hier », « la semaine
     dernière », « en mars », « il y a 3 jours »…) restreint la période.
   - Sans modèle disponible, la recherche se dégrade en plein texte, ce qui est signalé dans
     l'interface.
6. **Qualité de la mémoire** (F07), en local et sans appel cloud :
   - **Doublons** : distance < 0,08 entre deux faits du même type.
   - **Mises à jour ou contradictions possibles** : distance < 0,25, avec détection des
     négations françaises.
   - **Préférences anciennes** : non confirmées depuis 180 jours.
   - Ces seuils sont configurables (`LIFEOS_MEMORY_*`) et dépendent du modèle.
7. **GPU non utilisé pour les embeddings.**
   - `onnxruntime-gpu` ≥ 1.27 ne cible que CUDA 13, qui a abandonné Pascal. La branche de
     pilotes R580 est la dernière à prendre en charge cette carte.
   - Le CPU suffit pour une personne.
   - La carte reste disponible pour l'étape 4 : transcription `faster-whisper` en int8 via
     CTranslate2 (CUDA 12), ou un petit LLM local via Ollama (CUDA 12). Ces deux pistes sont à
     évaluer séparément.

## Mesures

Job CI `ml-model` (runner GitHub 4 vCPU), 2026-10-11, vrai modèle hors ligne, cache en lecture
seule :

| Mesure | Résultat |
|---|---|
| 5 questions françaises → bonne note en tête | 5/5 (distance 0,42–0,55 contre ≥ 0,80 pour les autres) |
| Doublons reformulés (« J'habite à Lyon » / « Je vis à Lyon ») | 0,02–0,03 |
| Négation (« J'aime le café » / « Je n'aime pas le café ») | 0,115 |
| Mise à jour (« J'habite à Lyon » / « … à Bordeaux ») | 0,21 |
| Sans rapport | 0,43 |
| Débit (passages courts, 4 threads) | 24 passages/s ; une requête : 32 ms |

Sur l'i7-6700, il faut s'attendre à un débit plus faible : à mesurer sur la VM avec
`LIFEOS_TEST_REAL_MODEL=1 uv run pytest -s tests_ml`. Un corpus personnel de quelques milliers
d'éléments s'indexe malgré tout en quelques minutes.

**Prérequis Proxmox** : type de CPU `host` (ou `x86-64-v3`). Le type par défaut
`x86-64-v2-AES` masque AVX2/FMA, et ONNX Runtime retombe alors sur des noyaux SSE nettement
plus lents. Vérification dans la VM : `grep -o -w 'avx2\|fma' /proc/cpuinfo | sort -u`.

## Conséquences

- Mémoire du service `ml` : environ 1,5–2,5 Go en pointe ; limite fixée à 3 Go dans Compose.
- Disque : environ 1,2 Go de modèle, plus environ 1,6 Ko par segment indexé.
- Changer de modèle oblige à réindexer tout le corpus (automatique) et à recalibrer les seuils
  (le test `tests_ml` affiche les distances).

## Alternatives écartées

- **`multilingual-e5-small`** : plus léger (384 dimensions), mais nettement moins bon en
  recherche française. Écarts mesurés sur les résultats MTEB : −8,8 nDCG sur MIRACL-fr et −15
  MAP sur AlloprofReranking. Reste un repli possible si la RAM manque.
- **`bge-m3`, `Qwen3-Embedding-0.6B`, `multilingual-e5-large`** : environ 2,2 à 2,4 Go et 15 à
  20 fois le calcul ; trop lourds pour cette VM.
- **Embeddings Gemini (API)** : le contenu partirait dans le cloud et consommerait le quota.
- **sentence-transformers / PyTorch** : plusieurs centaines de Mo de dépendances, et la prise
  en charge de Pascal disparaît (dernières roues CUDA 12.6 en 2.14).
