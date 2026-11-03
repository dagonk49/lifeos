# ADR 0015 — Notifications adaptatives : politique de livraison pure, regroupement des suggestions, permission explicite pour les alertes critiques

- Statut : acceptée
- Date : 2026-10-25

## Contexte

Le cahier des charges (F09) demande : « Critique : immédiat si risque réel et permission de
contourner le silence. Important : créneau opportun ; suggestions : regroupement dans
briefing/bilan. Plages de tranquillité, anti-doublons, fréquence par catégorie et
désactivation ciblée. »

Avant les tâches 20 et 21, `next_delivery` ne connaissait que les plages de tranquillité et
laissait toujours passer `critical`, sans aucune permission. Chaque suggestion devenait sa
propre notification ; aucune fréquence, aucun créneau, aucune désactivation. L'envoi push
(tâche 19) doit pouvoir reprendre simplement les notifications dues.

Choix du propriétaire :

- contournement des plages par les alertes critiques **désactivé par défaut** ; l'activer
  est une opération renforcée (passkey récente), auditée et révocable ;
- suggestions regroupées une fois par jour, à **18:00** heure locale, **3 au plus par jour** ;
- rappels (`important`) immédiats, mais respectant les plages de tranquillité.

## Décision

1. **Une seule règle, calculée à la création** : `notifications/policy.py::next_delivery`
   est une fonction pure (aucun accès à la base) qui donne `deliver_after` à partir d'une
   `DeliveryPolicy` (fuseau, plages, permission critique, règles par catégorie) et des
   comptes par jour local. Les heures locales sont converties en UTC ; une heure répétée au
   changement d'heure d'automne prend sa première occurrence (la seconde si la première est
   passée), une heure sautée au printemps est décalée de la durée du saut.
2. **Catégories** :
   - `critical` : immédiate seulement avec `users.critical_bypass_quiet_hours` ; sinon
     retenue jusqu'à la fin des plages comme les autres. Jamais plafonnée, jamais
     désactivable, jamais limitée à des créneaux. Action Guardian
     `notification.critical_bypass` (niveau renforcé, interdite aux agents) ; le retrait est
     immédiat ; accord et retrait audités ;
   - `important` : hors plages, dans un des créneaux de livraison s'il y en a (aucun par
     défaut) ;
   - `suggestion` : à la prochaine heure de regroupement hors plages.
3. **Maximum par jour** : par catégorie et par jour local, compté sous un verrou
   consultatif par utilisateur (`NOTIFY_LOCK_CLASS`, comme le quota LLM). Le surplus est
   **reporté** au jour suivant qui a de la place, jamais perdu ni fusionné ailleurs.
4. **Regroupement** : les suggestions dues ne sont jamais montrées seules. La tâche
   `build_suggestion_digests` (chaque minute) crée par propriétaire une notification de
   synthèse (`source_type = 'digest'`) et rattache les suggestions (`digest_id`), en une
   transaction, lignes verrouillées `SKIP LOCKED` : idempotent. La synthèse ne cite aucun
   titre : un objet effacé ne doit pas y survivre ; elle disparaît avec sa dernière
   suggestion.
5. **Désactivation ciblée** : une catégorie désactivée ne crée rien (`notify` répond faux,
   la tâche de rappels compte `suppressed`) ; le rappel est tout de même marqué envoyé, donc
   la réactivation ne rejoue pas l'arriéré. Les notifications en attente de la catégorie
   passent à `dismissed`.
6. **Reprogrammation** : changer les préférences, le fuseau, les plages ou la permission
   critique recalcule `deliver_after` des notifications pas encore livrées (les plus
   anciennes d'abord), sans toucher aux clés d'anti-doublon.
7. **Contrat pour l'envoi push (tâche 19)** : `notifications.service.due(now)` donne les
   conditions d'une notification à envoyer (livrée, pas dans une synthèse, pas une
   suggestion en attente) ; l'envoyeur ne voit donc que les synthèses, jamais les
   suggestions une à une. Il ajoutera son propre marqueur d'envoi.

## Conséquences

- Un rappel peut arriver plus tard qu'à l'heure demandée si le propriétaire fixe des
  créneaux ou un maximum : les valeurs par défaut restent permissives (aucun créneau, aucun
  maximum pour `important`).
- Si le worker est arrêté, les suggestions dues restent invisibles jusqu'à son retour (elles
  ne sont pas perdues).
- Le surplus de suggestions s'accumule si une source en produit durablement plus que le
  maximum ; aujourd'hui, seuls les bilans en produisent (deux espaces : au plus 2 par jour,
  4 le dimanche).
- Les plages de LifeOS ne sont pas le « Ne pas déranger » du téléphone : un push, même
  critique, ne le traverse pas.
- Aucun producteur n'émet encore de notification `critical` : définir ce qu'est un « risque
  réel » reste à faire avant d'en ajouter un.
