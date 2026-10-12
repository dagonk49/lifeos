# Politique de données

Principe : les données restent sur le serveur personnel ; le cloud ne reçoit que des extraits
minimaux, filtrés localement, pour un espace dont le propriétaire a donné son consentement.
Les valeurs marquées **à valider** sont des propositions par défaut.

## 1. Durées de conservation

| Catégorie | Durée | Mécanisme |
|---|---|---|
| Messages de conversation (bruts) | **90 jours**, sauf conservation explicite | `purge_after` ; purge quotidienne avec événement d'audit (nombre supprimé) |
| Enregistrements de dictée (audio) | **jusqu'à la transcription** (au plus 24 heures) | chiffrés comme les documents ; supprimés dès que le texte existe ou que l'enregistrement est refusé ([ADR 0009](adr/0009-voix-locale.md)) |
| Dictées (texte transcrit, en attente de relecture) | 24 heures | effacées par le client une fois utilisées, sinon par une purge horaire ; le texte gardé vit là où le propriétaire l'a enregistré (journal, message) |
| Faits, souvenirs, journal, tâches | tant que le propriétaire ne les supprime pas | souvenirs `protected` jamais purgés automatiquement |
| Embeddings | durée de l'objet source | calculés localement (jamais transmis) ; supprimés avec l'objet par trigger, retirés si le fait est retiré |
| Bilans (faits + lecture) | tant que le propriétaire ne les supprime pas | régénérables à la demande |
| Paires « garder les deux » (qualité mémoire) | durée des faits concernés | supprimées avec les faits |
| Documents : fichier original | **90 jours** par défaut, ou sans limite si le propriétaire choisit « conserver » ; une image illisible n'est pas conservée (elle n'a pas pu être nettoyée de ses métadonnées) | chiffré AES-256-GCM ; `original_until` ; purge quotidienne et suppression des fichiers orphelins ([ADR 0008](adr/0008-documents-ocr-local.md)) |
| Documents : texte extrait, sensibilité, lecture d'un ticket | tant que le propriétaire ne supprime pas le document | supprimer le document efface aussi le fichier et les vecteurs |
| Opérations financières, catégories, règles apprises | tant que le propriétaire ne les supprime pas | montants en décimal exact ; aucune connexion bancaire |
| Sessions | expiration + 30 jours | purge quotidienne |
| Défis WebAuthn, jetons d'enrôlement | expiration + 1 jour | purge quotidienne |
| Clés d'idempotence | 24 heures | purge quotidienne |
| Journal d'audit | 1 an (**à valider**) | purge dédiée, seule exception au mode append-only |
| Journal des appels LLM (`llm_calls`) | 1 an (**à valider**) | ne contient aucun contenu |
| Logs applicatifs | 14 jours | rotation Docker ; aucun contenu personnel brut |
| Métriques Prometheus | 15 jours | rétention Prometheus |
| Sauvegardes | 14 quotidiennes + 4 hebdomadaires (≈ 35 jours) | `restic forget --prune` |

## 2. Effacement (droit à l'oubli)

- Supprimer un message, un fait, un souvenir ou une entrée de journal supprime la ligne, ses
  liens, ses embeddings et ses entrées de cache. L'audit ne conserve que l'identifiant de
  l'objet et l'action, **jamais le contenu** : il n'empêche donc pas l'effacement.
- Les faits extraits d'un message purgé restent ; leur provenance indique une source
  supprimée.
- **Sauvegardes** : elles ne sont pas réécrites. Une donnée supprimée disparaît des
  sauvegardes à la fin de leur rotation (**≈ 35 jours au plus**). En cas de restauration, les
  suppressions survenues après la date de la sauvegarde doivent être rejouées : le journal
  d'audit (identifiants d'objets supprimés) sert de liste de rejeu.
- Export complet sur demande : JSON (mémoire, journal, tâches, documents : métadonnées et texte
  extrait, finances), CSV (tâches, finances), ICS (tâches datées). Les originaux chiffrés se
  téléchargent un par un depuis la page Documents.
- Les originaux des documents figurent, **chiffrés**, dans les sauvegardes : illisibles sans
  `lifeos_documents_key`, qui est conservée à part.

## 3. Transmission au fournisseur LLM (Gemini)

Conditions cumulatives pour tout appel cloud (vérifiées par le Guardian, hors LLM) :

1. le fournisseur est configuré et n'est pas désactivé ;
2. l'espace concerné a le consentement cloud activé (`spaces.cloud_ai_enabled`) ;
3. l'appelant (utilisateur ou agent) a accès aux données incluses ;
4. l'assistant n'est pas en pause (arrêt d'urgence) ;
5. le budget mensuel de jetons et le plafond quotidien de requêtes du palier ne sont pas
   atteints (ou, en facturation payante, les budgets en dollars).

Contenu transmis :

- uniquement le **paquet de contexte minimal** construit pour la requête (message courant,
  derniers échanges de la conversation, souvenirs pertinents du même espace) ;
- après **masquage local** des données détectées : adresses e-mail, numéros de téléphone,
  IBAN, numéros de carte bancaire (contrôle de Luhn), numéros de sécurité sociale ;
- jamais : identifiants, mots de passe, jetons, clés d'API, secrets.

**Documents sensibles, jamais transmis, même masqués** : un document est classé sensible
**localement**, avant tout usage, s'il relève de l'une de ces catégories :

- identité : pièce d'identité, passeport, titre de séjour, bande MRZ ;
- santé : ordonnance, compte rendu, analyses, numéro de sécurité sociale ;
- banque : RIB, IBAN valide, relevé de compte, numéro de carte.

Un tel document n'est ni joint à un message, ni lu par un agent. La détection penche du côté
de la prudence ; ses raisons sont affichées sans les valeurs détectées.

**Pièces jointes du chat** : seul le **texte extrait localement** d'un document non sensible
est transmis, et seulement avec le message auquel il est joint. Il est limité à 12 000
caractères par document et à 3 documents, présenté comme des données (jamais des
instructions), puis masqué comme le reste. Chaque envoi est audité (`document.attach`). Les
fichiers eux-mêmes (images, PDF) ne quittent jamais le serveur.

Fournisseur : Gemini Developer API, projet **sans compte de facturation** par défaut (voir
[ADR 0007](adr/0007-couts-gemini-niveau-gratuit.md) et le [guide](guide-gemini.md)). En
France (EEE), les conditions de Google appliquent au quota gratuit les règles des services
payants : pas d'utilisation des contenus pour améliorer les produits Google ; conservation
limitée pour la détection d'abus (55 jours annoncés pour les services payants, **à
revérifier** sur les conditions en vigueur).

Ce qui n'est **jamais** envoyé au cloud :

- les embeddings et la recherche par le sens (calculés localement) ;
- la détection des doublons et contradictions de la mémoire ;
- les faits des bilans et la progression des objectifs ;
- les fichiers importés, leur OCR et la détection de sensibilité (service `ml`) ;
- la lecture des tickets de caisse ;
- la voix : la transcription a lieu sur le serveur (service `stt`) et la lecture à voix
  haute sur l'appareil, avec ses seules voix locales ;
- les calculs de budget.

Consolidation d'une conversation : uniquement à la demande, seuls les messages **du
propriétaire** sont transmis (jamais les réponses de l'assistant), et les faits proposés ne
sont enregistrés qu'après approbation. Agents spécialisés : ils ne lisent que les ressources
pour lesquelles le propriétaire leur a donné un accès explicite (90 jours, révocable).
L'agent Finances voit les opérations et budgets de l'espace ; avec l'accès « documents », il
voit aussi ce qui a été lu sur les tickets (marchand, date, total), jamais le texte extrait
ni un document sensible.

## 4. Séparation des espaces

- Chaque donnée appartient à un espace (`personal` ou `professional`).
- Un agent n'accède à un espace et à une ressource que via une permission explicite
  (`agent_permissions`), limitée dans le temps et révocable.
- Les recherches et les paquets de contexte sont toujours filtrés par espace.

## 5. Journalisation

- Logs applicatifs structurés (JSON) : route, statut, durée, identifiant de requête. Jamais de
  corps de requête, de prompt, de réponse LLM, de pièce jointe, de jeton ni d'adresse e-mail.
  Les services `ml` et `stt` ne journalisent que la forme d'un traitement (type, pages,
  durée, nombre de caractères), jamais le texte ni l'audio.
- Audit : qui (acteur), quoi (action), sur quoi (identifiants), résultat, espace.
