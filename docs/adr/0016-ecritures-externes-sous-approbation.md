# ADR 0016 — Écritures chez un fournisseur : approbation liée aux paramètres, réservation unique, rapprochement

- Statut : acceptée
- Date : 2026-10-28
- Tâche : 16 (modifier l'agenda sous approbation) ; mécanisme repris par la tâche 18 (envoi
  d'e-mails)

## Contexte

Jusqu'ici, une action approuvée s'exécutait dans la transaction de la requête du
propriétaire et n'écrivait que dans la base de LifeOS : en cas d'échec, tout revenait en
arrière. Créer, déplacer ou annuler un événement Google Agenda (et bientôt envoyer un
e-mail) écrit **ailleurs**. Un retour arrière de la base n'annule rien chez le fournisseur.

Ce que le moteur d'approbation ne garantissait pas :

1. **Les paramètres exécutés sont ceux approuvés.** `policy.evaluate()` vérifiait l'action,
   l'espace et le statut de l'approbation, jamais ses paramètres.
2. **Un rejeu ne réécrit pas.** Si l'appel au fournisseur réussit puis que la requête
   échoue (délai dépassé, processus tué), la transaction revient en arrière, l'approbation
   redevient « en attente » et une nouvelle approbation renvoie l'écriture : un deuxième
   événement.
3. **L'aperçu décrit encore la réalité.** Entre l'aperçu et l'accord, l'événement peut
   changer chez le fournisseur (quelqu'un l'a déplacé, a ajouté un participant).
4. **« Aperçu exact + passkey »** pour un événement partagé : le niveau `reinforced` de la
   politique ne savait exprimer que « passkey récente », sans approbation.

## Décision

### 1. L'approbation est liée à ses paramètres

- `approvals.params_sha256` : SHA-256 du JSON canonique des paramètres (clés triées, sans
  espaces). Calculé à la demande et à chaque correction par le propriétaire.
- `execute()` recalcule l'empreinte des paramètres qu'il va exécuter : différente (ou
  absente), refus `403 approval_params_mismatch`, audité.
- Une action peut se déclarer **non modifiable** à l'approbation (`editable=False`) : son
  aperçu vient de l'état du fournisseur, une correction ne le décrirait plus
  (`422 approval_not_editable`).

### 2. Une proposition périmée ne peut plus être approuvée

- Nouveau statut `invalidated`, avec la raison dans `error` (`event_changed`, `event_gone`,
  `superseded`).
- `approvals.target` nomme l'objet visé (`calendar_event:<id>`). Une nouvelle proposition
  sur le même objet remplace la précédente ; la même proposition encore en attente est
  renvoyée telle quelle (double clic, requête rejouée).
- La synchronisation de l'agenda invalide les propositions d'un événement dont l'etag a
  changé ou qui a disparu (dans sa propre transaction, après celle de la page : jamais de
  verrou sur `calendar_events` tenu en attendant une approbation).

### 3. Approbation renforcée

`ActionSpec.approval_level = "reinforced"` : l'action reste au niveau « validation
obligatoire », mais l'approbation créée est `reinforced`. Pour l'approuver, il faut une
connexion par passkey de moins de 10 minutes, et `evaluate()` refuse l'exécution si
l'approbation n'est pas de ce niveau. `calendar.update_shared_event` l'utilise.

### 4. Opérations externes : réservation, clé d'idempotence, rapprochement

Une action enregistrée avec un `ExternalAction` (`perform`, `reconcile`) passe par
`guardian/operations.run_external` :

1. **Réservation** : une ligne `external_operations` par approbation (`approval_id`
   unique), insérée et validée dans une transaction à part **avant** l'appel au
   fournisseur.
2. **Clé d'idempotence** dérivée de l'approbation (`lo` + base32hex de son UUID, 28
   caractères `0-9a-v`) : c'est l'identifiant de l'événement créé. Google refuse un
   deuxième événement avec le même identifiant (`409`) : LifeOS relit alors l'événement et
   compte l'écriture comme faite. Déplacer et annuler sont conditionnés à l'etag de
   l'aperçu (`If-Match`) ; une annulation d'un événement déjà supprimé (`404`, `410`) est
   faite.
3. **Réservation existante** (requête morte après l'écriture, nouvelle approbation après un
   crash) : jamais d'écriture à l'aveugle. `reconcile` demande d'abord au fournisseur si
   l'écriture a eu lieu ; si oui, elle est enregistrée ; sinon elle est renvoyée avec la
   même clé, trois tentatives au plus.
4. **Issue inconnue** (délai dépassé après l'envoi, erreur `5xx`) : l'approbation passe
   `failed` avec `outcome_unknown`, l'opération `unknown`. La tâche
   `reconcile_external_operations` (file `connectors`, toutes les 5 minutes, et mise en file
   aussitôt) vérifie chez le fournisseur. L'écriture a eu lieu : l'approbation devient
   `executed`. Elle n'a pas eu lieu : `not_applied`, rien n'est renvoyé.
5. Les résultats sont enregistrés hors de la transaction de la requête : ils survivent à
   son retour arrière.

Pas de clé étrangère de `external_operations` vers `approvals` : la réservation est
insérée pendant que la requête qui décide tient le verrou de la ligne d'approbation (un
contrôle de clé étrangère attendrait ce verrou). Les opérations partent avec leur
approbation (purge à 90 jours).

### 5. Niveaux retenus pour l'agenda (choix par défaut du propriétaire)

- **Toute** écriture dans l'agenda demande une approbation, même un événement privé du
  propriétaire.
- Un événement **partagé** (autres participants, événement organisé par quelqu'un
  d'autre, agenda qui n'appartient pas au compte) passe par
  `calendar.update_shared_event` : approbation renforcée (passkey). Le classement est
  refait juste avant l'écriture, sur l'état du fournisseur.
- Les participants ne sont prévenus (`sendUpdates=all`) que si le propriétaire l'a coché ;
  sinon `sendUpdates=none`, toujours explicite.
- Les agents proposent (`requested_by = "agent:<nom>"`, permission `calendar` en écriture),
  le propriétaire décide.

## Conséquences

- « Aucune modification sensible sans l'accord requis » et « un rejeu ne crée pas un second
  événement » sont prouvés par `backend/tests/test_calendar_writes.py` : double
  approbation, approbations simultanées, crash après l'écriture, issue inconnue, paramètres
  modifiés, événement changé entre l'aperçu et l'accord.
- Les adresses des participants figurent dans les paramètres et l'aperçu de l'approbation
  (purgés avec elle), jamais dans `calendar_events`, les journaux ou l'audit.
- La tâche 18 réutilise `run_external` : la clé d'idempotence d'un envoi, et un
  `reconcile` qui cherche le message envoyé.
- Limites : un événement récurrent ne se modifie qu'occurrence par occurrence ; déplacer un
  événement organisé par quelqu'un d'autre est refusé (`event_not_organizer`), l'annuler le
  retire de l'agenda du propriétaire. Un événement créé pendant un passage de
  synchronisation déjà commencé peut disparaître de l'agenda local jusqu'au passage suivant.
- Les règles de Google (identifiant fourni par le client, réponse `409`, `If-Match` sur
  `events.patch` et `events.delete`) sont celles de sa documentation actuelle : à revérifier
  par un essai réel avant la mise en service.

## Alternatives écartées

- **Écrire hors de toute transaction sans réservation** : un crash après l'écriture laisse
  l'approbation en attente, et un nouvel accord réécrit.
- **Boîte d'envoi (outbox) traitée par le worker** : l'accord du propriétaire ne donnerait
  plus de résultat immédiat, et l'aperçu se périmerait pendant l'attente. La réservation
  détachée donne la même garantie avec une réponse immédiate.
- **Niveau `reinforced` seul pour les événements partagés** : il ne demande qu'une passkey
  récente, sans aperçu ni accord ponctuel.
