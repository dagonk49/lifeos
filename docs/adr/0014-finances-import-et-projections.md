# ADR 0014 — Finances : import CSV en aperçu côté serveur, clé d'import, projections locales explicables

- Statut : acceptée (tâches 25 et 26)
- Date : 2026-10-25

## Contexte

Le cahier des charges (F04) demande l'« import des opérations via source autorisée ou
saisie manuelle », les « dépenses récurrentes, objectifs d'épargne et prévisions
explicitement incertaines », et (§9) que « chaque recommandation financière importante
montre les hypothèses et données utilisées » et que le budget se calcule sans le cloud.
L'architecture (§10) recommande de « commencer par CSV/OFX […] sans conserver les
identifiants bancaires ». Une connexion bancaire (agrégateur agréé, contrat, frais) reste un
projet séparé.

Contraintes retenues :

- « Terminé quand » de la tâche 25 : réimporter le même fichier ne crée aucun doublon, et
  **rien n'est écrit avant que le propriétaire valide** ;
- « Terminé quand » de la tâche 26 : une projection se recalcule après une correction, et
  les montants connus se distinguent des montants estimés ;
- les exports bancaires français : séparateur `;`, virgule décimale, dates `jj/mm/aaaa`,
  encodage UTF-8 ou Windows-1252, quelques lignes de préambule (compte, solde) avant
  l'en-tête, débit et crédit dans une colonne signée ou deux colonnes.

## Décisions

### 1. Le fichier est lu côté serveur, dans un aperçu à durée limitée

Le navigateur envoie le fichier par les envois habituels (`read_upload` : limite stricte,
en mémoire, jamais sur le disque). Le serveur le décode **strictement** (UTF-8, sinon
Windows-1252 ; octets nuls et balisage refusés), écarte le préambule, et range les lignes
dans `finance_import_rows` sous un import `preview` qui expire après 24 h. La
correspondance des colonnes se choisit ensuite (`PUT …/mapping`) et chaque ligne est relue :
statut, raison, montant exact, doublon. Rien n'est inséré dans `transactions` ni compté dans
les règles avant `POST …/commit`.

Écarté : **lire le CSV dans le navigateur** et n'envoyer que les opérations. Plus simple pour
la vie privée (pas d'aperçu en base), mais la détection des doublons (clés déjà importées,
opérations saisies à la main) et le calcul exact des totaux demanderaient de toute façon des
appels au serveur, avec deux analyseurs à garder identiques (TypeScript et Python). Le
serveur reste la seule source de vérité ; l'aperçu, qui contient des libellés bancaires, est
effacé à la validation, à l'abandon ou après 24 h (purge horaire), et peut se trouver dans
une sauvegarde de cette fenêtre.

La validation est une **étape explicite** du propriétaire (bouton « Importer N
opérations »), et non une approbation du gardien : c'est le propriétaire lui-même qui agit.
Les agents ne peuvent ni importer ni annuler (`finance.import` et `finance.import.undo` sans
niveau agent).

### 2. Une clé d'import par opération, unique par espace

`transactions.import_key` = SHA-256 de `v1 | compte | date | sens | montant | devise |
libellé normalisé | référence bancaire, ou rang de l'opération identique dans la journée`.
L'index unique partiel `(space_id, import_key)` et l'insertion `ON CONFLICT DO NOTHING`
garantissent l'absence de doublon même pour deux validations simultanées (le verrou
`FOR UPDATE` sur l'import sérialise en plus une même validation rejouée).

- Le **rang** distingue deux cafés identiques le même jour, et reste le même quand un relevé
  qui chevauche le précédent couvre de nouveau toute la journée.
- Le **compte** (`account_label`, facultatif) distingue deux comptes importés dans le même
  espace.
- Faux négatifs possibles : une banque qui change le format de ses libellés entre deux
  exports. Faux positifs : deux opérations réellement identiques d'une journée coupée en
  deux exports. Une opération de même montant saisie à la main ou scannée à ±3 jours est
  signalée « doublon possible », **décochée** par défaut.
- Une opération importée puis supprimée à la main revient si le fichier est réimporté (sa
  clé a disparu) : comportement documenté, à revoir si le propriétaire le souhaite.

### 3. Annuler un import supprime ses opérations encore présentes

`POST …/undo {"confirm": true}` supprime en une fois les opérations de l'import, y compris
celles corrigées depuis (leur nombre est conservé dans `edited_count` et annoncé avant la
confirmation, qui nomme le fichier). L'import reste dans l'historique (`undone`). Niveau
**automatique** avec confirmation nommée plutôt que renforcé : seules les opérations que cet
import a ajoutées disparaissent, et un nouvel import du fichier les recrée. L'effacement est
audité (`finance.import.undo`) et rejoué après une restauration (`replay_erasures`).

### 4. Projections : calcul local, pur, jamais stocké, chaque montant étiqueté

`finance/projection.py` est une fonction pure en `Decimal`. Chaque lecture de
`GET /v1/finance/projection` relit les opérations, les échéances et les objectifs : une
correction se voit aussitôt, et aucune projection périmée ne peut être servie. Pour le mois
en cours puis les N mois suivants :

- **connu** : opérations enregistrées ;
- **prévu** : échéance déclarée à montant fixe (`finance_recurring`) ;
- **estimé** : échéance à montant variable (avec sa fourchette), ou **moyenne des derniers
  mois complets** par catégorie, hors opérations rattachées à une échéance (pas de double
  compte), avec une fourchette du mois le plus bas au mois le plus haut ; élargie de 25 % et
  signalée quand l'historique compte moins de 3 mois. Pour le mois en cours, l'estimé est ce
  qu'il reste de la moyenne après ce qui est déjà dépensé.

Les hypothèses (fenêtre de la moyenne, aucune inflation, pas de solde de départ faute de
connexion bancaire, une seule devise sans conversion, premier versement d'épargne le mois
suivant) et les avertissements sont renvoyés avec les chiffres, affichés dans l'interface et
transmis à l'agent Finances. Le LLM ne calcule rien.

Méthode écartée : régression ou saisonnalité. Avec quelques mois d'historique d'un seul
foyer, elles donneraient une précision trompeuse ; la moyenne et le min/max sont
vérifiables à la main.

### 5. Un objectif d'épargne est un objectif mesurable en euros

`goals.currency` et `goals.monthly_contribution` plutôt qu'une entité financière à part :
les points d'étape existants restent le montant épargné **connu**, la progression et les
jalons sont réutilisés, et la projection estime la date d'atteinte (versement déclaré, sinon
solde moyen prévu, avec une fourchette).

## Conséquences

- Migrations 0013 (`finance_imports`, `finance_import_rows`, `transactions.import_id` /
  `import_key`) et 0014 (`finance_recurring`, `transactions.recurring_id`, colonnes
  d'épargne des objectifs), toutes deux marquées `Downgrade: lossy`.
- Le `transactions.csv` de l'archive LifeOS est reconnu par la suggestion de colonnes
  (`signed_amount`, `label`, `id` comme référence).
- Restent ouverts : OFX, connexion bancaire, conversion des devises, solde de départ déclaré,
  suggestions d'échéances à partir des opérations répétées.
