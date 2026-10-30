# ADR 0005 — Déploiement Docker Compose, Traefik sans socket Docker

- Statut : acceptée ; précisée le 2026-10-30 (réseaux réels, certificat obtenu hors de Traefik,
  pas de MinIO)
- Date : 2026-10-11

## Contexte

Cible : une VM Debian dédiée sur Proxmox (architecture §3). Seul le reverse proxy doit être
exposé ; PostgreSQL, Redis, MinIO et consoles d'administration ne doivent jamais l'être
(MinIO n'a finalement pas été retenu : les originaux sont dans un volume chiffré, ADR 0008).

## Décision

- **Docker Compose** (`infra/compose.yaml`), une image backend unique pour `api`, `worker`,
  `scheduler` et `migrate` (job ponctuel lancé avant l'API).
- **Deux réseaux** : `edge` (Traefik ↔ api/web) et `internal` (`internal: true`, sans accès
  sortant) pour PostgreSQL et Redis. Le worker et l'API sont sur les deux réseaux uniquement
  parce qu'ils doivent joindre le fournisseur LLM.
  - Précision (2026-10-30) : le worker est en fait sur `internal` et `egress` (accès sortant
    sans être joignable par Traefik) ; `egress` sert aussi aux téléchargements ponctuels des
    modèles, et le profil `monitoring` ajoute un réseau `monitoring`
    ([exploitation.md §3](../exploitation.md#3-ce-qui-est-exposé)).
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

- Mise à jour par `infra/scripts/update.sh` : images étiquetées par commit
  (`LIFEOS_IMAGE_TAG`), celles des versions précédentes gardées sur la machine (retour arrière
  sans reconstruction), sauvegarde préalable, migrations appliquées par le service `migrate`
  avant le démarrage de l'API, contrôle de santé puis retour arrière automatique en cas
  d'échec ([exploitation.md §6](../exploitation.md#6-mise-à-jour-et-retour-arrière)).
- Le certificat TLS est fourni à Traefik sous forme de fichiers (`infra/traefik/certs/`).
  Précision (2026-10-30) : aucun client ACME n'est configuré dans Traefik ; le certificat est
  obtenu et renouvelé par un outil externe (par exemple `lego`, challenge DNS), ou par le
  reverse proxy existant en mode `--behind-proxy`
  ([exploitation.md §2](../exploitation.md#2-domaine-et-certificat-tls)).

## Alternatives écartées

- Kubernetes (k3s) : surdimensionné pour une VM personnelle.
- Labels Docker pour Traefik : nécessite le socket Docker.
