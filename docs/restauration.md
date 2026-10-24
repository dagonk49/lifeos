# Restauration complète (plan de reprise)

Ce document décrit comment remettre LifeOS en service sur une **machine vierge** à partir des
sauvegardes : après la perte de la VM, de son disque ou du serveur Proxmox, et lors de
l'**exercice de restauration** à faire au moins une fois par an. La mise en place des
sauvegardes elles-mêmes est dans [exploitation.md §4](exploitation.md#4-sauvegardes-restic).

La restauration est réussie quand :

- le propriétaire **se connecte** (passkey existante, ou lien d'enrôlement) ;
- un **original restauré s'ouvre** (il est déchiffré avec la clé conservée à part) ;
- les **tâches, les finances et la recherche** sont cohérentes avec l'état d'avant.

## 1. Ce qu'il faut avoir, hors du serveur

| Élément | Où | Sans lui |
|---|---|---|
| Fiche de récupération (`infra/scripts/export-keys.sh`) | gestionnaire de mots de passe | — |
| … dont la clé des documents (`lifeos_documents_key`) | sur la fiche | les originaux sont perdus, sauvegardes comprises |
| … dont la clé des comptes connectés (`lifeos_connectors_key`), facultative | sur la fiche | les comptes connectés (Google) sont à reconnecter ; rien d'autre n'est perdu |
| … dont le mot de passe restic (et celui de la copie hors site) | sur la fiche | les sauvegardes sont illisibles |
| … dont les réglages des sauvegardes (`/etc/lifeos/backup.env` : dépôts, identifiants B2/S3, alertes) | sur la fiche | pas d'accès au dépôt |
| Clé SSH du dépôt (dépôt `sftp:`) | gestionnaire de mots de passe | pas d'accès au dépôt |
| Un appareil avec une passkey LifeOS (téléphone), ou l'adresse e-mail du propriétaire | — | connexion par lien d'enrôlement (§3, étape 8) |

Ce que contiennent les sauvegardes, et ce qu'elles ne contiennent pas :

| Dans les sauvegardes (chiffrées par restic) | Hors des sauvegardes |
|---|---|
| la base de données (dump PostgreSQL cohérent) | la clé des documents (seule son empreinte est notée) |
| les originaux des documents, déjà chiffrés (AES-256-GCM) | les mots de passe de la base (recréés : le dump ne contient aucun rôle) |
| `infra/.env` (domaine, RP ID WebAuthn, surcouches ; aucun secret par construction) et `traefik/dynamic/tls.yml` | les certificats TLS et leurs clés privées (`infra/traefik/certs/`) |
| | la clé des comptes connectés (les jetons, chiffrés, sont dans la base) et le secret du client Google (à recopier depuis la console Google Cloud) |
| | les modèles locaux (≈ 3 Go, retéléchargés) et la clé Gemini (à recréer dans AI Studio) |

## 2. Une sauvegarde = une exécution, une paire cohérente

Chaque nuit, `backup.sh` produit **une exécution** : trois instantanés restic qui portent la
même étiquette `run:<date UTC>`.

1. `documents` : l'archive des originaux chiffrés, prise **en premier** ;
2. `db` : le dump de la base ;
3. `config` : `infra/.env` et `tls.yml`.

Pris dans cet ordre, un document supprimé ou purgé pendant la sauvegarde laisse au pire un
fichier en trop dans l'archive (retiré par le ménage quotidien après une restauration), jamais
un document sans original. Un document envoyé pendant la sauvegarde est signalé (« newer than
the files ») et figure dans la sauvegarde suivante. L'instantané `db` porte aussi la version de
LifeOS (`version:`), la révision du schéma (`rev:`) et l'empreinte de la clé des documents
(`dockey:`) : la restauration vérifie qu'elle a le bon code et la bonne clé **avant** de
remplacer quoi que ce soit.

Les scripts choisissent toujours une paire : l'instantané `db` demandé (`latest`, un
identifiant d'exécution comme `20261019T023012Z`, ou un identifiant d'instantané) et
l'instantané `documents` de la même exécution. Une exécution incomplète est refusée. Les
sauvegardes antérieures à ces étiquettes sont appariées par l'heure, avec un avertissement.

```bash
restic snapshots --host lifeos --tag db      # une ligne par exécution, avec ses étiquettes
```

Après la restauration, `lifeos-admin verify-data` compare la base et les fichiers : chaque
original référencé doit être présent, se déchiffrer avec la clé installée et correspondre à
l'empreinte SHA-256 enregistrée. Un original absent est **listé par identifiant de document**
et le code de sortie vaut 2 : le problème n'est jamais masqué (voir §4).

## 3. Restauration sur une VM vierge, pas à pas

Durée : environ 30 à 45 minutes, dont la construction des images. Toutes les commandes se
lancent sur la nouvelle VM, depuis `/opt/lifeos` ; celles qui lisent `/etc/lifeos` (réglages
et mot de passe restic) passent par `sudo`.

### Étape 1 : la VM, Docker, restic et jq

Préparer la VM et installer Docker comme pour une installation neuve
([installation.md](installation.md) §1 et §2). Puis restic ≥ 0.17 (le binaire officiel : les
versions de Debian sont trop anciennes) et jq :

```bash
sudo apt-get install -y jq bzip2 git curl
curl -fsSLO https://github.com/restic/restic/releases/download/v0.18.1/restic_0.18.1_linux_amd64.bz2
echo "680838f19d67151adba227e1570cdd8af12c19cf1735783ed1ba928bc41f363d  restic_0.18.1_linux_amd64.bz2" | sha256sum -c -
bunzip2 restic_0.18.1_linux_amd64.bz2 && sudo install -m 755 restic_0.18.1_linux_amd64 /usr/local/bin/restic
```

### Étape 2 : l'accès au dépôt

Recopier depuis la fiche de récupération :

```bash
sudo install -d -m 700 /etc/lifeos
sudoedit /etc/lifeos/backup.env                 # les lignes « 3. Backup settings » de la fiche
sudoedit /etc/lifeos/restic-password            # le mot de passe restic (une ligne)
sudo chmod 600 /etc/lifeos/backup.env /etc/lifeos/restic-password
# Dépôt sftp: : remettre la clé SSH dans /root/.ssh (mode 600).
sudo bash -c 'set -a; . /etc/lifeos/backup.env; restic snapshots --host lifeos --tag db'
```

La dernière commande liste les exécutions : noter celle à restaurer (par défaut la plus
récente) et sa version (étiquette `version:`). Si le dépôt principal est perdu avec le
serveur, utiliser la copie hors site : `--offsite` aux étapes 5 ou 6.

### Étape 3 : le code de la même version

```bash
sudo git clone <url-du-dépôt> /opt/lifeos && sudo chown -R "$USER" /opt/lifeos
cd /opt/lifeos && git checkout <version>        # l'étiquette version: de l'exécution
```

Un code plus récent convient aussi (les migrations s'appliquent après la restauration) ; un
code plus ancien que la sauvegarde est refusé.

### Étape 4 : la clé des documents, AVANT toute installation

`install.sh` crée une clé neuve si aucune n'existe : les originaux restaurés seraient alors
indéchiffrables. Déposer la clé de la fiche dans un fichier temporaire :

```bash
sudo bash -c 'umask 077 && cat > /root/documents-key'   # coller la clé, Entrée, puis Ctrl-D
```

Clé des comptes connectés, **vraie reprise seulement** (facultatif) : la déposer elle aussi
avant l'installation, qui sinon en crée une neuve. Sans elle, les comptes Google seront
simplement à reconnecter. Pour un **exercice**, ne pas la déposer : la VM d'exercice ne doit
pas pouvoir utiliser les comptes du propriétaire.

```bash
sudo bash -c 'umask 077 && cat > /root/connectors-key'  # coller la clé, Entrée, puis Ctrl-D
sudo install -m 644 /root/connectors-key infra/secrets/lifeos_connectors_key
sudo shred -u /root/connectors-key
```

### Étape 5 : restaurer en une commande (recommandé)

```bash
sudo infra/scripts/restore-drill.sh --domain lifeos.mondomaine.fr --key-file /root/documents-key --yes
```

Le script vérifie l'empreinte de la clé, installe la clé, reprend `infra/.env` de la
sauvegarde, lance `install.sh --no-models`, puis `restore.sh` (base, originaux, migrations,
`verify-data`). Il affiche les durées et une ligne pour le journal des exercices (§5).
Options : `--run <exécution>`, `--offsite`, `--behind-proxy`, `--models` (télécharge les
modèles), `--enroll-email moi@exemple.fr` (lien de connexion), `--keep-llm` (garde
l'assistant actif : à réserver à une vraie reprise, voir §5).
Le secret du client Google n'étant ni dans les sauvegardes ni sur la fiche, le script
désactive les comptes connectés (`LIFEOS_CONNECTORS_PROVIDER=disabled`). Pour une vraie
reprise, recopier ce secret dans `infra/secrets/lifeos_google_client_secret`, remettre
`LIFEOS_CONNECTORS_PROVIDER=google` dans `infra/.env`, puis `docker compose up -d`
([guide-google.md](guide-google.md) §4).

Passer ensuite à l'étape 7.

### Étape 6 : ou bien, à la main

```bash
sudo install -d -m 700 infra/secrets
sudo install -m 644 /root/documents-key infra/secrets/lifeos_documents_key
sudo infra/scripts/install.sh --domain lifeos.mondomaine.fr --no-models --yes   # + --behind-proxy
sudo infra/scripts/restore.sh latest --yes      # ou l'identifiant d'exécution ; --offsite
```

Sans sauvegarde `config` (exécutions antérieures à cette version), reporter à la main dans
`infra/.env` les réglages de la fiche (surcouches `COMPOSE_FILE`, réseau du proxy).
Ne remettre `LIFEOS_CONNECTORS_PROVIDER=google` qu'une fois le secret du client Google
recopié dans `infra/secrets/lifeos_google_client_secret` : sans lui, l'API ne démarre pas.

`restore.sh` lit les deux archives et vérifie la clé et le schéma **avant** de remplacer quoi
que ce soit. Il sauvegarde la base courante dans `/var/backups/lifeos/` (si elle contient
déjà des données), applique les migrations, puis vérifie tout. Code de sortie : 0 restauré et
vérifié, 2 restauré mais des originaux manquent (§4), 1 échec.

### Étape 7 : réseau, certificat, modèles

- **Derrière un reverse proxy existant** (Nginx Proxy Manager…) : la surcouche
  `compose.proxy.yaml` exige que le réseau Docker externe `lifeos-proxy` existe **avant** le
  premier `docker compose up`. `install.sh --behind-proxy` (ou `restore-drill.sh
  --behind-proxy`) le crée ; `restore.sh` refuse de démarrer sans lui. Reconnecter ensuite le
  conteneur du proxy au réseau et recréer l'hôte (`lifeos-traefik`, port 80) :
  [installation.md §4](installation.md#derrière-un-reverse-proxy-existant-nginx-proxy-manager).
- **Certificat** (mode standard) : remettre les fichiers du certificat dans
  `infra/traefik/certs/` (`tls.yml` est restauré, pas les clés privées) ou en demander un
  nouveau ([exploitation.md §2](exploitation.md#2-domaine-et-certificat-tls)).
- **Modèles** (si l'option `--models` n'a pas été utilisée) :

  ```bash
  docker compose --profile setup run --rm ml-download
  docker compose --profile setup run --rm stt-download
  docker compose restart ml stt
  ```

  La recherche par le sens se reconstruit seule si le modèle a changé.

### Étape 8 : l'accès du propriétaire

- **Même domaine** (`LIFEOS_WEBAUTHN_RP_ID` inchangé) : les passkeys existantes fonctionnent
  telles quelles, elles ont été restaurées avec la base.
- **Autre domaine** (exercice à côté de la production) ou appareils perdus : créer un lien
  d'enrôlement, puis l'ouvrir sur l'appareil qui portera la passkey :

  ```bash
  docker compose exec api lifeos-admin enrollment-token --email moi@exemple.fr
  ```

### Étape 9 : vérifier

1. Le rapport de `verify-data` affiché par `restore.sh` se termine par `result: OK` : schéma à
   jour, compteurs, recherche plein texte, originaux présents et déchiffrés. Le relancer à
   tout moment, depuis `infra/` : `docker compose exec api lifeos-admin verify-data`.
2. Dans le navigateur :
   - **connexion** avec la passkey (ou le lien de l'étape 8) ;
   - **Tâches** : les tâches ouvertes attendues sont là ;
   - **Finances** : les totaux du mois correspondent à ceux d'avant l'incident ;
   - **Recherche** : un mot connu retrouve la tâche ou le document attendu ;
   - **Documents** : ouvrir un original restauré (il est déchiffré à la volée).

### Étape 10 : après la restauration

- **Rejouer les effacements** faits après la sauvegarde
  ([politique-de-donnees.md §2](politique-de-donnees.md#2-effacement-droit-à-loubli)). Si
  l'ancienne base est accessible (sauvegarde de sécurité de `restore.sh`, ancien serveur), la
  liste des suppressions vient de son journal d'audit :

  ```bash
  docker compose exec -T postgres createdb -U postgres lifeos_avant
  docker compose exec -T postgres pg_restore -U postgres -d lifeos_avant < /var/backups/lifeos/pre-restore-<date>.pgdump
  docker compose exec postgres psql -U postgres -d lifeos_avant -c \
    "SELECT occurred_at, action, target_type, target_id FROM audit_events
     WHERE action LIKE '%delete%' AND occurred_at > '<date de la sauvegarde>' ORDER BY occurred_at"
  docker compose exec -T postgres dropdb -U postgres lifeos_avant
  ```

  Supprimer à nouveau ces objets depuis l'interface. Si l'ancien serveur est perdu, ces
  suppressions sont perdues avec lui : les refaire de mémoire.
- Supprimer `/root/documents-key` (`shred -u`) et, une fois la restauration validée, la
  sauvegarde de sécurité de `/var/backups/lifeos/`.
- **Vraie reprise** : réinstaller les sauvegardes sur la nouvelle machine, avec le même dépôt
  (`sudo infra/scripts/install-backups.sh`), puis `sudo infra/scripts/export-keys.sh --check`.
  **Exercice** : ne jamais lancer `install-backups.sh` sur la VM d'exercice (elle écrirait
  dans le dépôt de production), puis détruire la VM : elle contient toutes les données.

## 4. Originaux manquants

`verify-data`, `restore.sh` et le test hebdomadaire listent chaque original manquant par
identifiant de document (`missing original: document <id>`), sans jamais rien en afficher
d'autre. La métrique `lifeos_backup_missing_originals` suit la dernière sauvegarde. Causes
possibles :

- le fichier a disparu du volume du serveur (incident disque, manipulation) : toutes les
  sauvegardes suivantes en héritent ;
- « newer than the files » : le document a été envoyé pendant la sauvegarde. Ce n'est pas une
  erreur, il figure dans la sauvegarde suivante.

Récupérer un original dans une exécution plus ancienne (depuis `infra/`, en root, réglages
restic chargés : `sudo -i`, `cd /opt/lifeos/infra`, `set -a; . /etc/lifeos/backup.env; set +a`) :

```bash
docker compose exec postgres psql -U postgres -d lifeos -tAc \
  "SELECT storage_key FROM documents WHERE id = '<document-id>'"     # <user>/<document>.bin
restic snapshots --host lifeos --tag documents
restic dump <instantané-documents> lifeos-documents.tar | tar -t | grep <document-id>
restic dump <instantané-documents> lifeos-documents.tar \
  | docker compose run --rm --no-deps -T --entrypoint tar worker -C /data/documents -xf - ./<user>/<document>.bin
docker compose exec api lifeos-admin verify-data
```

Un original introuvable dans toutes les sauvegardes est perdu. Le constater explicitement :
`docker compose exec api lifeos-admin reconcile-originals` liste les originaux manquants, et
`… reconcile-originals --yes` les enregistre comme perdus : le document reste (titre, texte
extrait) et indique que l'original n'est plus conservé. Un événement d'audit
`document.original_missing` par document garde la trace de la perte. La commande refuse
d'agir si le dossier ne contient aucun original (volume non monté).

## 5. Exercice de restauration

À faire **au moins une fois par an**, et après tout changement de dépôt, de domaine ou de
version majeure. Les tests automatiques (§6) ne remplacent pas l'exercice : ils ne testent ni
la fiche de récupération, ni l'installation, ni la connexion depuis un vrai appareil.

1. Créer une VM vierge dans Proxmox (même gabarit que la production).
2. Choisir le domaine :
   - le domaine de production, sur un réseau isolé (les passkeys du téléphone fonctionnent) ;
   - ou un domaine de test, avec `--enroll-email` pour se connecter.
3. Suivre le §3 **avec la seule fiche de récupération** : c'est elle que l'exercice teste.
   L'assistant reste désactivé (sans `--keep-llm`) : la VM d'exercice ne consomme pas le
   quota de la production et ne produit pas de bilans en double.
4. Vérifier (étape 9), noter le résultat ci-dessous, puis détruire la VM.

Mesures : la **durée** (RTO, du début de l'étape 1 à l'étape 9) et l'**âge des données**
(RPO, écart entre la sauvegarde restaurée et l'incident ; au plus 24 h avec une sauvegarde
par nuit). `restore-drill.sh` affiche les deux et la ligne à copier ici.

### Journal des exercices

| Date | Exécution restaurée | Durée (RTO) | Âge des données (RPO) | Résultat | Remarques |
|---|---|---|---|---|---|
| | | | | | |

## 6. Ce que les tests automatiques prouvent

- **Chaque semaine** (`lifeos-restore-check.timer`), et **chaque mois depuis la copie hors
  site** (`lifeos-restore-check-offsite.timer`) : la dernière exécution est restaurée dans une
  base et un volume jetables, puis `verify-data` déchiffre chaque original avec la clé du
  serveur. Une clé différente de celle des sauvegardes, un original manquant ou illisible
  font échouer le test, ce qui déclenche une alerte.
- **En CI** (`scripts/test-backup.sh`) : un LifeOS jetable avec une passkey logicielle, une
  tâche, une opération et un document. Le test vérifie la sauvegarde (dépôt principal et
  copie), la restauration de test, le signalement d'un original manquant et le refus d'une
  paire incomplète. Il restaure ensuite sur des volumes vides et vérifie la connexion avec la
  même passkey, la recherche, les finances et le déchiffrement de l'original. Il vérifie enfin
  le refus d'une mauvaise clé, et l'alerte et la métrique d'échec d'une sauvegarde ratée.
- **En CI** (`scripts/test-update.sh`) : la sauvegarde faite avant une mise à jour
  (étiquette `pre-update`) est restaurée par `update.sh --rollback --restore-db`, et d'elle-même
  quand une migration de la mise à jour perd des données en redescendant
  ([exploitation.md §6](exploitation.md#6-mise-à-jour-et-retour-arrière)) : la base d'avant la
  mise à jour revient, la base remplacée est d'abord mise de côté.

## 7. Problèmes fréquents

| Message | Cause, solution |
|---|---|
| `… is not the key of this backup` | la clé déposée n'est pas celle des sauvegardes : chercher la bonne dans le gestionnaire de mots de passe (son empreinte est sur la fiche) |
| `the backup's schema … is unknown to this code` | code plus ancien que la sauvegarde : `git checkout <version>` (étiquette `version:`) |
| `behind a reverse proxy: create the Docker network first` | `docker network create lifeos-proxy` (ou `install.sh --behind-proxy`), puis y connecter le proxy |
| `this run has no documents snapshot` | exécution incomplète : en choisir une autre (`restic snapshots --host lifeos --tag db`) ou restaurer la base seule (`--no-documents`) |
| `originals_unreadable` | mauvaise clé, ou fichiers abîmés : vérifier l'empreinte (`export-keys.sh --check`) |
| la passkey est refusée | domaine différent de celui d'origine : lien d'enrôlement (étape 8) |
