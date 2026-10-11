# ADR 0004 — Tâches asynchrones : Dramatiq + Redis, planification APScheduler

- Statut : acceptée
- Date : 2026-10-11

## Contexte

Besoins : purges planifiées (conversations à 90 jours, sessions, défis WebAuthn), rappels,
bilans planifiés, imports, calcul d'embeddings ; reprise sur erreur ; gestion des fuseaux
horaires et des exécutions manquées. L'architecture proposait Celery (ou Dramatiq).

## Décision

- **Dramatiq** avec courtier **Redis** pour l'exécution (processus `worker`) : API plus simple
  que Celery, retries avec backoff intégrés, suffisant pour un utilisateur.
- **APScheduler** dans un processus `scheduler` dédié : il ne fait qu'**enfiler** des messages
  Dramatiq selon des déclencheurs cron exprimés dans le fuseau `Europe/Paris`
  (`coalesce=True`, tolérance de retard configurée pour rattraper une exécution manquée une
  seule fois).
- **Redis n'est jamais source de vérité** : l'état (tâches, rappels envoyés, purges) est en
  PostgreSQL. Une perte de Redis ne fait perdre que des messages en attente, rejoués au
  prochain déclenchement.
- **Chaque tâche est idempotente** : elle relit l'état en base et peut être rejouée sans double
  écriture (clés de déduplication, conditions `WHERE ... IS NULL`).
- Chaque purge écrit un événement d'audit avec le nombre d'éléments traités (preuve
  d'exécution).

## Conséquences

- Deux processus supplémentaires (`worker`, `scheduler`) mais une seule image Docker.
- Un seul `scheduler` doit tourner (pas de verrou distribué au MVP).

## Alternatives écartées

- Celery + Celery Beat : plus lourd, configuration plus complexe pour un gain nul ici.
- File de tâches en PostgreSQL : viable, mais Redis est de toute façon prévu pour le cache.
