# ADR 0005 — Déploiement Docker Compose, Traefik sans socket Docker

- Statut : acceptée
- Date : 2026-10-11

## Contexte

Cible : une VM Debian dédiée sur Proxmox (architecture §3). Seul le reverse proxy doit être
exposé ; PostgreSQL, Redis, MinIO et consoles d'administration ne doivent jamais l'être.

## Décision

- **Docker Compose** (`infra/compose.yaml`), une image backend unique pour `api`, `worker`,
  `scheduler` et `migrate` (job ponctuel lancé avant l'API).
- **Deux réseaux** : `edge` (Traefik ↔ api/web) et `internal` (`internal: true`, sans accès
  sortant) pour PostgreSQL et Redis. Le worker et l'API sont sur les deux réseaux uniquement
  parce qu'ils doivent joindre le fournisseur LLM.
- **Traefik avec le fournisseur de configuration par fichier**, sans monter
  `/var/run/docker.sock` (le socket donne l'équivalent d'un accès root à l'hôte).
  Routage : `/v1` → API, le reste → client web ; `/metrics` n'est pas routé.
- **Secrets Docker** (fichiers dans `infra/secrets/`, ignorés par Git) lus via `*_FILE` ou le
  répertoire `/run/secrets`.
- **PostgreSQL** : image `pgvector/pgvector`, rôle superutilisateur réservé à l'initialisation
  et aux sauvegardes, rôle applicatif `lifeos` non superutilisateur propriétaire de la base.
- **Sauvegardes** par `restic` (chiffrées, dédupliquées, copie hors machine) — voir
  [exploitation.md](../exploitation.md).

## Conséquences

- Mise à jour = `docker compose pull/build` puis `up -d` ; migrations appliquées par le service
  `migrate` avant le démarrage de l'API.
- Le certificat TLS est fourni à Traefik (fichiers ou ACME DNS) : à configurer selon le domaine
  choisi.

## Alternatives écartées

- Kubernetes (k3s) : surdimensionné pour une VM personnelle.
- Labels Docker pour Traefik : nécessite le socket Docker.
