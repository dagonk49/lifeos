# ADR 0010 — Plafonds IA fiables : réservation atomique des quotas avant l'appel

- Statut : acceptée (amende les conséquences de l'[ADR 0007](0007-couts-gemini-niveau-gratuit.md))
- Date : 2026-10-18

## Contexte

L'ADR 0007 fixe des plafonds côté LifeOS : requêtes par jour et par palier (facturation
gratuite), budgets en dollars (facturation payante), budget mensuel de jetons. La passerelle
les vérifiait avant l'appel, mais la ligne `llm_calls` n'était écrite qu'**après** la
réponse du fournisseur.

- **Fenêtre de course.** Entre la vérification et l'écriture, il s'écoule toute la latence du
  fournisseur : jusqu'au délai d'attente (60 s), deux fois si l'adaptateur réessaie avec un
  autre niveau de raisonnement. Pendant ce temps, tous les appels simultanés passent la même
  vérification.
- **Appelants concurrents.** L'API (assistant, agents, analyse du journal, consolidation,
  bilans à la demande) et le worker (bilans automatiques, palier `deep`) appellent la
  passerelle en même temps.
- **Budgets comparés au passé seulement.** Les budgets de jetons et de dollars ne
  comparaient que la consommation passée (`>=`) : un appel pouvait dépasser la limite de
  toute sa propre taille.
- **Ce que coûte un dépassement.**
  - En facturation gratuite, il ne coûte rien (Google répond 429), mais l'assistant devient
    indisponible jusqu'au lendemain 9 h.
  - En facturation payante, il coûte de l'argent, et les données de facturation de Google
    ont environ 10 minutes de retard.
- **Deux requêtes pour un appel.** L'adaptateur Gemini peut envoyer deux requêtes pour un
  seul appel : un refus 400 du niveau `minimal`, puis une nouvelle tentative en `low`. Cela
  arrive une fois par processus et par palier.

L'ADR 0007 acceptait ce risque (« deux appels simultanés peuvent dépasser un plafond d'une
ou deux requêtes »). Le propriétaire demande que les plafonds soient fiables en concurrence.

## Décision

1. **Réserver avant l'appel, dans une transaction courte et à part.**
   - Après les contrôles qui ne demandent aucun compteur (arrêt d'urgence, politique,
     consentement de l'espace, masquage local), la passerelle ouvre **sa propre
     transaction**.
   - Elle prend un verrou consultatif PostgreSQL par utilisateur, à deux clés (classe
     propre aux quotas, puis empreinte de l'utilisateur) :
     `pg_advisory_xact_lock(<classe>, hashtext('<utilisateur>'))`. Ces verrous ne peuvent
     pas entrer en collision avec les verrous à une clé utilisés ailleurs (les bilans en
     tiennent un pendant leur appel au LLM).
   - Elle recompte la consommation, réservations en cours comprises. Elle la compare aux
     limites, **en ajoutant l'estimation de l'appel**.
   - Si l'appel tient, elle insère une ligne `llm_calls` avec `outcome = 'reserved'`, puis
     valide la transaction.
   - Le verrou est libéré à la validation : il n'est **jamais tenu pendant l'appel au
     fournisseur**, et la transaction de la requête HTTP n'intervient pas.
   - Un refus garde les mêmes erreurs qu'avant : `429 llm_daily_quota_reached` (extensions
     `tier` et `limit`) ou `429 llm_budget_exceeded`. Il laisse une ligne `budget_exceeded`
     et rien n'est envoyé.
2. **Taille d'une réservation.**
   - **Requêtes** : une requête sur le compteur du jour du palier.
   - **Jetons d'entrée** : estimés de façon prudente sur le texte masqué, à raison d'un jeton
     pour 3 caractères (Gemini compte plutôt 4 caractères par jeton en français). On ajoute
     8 jetons par message et, pour une réponse structurée, la taille du schéma JSON envoyé.
   - **Jetons de sortie** :
     - le `max_output_tokens` de la requête quand il est fixé (les bilans fixent 2 000). Il
       inclut le raisonnement : c'est une vraie borne ;
     - sinon, une estimation par palier : `LIFEOS_LLM_RESERVE_OUTPUT_TOKENS_FAST` (2 048 par
       défaut) et `LIFEOS_LLM_RESERVE_OUTPUT_TOKENS_DEEP` (8 192).
   - **Coût** (facturation payante) : la table de prix appliquée à cette estimation.
   - **Pourquoi ces valeurs par défaut.**
     - Palier `fast` (assistant, analyses, agents, raisonnement `minimal`) : une réponse
       compte typiquement quelques centaines de jetons. 2 048 couvre une longue réponse et
       son raisonnement, soit trois à cinq fois une réponse ordinaire. Une réservation
       immobilise 0,2 % du budget mensuel par défaut (1 million de jetons). En payant, elle
       vaut environ 0,5 centime, soit 1 % du budget quotidien par défaut.
     - Palier `deep` (raisonnement `low`, longues synthèses) : ses seuls appelants
       aujourd'hui, les bilans, fixent déjà 2 000. 8 192 est un plafond prudent pour un futur
       appel `deep` sans limite (environ 3 centimes en payant).
3. **Marges conservatrices.**
   - **Facturation gratuite.**
     - `LIFEOS_LLM_QUOTA_MARGIN_REQUESTS` (2 par défaut) est retiré de chaque plafond
       quotidien. La limite appliquée par défaut est donc de 398 requêtes en `fast` et de
       13 en `deep`.
     - Cette marge couvre la double requête du changement de niveau de raisonnement et les
       appels dont l'issue s'est perdue.
   - **Facturation payante.**
     - `LIFEOS_LLM_BUDGET_MARGIN_RATIO` (0,05 par défaut) : 5 % des budgets quotidien et
       mensuel en dollars sont gardés en réserve.
     - Cette réserve couvre une sortie plus longue que l'estimation (appels sans
       `max_output_tokens`) et le retard de facturation de Google.
   - **Budget mensuel de jetons.** Pas de marge supplémentaire : c'est un seuil choisi par le
     propriétaire, pas une limite externe. La réservation de l'appel le rend déjà prudent.
4. **Rapprochement après l'appel.**
   - La ligne réservée est mise à jour avec la consommation réelle : jetons, coût, modèle,
     durée, `settled_at`. Elle passe à `ok` ou `error`.
   - **Erreur du fournisseur** : les jetons facturés d'une réponse inutilisable sont
     enregistrés.
   - **Échec inattendu ou annulation** : la ligne passe à `error` (`llm_error`). Le
     rapprochement est protégé contre l'annulation (`asyncio.shield`).
   - L'estimation reste dans `reserved_tokens` et `reserved_cost_usd`, pour comparaison. Elle
     ne compte que tant que la ligne est `reserved`.
5. **Réservations abandonnées.**
   - Si le processus meurt entre la réservation et le rapprochement (redémarrage, arrêt
     mémoire), la ligne reste `reserved`.
   - Plus vieille que `LIFEOS_LLM_RESERVATION_TTL_SECONDS` (600 s par défaut, jamais moins
     de deux délais d'attente du fournisseur plus une minute), elle passe à `abandoned`.
     **Ses jetons et son coût sont libérés ; sa requête continue de compter** : elle a pu
     atteindre Google (décision du propriétaire, prudente).
   - La libération a lieu avant chaque nouvelle réservation, dans la même transaction, et
     toutes les 5 minutes dans le worker (`release_llm_reservations`).
   - Un appel qui se termine après avoir été libéré enregistre quand même sa consommation
     réelle.
6. **Compteurs et affichage.**
   - Requêtes du jour : lignes `ok`, `error`, `reserved` et `abandoned`.
   - Jetons et coûts : consommation réelle des appels terminés, plus l'estimation des
     réservations en cours.
   - Le tableau de bord et **Réglages → Assistant IA** affichent les limites appliquées
     (marges déduites). Les réglages montrent aussi les appels en cours (`in_flight`), la
     marge, et les états « En cours » et « Abandonné » dans les derniers appels.

## Alternatives écartées

- **Garder le contrôle sans réservation (ADR 0007).** Simple, mais le plafond n'est pas
  fiable dès que l'API et le worker appellent en même temps.
- **Verrouiller pendant tout l'appel.** Fiable, mais chaque appel attendrait la fin du
  précédent (jusqu'à 60 s) : l'assistant serait sérialisé.
- **Compteur dans Redis (`INCR` atomique).** Rapide, mais il dédoublerait la source de
  vérité : `llm_calls` reste nécessaire pour la mesure et l'affichage, et les deux devraient
  être resynchronisés après chaque incident.
- **`SELECT … FOR UPDATE` sur la ligne de l'utilisateur.** Équivalent au verrou
  consultatif, mais il verrouille une ligne métier (`users`) lue partout. Le verrou
  consultatif ne bloque que les réservations.
- **Libérer entièrement une réservation abandonnée.** Moins prudent : la requête a pu
  atteindre Google et consommer son quota.

## Conséquences

- **Deux appels simultanés près d'un plafond ne peuvent plus le dépasser** : le second voit
  la réservation du premier. Des tests couvrent les appels simultanés, l'appel en cours,
  l'annulation, la libération et le rapprochement tardif.
- Chaque appel ajoute deux transactions courtes (réservation, rapprochement). L'attente du
  verrou se limite à la réservation elle-même (quelques millisecondes), jamais à la latence
  du fournisseur.
- La réservation peut refuser un appel qui aurait tenu : les derniers milliers de jetons du
  budget mensuel, ou les dernières requêtes du jour (marge). Les réglages ci-dessus
  permettent d'ajuster.
- Le nombre de requêtes est garanti. Pour les jetons et les coûts, un appel sans
  `max_output_tokens` peut consommer plus que son estimation : les marges absorbent l'écart,
  sans garantie stricte pour un appel isolé.
- Une réservation faite avant minuit (heure du Pacifique) et rapprochée après compte pour le
  jour de la réservation.
- Compteurs et verrou sont par utilisateur. C'est équivalent au quota de Google, qui est par
  projet, tant que LifeOS n'a qu'un propriétaire. Un second utilisateur sur la même clé
  demanderait une clé de verrou et des compteurs globaux.
- Un quota du jour épuisé chez Google plus tôt que prévu reste traité comme avant
  (`llm_quota_exceeded`, sans frais).
- La colonne `llm_calls.correlation_id` (sans clé étrangère) est ajoutée par la même
  migration (0007) : elle reliera plus tard un appel à l'exécution qui l'a demandé.
