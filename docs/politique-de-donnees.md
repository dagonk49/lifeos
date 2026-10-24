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
| Comptes connectés (adresse, scopes accordés, statut) et consentements par espace | tant que le propriétaire ne supprime pas le compte ; consentements retirés gardés en historique | déconnecter efface les jetons tout de suite et garde le compte (`revoked`) ; « supprimer » l'efface avec ses données synchronisées ([ADR 0012](adr/0012-connecteurs-oauth.md)) |
| Jetons OAuth (accès, renouvellement) | tant que le compte est actif | scellés AES-256-GCM (`lifeos_connectors_key`) ; effacés à la déconnexion, quand le fournisseur refuse le renouvellement ou quand la clé ne les déchiffre plus |
| Données synchronisées (agenda, e-mails : tâches 14 à 18) | tant que la source existe | gardées à la déconnexion, sauf si le propriétaire choisit de les supprimer ; supprimées avec le compte (cascade) |
| Consentements OAuth en cours (`oauth_states`) | expiration ou usage + 1 jour (valables 10 minutes) | empreinte du `state` seulement, vérificateur PKCE scellé ; purge quotidienne avec événement d'audit |
| Sessions | expiration + 30 jours | purge quotidienne |
| Défis WebAuthn, jetons d'enrôlement | expiration + 1 jour | purge quotidienne |
| Clés d'idempotence | 24 heures | purge quotidienne |
| Journal d'audit | 1 an (**à valider**) | purge dédiée, seule exception au mode append-only |
| Journal des appels LLM (`llm_calls`) | 1 an (**à valider**) | ne contient aucun contenu |
| Logs applicatifs | 14 jours | rotation Docker ; aucun contenu personnel brut |
| Métriques Prometheus | 15 jours | rétention Prometheus ; compteurs, durées et âges seulement, aucune donnée personnelle ([exploitation.md §5](exploitation.md#5-supervision-et-alertes)) |
| Alertes (silences, journal des notifications d'Alertmanager) | 5 jours | rétention par défaut d'Alertmanager ; textes fixes des règles, aucune donnée personnelle |
| Journal des déploiements (`infra/state/deployments.tsv`) | tant que l'installation existe | dates, commits, étiquettes d'images, révisions du schéma, résultat ; aucune donnée personnelle |
| Rapports de recette (`infra/state/recette/`) | jusqu'à leur suppression à la main | durées, statuts, tailles et mémoire, sur des entrées synthétiques ([recette-serveur.md](recette-serveur.md)) |
| Sauvegardes (y compris celles d'avant une mise à jour, étiquetées `pre-update`) | 14 quotidiennes + 4 hebdomadaires (≈ 35 jours), copie hors site comprise | `restic forget --prune` sur le dépôt principal et sur la copie, chaque nuit ; originaux et configuration suivent leur sauvegarde de base ([exploitation.md §4](exploitation.md#4-sauvegardes-restic)) |

## 2. Effacement (droit à l'oubli)

- Supprimer un message, un fait, un souvenir ou une entrée de journal supprime la ligne, ses
  liens, ses embeddings et ses entrées de cache. L'audit ne conserve que l'identifiant de
  l'objet et l'action, **jamais le contenu** : il n'empêche donc pas l'effacement.
- Les faits extraits d'un message purgé restent ; leur provenance indique une source
  supprimée.
- **Sauvegardes** : elles ne sont pas réécrites. Une donnée supprimée disparaît des
  sauvegardes à la fin de leur rotation (**≈ 35 jours au plus**), dans le dépôt principal
  comme dans la copie hors site (même rétention, appliquée à chaque copie). En cas de
  restauration, les suppressions survenues après la date de la sauvegarde doivent être
  rejouées : le journal d'audit de l'ancienne base (identifiants d'objets supprimés) sert de
  liste de rejeu ([restauration.md](restauration.md) §3, étape 10). Si l'ancien serveur est
  perdu, ce journal l'est aussi : les suppressions des dernières heures sont à refaire de
  mémoire.
- **Originaux manquants après une restauration** : ils sont signalés par identifiant de
  document, jamais masqués. Le propriétaire peut les récupérer dans une sauvegarde plus
  ancienne, ou les déclarer perdus (`lifeos-admin reconcile-originals --yes`, audité
  `document.original_missing`) : le document et son texte restent, l'original n'est plus
  proposé.
- Export complet sur demande : JSON (mémoire, journal, tâches, documents : métadonnées et texte
  extrait, finances), CSV (tâches, finances), ICS (tâches datées). Les originaux chiffrés se
  téléchargent un par un depuis la page Documents.
- Les originaux des documents figurent, **chiffrés**, dans les sauvegardes : illisibles sans
  `lifeos_documents_key`, qui est conservée à part.
- **Comptes connectés** : l'export JSON liste les comptes (fournisseur, adresse, scopes,
  statut), les consentements par espace et les sources, jamais les jetons. Déconnecter
  révoque l'accès chez le fournisseur, efface les jetons et retire tous les consentements ;
  LifeOS demande à chaque fois s'il faut garder (par défaut) ou supprimer les données déjà
  synchronisées. Les jetons figurent, chiffrés, dans les sauvegardes : illisibles sans
  `lifeos_connectors_key`, conservée à part (la perdre oblige seulement à reconnecter les
  comptes).

## 3. Transmission au fournisseur LLM (Gemini)

Conditions cumulatives pour tout appel cloud (vérifiées par le Guardian, hors LLM) :

1. le fournisseur est configuré et n'est pas désactivé ;
2. l'espace concerné a le consentement cloud activé (`spaces.cloud_ai_enabled`) ;
3. l'appelant (utilisateur ou agent) a accès aux données incluses ;
4. l'assistant n'est pas en pause (arrêt d'urgence) ;
5. l'appel tient dans le budget mensuel de jetons et dans le plafond quotidien de requêtes du
   palier, marge déduite (ou, en facturation payante, dans les budgets en dollars). Sa part
   est réservée atomiquement avant l'envoi, appels en cours compris
   ([ADR 0010](adr/0010-reservation-des-quotas-llm.md)).

Contenu transmis :

- uniquement le **paquet de contexte minimal** construit pour la requête (message courant,
  derniers échanges de la conversation, souvenirs pertinents du même espace) ;
- après **masquage local** des données détectées, remplacées par une étiquette (`[IBAN]`,
  `[TÉLÉPHONE]`…) : adresses e-mail (y compris écrites « jean . dupont @ example . org »,
  « (at) », « arobase »), numéros de téléphone (`0…`, `+33`, `0033`, `+33 (0)`, numéros
  internationaux), IBAN (clé mod 97 vérifiée, minuscules acceptées), numéros de carte bancaire
  (contrôle de Luhn ; un SIRET annoncé comme tel n'en est pas un), numéros de sécurité
  sociale, mots de passe et codes annoncés (« mot de passe : … », « mot de passe hunter2 »,
  « code PIN 1234 »), et les numéros de passeport, de pièce d'identité, de titre de séjour,
  de permis et fiscaux **écrits juste après le nom du document** (ils n'ont pas de clé de
  contrôle : seuls, ils seraient confondus avec des références ordinaires) ;
- ces identifiants sont reconnus dans les formes des vrais documents : groupés par des
  espaces, des espaces insécables ou fines (typographie française), des points ou des
  tirets, et lus par OCR avec les confusions habituelles (`O` pour `0`, `l` ou `I` pour
  `1`). Seuls les identifiants sont remplacés : le reste du texte part tel qu'il est écrit ;
- jamais : identifiants, mots de passe, jetons, clés d'API, secrets.

**Contrôle avant l'envoi** : avant d'envoyer un message, la page demande au serveur ce qui
sera masqué et ce qui restera local (`POST /v1/chat/preview` : mêmes règles que l'envoi,
rien n'est enregistré ni transmis). Si un identifiant sera masqué ou si une pièce jointe
reste sur le serveur, un récapitulatif l'indique — par catégorie et par nombre, jamais les
valeurs — et le message attend la confirmation ; le propriétaire peut aussi retirer une
pièce jointe ou annuler. Sinon, le message part sans étape de plus. Le récapitulatif porte
sur ce que le propriétaire ajoute (son message et ses pièces jointes) ; le reste de la
requête (consignes, souvenirs, contexte d'un agent) est masqué de la même façon à l'envoi.

**Documents sensibles, jamais transmis, même masqués** : un document est classé sensible
**localement**, avant tout usage, sur son titre et son texte, s'il relève de l'une de ces
catégories :

- identité : pièce d'identité, passeport, titre de séjour, permis, état civil, bande MRZ,
  carte grise (certificat d'immatriculation), avis d'impôt, de taxe foncière, de taxe
  d'habitation et autres documents fiscaux ;
- santé : ordonnance, compte rendu, analyses, arrêt de travail, attestation de droits,
  décompte de mutuelle ou de remboursement de soins, numéro de sécurité sociale ;
- banque : RIB, IBAN valide, relevé de compte, numéro de carte, bulletin de paie, offre ou
  contrat de prêt.

Les mots-clés sont aussi reconnus lus par OCR (`0RD0NNANCE`, `CARTE NATI0NALE`) et les
identifiants dans toutes les formes ci-dessus. Un tel document n'est ni joint à un message,
ni lu par un agent. La détection penche du côté de la prudence (le mot « passeport » suffit,
même dans une note de voyage) ; ses raisons sont affichées sans les valeurs détectées.

**Garder en local** : le propriétaire peut aussi cocher « Toujours garder en local » sur
n'importe quel document (`documents.local_only`). Ce choix s'ajoute à la détection et n'est
jamais modifié par une nouvelle lecture, une correction du texte ou un nouveau classement.
Le propriétaire ne peut qu'**ajouter** de la protection : il ne peut pas lever une catégorie
détectée (corriger le texte relance la détection, c'est la seule voie), et décocher « Toujours
garder en local » est une opération à validation renforcée (passkey récente,
`document.allow_cloud`).

**Détection retestée** : une correction du texte (ou un nouveau titre) relance la détection
avec les règles en vigueur. Au moment de l'envoi, chaque pièce jointe est classée à nouveau
avec les règles en vigueur, en plus de sa catégorie enregistrée : un document lu avant une
amélioration de la détection est protégé tout de suite. Une tâche horaire
(`reclassify_documents`) reclasse ensuite les documents lus par une version antérieure des
règles (`analysis.sensitivity_version`) : elle **ajoute** les catégories trouvées, sans
jamais en retirer, et l'audite sans contenu (`document.reclassify`).

**Mesure sur ses propres documents** : `lifeos-admin sensitivity-check <dossier>` lit des
fichiers sur le serveur (texte ; PDF et images par le service `ml`), les classe et les
masque comme un envoi, et n'affiche que les noms de fichiers, les catégories, les raisons et
le nombre d'identifiants masqués par type — jamais une valeur ni une ligne de texte. Avec
`--expect manifest.json`, il liste les écarts (faux négatifs, faux positifs). Un faux
négatif trouvé sur un vrai document est ajouté au corpus de test sous forme de
**reproduction synthétique** (`backend/tests/fixtures/sensitivity`), jamais le document
lui-même.

**Pièces jointes du chat** : seul le **texte extrait localement** d'un document ni sensible
ni gardé en local est transmis, et seulement avec le message auquel il est joint. Il est limité à 12 000
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
ni un document sensible ou gardé en local.

## 4. Séparation des espaces

- Chaque donnée appartient à un espace (`personal` ou `professional`).
- Un agent n'accède à un espace et à une ressource que via une permission explicite
  (`agent_permissions`), limitée dans le temps et révocable.
- Les recherches et les paquets de contexte sont toujours filtrés par espace.
- Un compte connecté (agenda, e-mails) ne sert que dans les espaces et pour les usages que
  le propriétaire y a autorisés (`connector_grants`) ; un agent a besoin en plus de sa
  propre permission `calendar` ou `email`.

## 5. Journalisation

- Logs applicatifs structurés (JSON) : route, statut, durée, identifiant de requête. Jamais de
  corps de requête, de prompt, de réponse LLM, de pièce jointe, de jeton ni d'adresse e-mail.
  Les jetons OAuth ne passent jamais dans une URL. Le code d'autorisation y passe une fois,
  dans la redirection du fournisseur vers `/connexions/retour` (à usage unique, inutile sans
  le vérificateur PKCE, retiré de la barre d'adresse dès l'arrivée ; un reverse proxy peut
  l'enregistrer dans son journal d'accès, voir T30). Un filtre masque en plus toute
  valeur qui ressemble à un jeton (défense en profondeur, T31).
  Les services `ml` et `stt` ne journalisent que la forme d'un traitement (type, pages,
  durée, nombre de caractères), jamais le texte ni l'audio.
- Métriques et alertes : nombres et durées étiquetés par route, tâche, file, service ou type
  de document, jamais par utilisateur, titre ou contenu. Une notification (ntfy, e-mail,
  Telegram) ne contient que le titre, le résumé et l'action de l'alerte.
- Audit : qui (acteur), quoi (action), sur quoi (identifiants), résultat, espace.
