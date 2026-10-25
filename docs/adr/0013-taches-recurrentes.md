# ADR 0013 — Tâches récurrentes : utiles, mais après les dépendances et sous une forme étroite

- Statut : acceptée, mise en œuvre reportée (tâche 22)
- Date : 2026-10-25

## Contexte

Le cahier des charges (F03) demande la création, la classification, les échéances, les
rappels, les dépendances et le suivi de résolution des tâches. Il ne parle pas de
récurrence. La tâche 22 a ajouté les dépendances (`task_dependencies`), l'état bloqué et
les étiquettes (`tasks.tags`). Elle demandait aussi de décider si les tâches récurrentes
valent la peine.

Ce qui pousse à les faire :

- un assistant personnel sert souvent pour des obligations qui reviennent : payer un loyer,
  prendre un traitement, sortir les poubelles, faire un bilan ;
- sans récurrence, le propriétaire recrée la tâche à la main ou l'oublie.

Ce qui pousse à attendre :

- **les fuseaux horaires.** « Tous les lundis à 8 h » est une heure locale. Ajouter
  7 × 24 h en UTC décale l'échéance d'une heure au changement d'heure. La synchronisation de
  l'agenda (tâche 14) a déjà montré ce piège ;
- **les liens avec le reste.** Il faut décider de ce que la tâche suivante reprend :
  dépendances, objectif, rappel, étiquettes. Une dépendance recopiée bloquerait chaque
  occurrence sur une tâche déjà terminée ;
- **le risque de doublons.** Un double clic, un rejeu ou une approbation rejouée ne doit
  jamais créer deux occurrences ;
- **le recouvrement avec l'agenda.** Les événements récurrents viennent déjà de Google
  (lecture) : la récurrence des tâches ne doit servir qu'aux choses à faire, pas aux
  rendez-vous.

## Décision

**Les tâches récurrentes valent la peine, mais elles ne font pas partie de la tâche 22.**
Elles viendront dans un lot à part, sous la forme étroite qui suit. Rien n'est stocké pour
elles aujourd'hui : ni colonne, ni table.

1. **Une règle courte**, sous-ensemble de RRULE (RFC 5545) : `FREQ=DAILY|WEEKLY|MONTHLY`
   et `INTERVAL=n` (1 à 12), plus `BYDAY` pour `WEEKLY`. Pas de `COUNT`, `UNTIL`
   ni d'exceptions. Pour arrêter la série, on supprime la règle ou on annule la tâche.
2. **Une occurrence à la fois.** La suivante est créée quand la tâche courante passe à
   `done`, ou à `cancelled` si l'on choisit « annuler cette fois seulement ». Rien n'est
   généré à l'avance : pas de tâche planifiée qui remplit la liste.
3. **Le calcul se fait dans le fuseau du propriétaire** (`users.timezone`), sur l'heure
   locale : l'échéance et le rappel gardent la même heure locale après un changement
   d'heure. Le 31 d'un mois plus court tombe sur le dernier jour de ce mois.
4. **Une seule occurrence suivante, même en cas de rejeu.** Elle porte
   `source_type = 'recurrence'` et `source_id` = l'identifiant de la tâche précédente. Un
   index unique sur `(source_type, source_id)` empêche toute création en double.
5. **Ce que la suivante reprend** : titre, notes, priorité, étiquettes, objectif et la
   règle. Elle ne reprend **pas** les dépendances : un lien vers une tâche terminée ne
   bloquerait rien et prêterait à confusion.
6. **Audit et effacement** comme pour une tâche créée à la main : l'audit garde des
   identifiants (`task.create`, `source: recurrence`), jamais de titre. Supprimer une
   occurrence ne supprime pas les autres.

## Conséquences

- Aujourd'hui, une tâche qui revient se recrée à la main. Le guide d'utilisation le dit.
- La tâche 22 ne prend aucun engagement de schéma. Le lot « récurrence » ajoutera une
  colonne `tasks.recurrence text null`, une contrainte de forme et l'index unique
  ci-dessus, dans une migration à lui.
- Les dépendances restent simples. Une occurrence n'hérite d'aucun lien, donc le contrôle
  des cycles (verrou par utilisateur, parcours récursif) ne change pas.

## Alternatives écartées

- **Générer toutes les occurrences d'avance** (par exemple 12 mois) : la liste se remplit de
  tâches futures, chaque modification de la règle doit les réécrire, et les supprimer laisse
  des traces dans les bilans.
- **RRULE complète** : trop de cas (`BYSETPOS`, exceptions, `UNTIL` en UTC) pour un usage
  personnel. Le jour où c'est utile, on la prendra d'une bibliothèque, sans l'écrire.
- **Réutiliser la récurrence de l'agenda Google** : elle ne vaut que pour les événements
  synchronisés, en lecture seule. Elle n'existe pas sans compte connecté.
