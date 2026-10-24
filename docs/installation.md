# Installation de LifeOS 1.0 sur la VM

Guide pas à pas pour la machine de référence : VM Debian sur Proxmox, 8 vCPU (i7-6700),
10 Go de RAM, GTX 1050 Ti en option. Les détails d'exploitation (exposition réseau,
sauvegardes, dimensionnement, mises à jour) sont dans [exploitation.md](exploitation.md).

Durée : environ 30 minutes, dont la construction des images et le téléchargement des modèles
(≈ 3 Go).

## 1. Préparer la VM (Proxmox)

| Réglage | Valeur |
|---|---|
| Système | Debian 13 (ou 12), installation minimale + serveur SSH |
| Type de CPU | **`host`** : expose AVX2/FMA, indispensable pour la vitesse des modèles locaux |
| vCPU / RAM | 8 / 10 Go (ballooning inutile) |
| Disque | 60 Go minimum (système, images ≈ 4 Go, modèles ≈ 3 Go, base et documents) ; plus selon vos documents |
| GPU (facultatif) | GTX 1050 Ti en PCI passthrough (VM `q35` + OVMF, VT-d/IOMMU activés) : voir §7 |

Vérification dans la VM : `grep -o -w 'avx2\|fma' /proc/cpuinfo | sort -u` doit afficher
`avx2` et `fma`.

## 2. Installer Docker

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"     # puis se déconnecter / reconnecter
```

## 3. Choisir le nom de domaine (avant tout)

Les passkeys sont liées au nom de domaine : **il ne doit plus changer ensuite**.

- **Recommandé** : un sous-domaine d'un domaine que vous possédez (ex.
  `lifeos.mondomaine.fr`), résolu vers l'IP de la VM par votre DNS local (box, Pi-hole,
  routeur). Un certificat Let's Encrypt s'obtient par challenge DNS, sans exposer la VM à
  Internet.
- Réseau local seulement : un nom interne (ex. `lifeos.maison.lan`) avec une autorité locale
  (`mkcert`) installée sur vos appareils. Google refuse ces noms : pas de compte Google
  connecté (agenda, Gmail) dans ce cas (§5).

Accès hors de chez vous : passer par un VPN (WireGuard) plutôt qu'ouvrir le port 443.

## 4. Lancer l'installation

```bash
sudo git clone <url-du-dépôt> /opt/lifeos && sudo chown -R "$USER" /opt/lifeos
cd /opt/lifeos
infra/scripts/install.sh --domain lifeos.mondomaine.fr --email moi@exemple.fr --name "Mon prénom"
```

Le script, que l'on peut relancer sans risque :

1. vérifie Docker, AVX2 et la mémoire ;
2. crée les secrets dans `infra/secrets/` : mots de passe et **clé de chiffrement des
   documents** ;
3. propose de coller la clé Gemini (facultatif, voir §5) ;
4. crée `infra/.env` avec votre domaine ;
5. construit et démarre les services ;
6. télécharge les modèles locaux (recherche par le sens ≈ 1,2 Go, dictée ≈ 1,6 Go) ;
7. vérifie l'OCR et crée votre compte. Il affiche un **lien d'enrôlement** à usage unique,
   valable 15 minutes.

Options : `--gpu` (transcription sur la carte, §7), `--behind-proxy` (voir ci-dessous),
`--no-models` (téléchargements plus tard), `--yes` (sans question).

### Derrière un reverse proxy existant (Nginx Proxy Manager)

Si un reverse proxy occupe déjà les ports 80 et 443 de la machine et sert d'autres sites, le
Traefik de LifeOS ne peut pas les prendre : il redémarre en boucle et LifeOS reste
injoignable. Dans ce cas, le proxy existant reste **la seule porte d'entrée** :

```
navigateur → Nginx Proxy Manager (HTTPS, certificat Let's Encrypt) → Traefik de LifeOS → web / api
```

Le Traefik de LifeOS ne publie alors aucun port. Il garde le routage, les en-têtes de sécurité
et la limite des tentatives de connexion (par adresse du client, transmise par le proxy).

1. Installer, ou relancer l'installation, avec l'option :

   ```bash
   infra/scripts/install.sh --domain lifeos.mondomaine.fr --behind-proxy
   ```

   Le script ajoute `compose.proxy.yaml` à `COMPOSE_FILE` dans `infra/.env`, crée le réseau
   Docker `lifeos-proxy` et y connecte le conteneur de Nginx Proxy Manager s'il le trouve.
   Pour utiliser un réseau existant du proxy : `--proxy-network nom_du_reseau`.
2. Dans Nginx Proxy Manager, **Hosts → Proxy Hosts → Add Proxy Host** :
   - *Domain Names* : `lifeos.mondomaine.fr` ;
   - *Scheme* : `http` ; *Forward Hostname / IP* : `lifeos-traefik` ; *Forward Port* : `80` ;
   - onglet *SSL* : *Request a new SSL Certificate*, puis cocher *Force SSL* et *HTTP/2
     Support*.
3. Rendre la connexion au réseau permanente (sinon elle disparaît quand le conteneur de Nginx
   Proxy Manager est recréé, par exemple à sa mise à jour). Dans son `docker-compose.yml` :

   ```yaml
   services:
     app:   # le service de Nginx Proxy Manager
       networks: [default, lifeos-proxy]
   networks:
     lifeos-proxy:
       external: true
   ```

Le certificat est alors celui de Nginx Proxy Manager : l'étape « Certificat » du §6 ne
s'applique pas.

**Tout de suite après** : copiez `infra/secrets/lifeos_documents_key` dans votre gestionnaire
de mots de passe. Sans cette clé, les originaux des documents sont illisibles, y compris dans
les sauvegardes.

## 5. Assistant IA (Gemini, sans frais)

Suivre le [guide Gemini](guide-gemini.md) : clé d'un projet Google Cloud **sans compte de
facturation** (rien ne peut être facturé), à coller dans `infra/secrets/lifeos_gemini_api_key`.
Ensuite :

```bash
cd /opt/lifeos/infra
sed -i 's/^LIFEOS_LLM_PROVIDER=.*/LIFEOS_LLM_PROVIDER=gemini/' .env
docker compose up -d
```

Le script le fait seul si la clé est fournie pendant l'installation. Sans clé, LifeOS
fonctionne entièrement, sans l'assistant.

### Comptes connectés (Google Agenda, Gmail : facultatif)

Suivre le [guide Google](guide-google.md) : projet Google Cloud, écran de consentement
publié, client OAuth « Application Web » dont l'URI de redirection est
`https://<votre domaine>/connexions/retour`. Il faut un domaine public (§3). Ensuite :

```bash
cd /opt/lifeos/infra
printf '%s' 'CODE_SECRET_DU_CLIENT' > secrets/lifeos_google_client_secret
sed -i 's/^LIFEOS_CONNECTORS_PROVIDER=.*/LIFEOS_CONNECTORS_PROVIDER=google/' .env
sed -i 's/^LIFEOS_GOOGLE_CLIENT_ID=.*/LIFEOS_GOOGLE_CLIENT_ID=<ID client>/' .env
docker compose up -d
```

Puis **Réglages → Comptes connectés** dans LifeOS. La clé qui chiffre les jetons
(`infra/secrets/lifeos_connectors_key`) est créée par `init-secrets.sh`. La perdre oblige
seulement à reconnecter les comptes.

## 6. Certificat et premier accès

1. Certificat : déposer `fullchain.pem` et `privkey.pem` dans `infra/traefik/certs/`, puis
   `cp infra/traefik/dynamic/tls.yml.example infra/traefik/dynamic/tls.yml`
   ([exploitation.md §2](exploitation.md#2-domaine-et-certificat-tls)). Derrière Nginx Proxy
   Manager, c'est lui qui fournit le certificat (§4).
2. Ouvrir le lien d'enrôlement sur l'appareil qui portera la première passkey (téléphone ou
   ordinateur), nommer l'appareil, valider.
3. Dans **Réglages → Appareils et passkeys**, ajouter une **deuxième passkey** (autre
   appareil) : c'est votre secours.
4. Dans **Réglages**, autoriser l'assistant IA pour les espaces souhaités (personnel,
   professionnel).

Lien perdu ou appareils perdus :
`docker compose exec api lifeos-admin enrollment-token --email moi@exemple.fr`.

## 7. GPU pour la dictée (facultatif)

La transcription fonctionne sur le CPU. La GTX 1050 Ti la rend plusieurs fois plus rapide :

1. passer la carte à la VM (Proxmox : VT-d/IOMMU, VM `q35` + OVMF) ;
2. dans la VM, installer le pilote NVIDIA **570 ou 580** (la 580 est la dernière branche
   qui gère Pascal) puis le **NVIDIA Container Toolkit**
   (`sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`) ;
3. vérifier `nvidia-smi`, puis relancer `infra/scripts/install.sh --gpu`.

Si la carte n'est pas utilisable, le service revient seul sur le CPU.

## 8. Sauvegardes (à faire le premier jour)

Prérequis : `restic` ≥ 0.17 (binaire officiel : Debian fournit une version plus ancienne, voir
[restauration.md](restauration.md) §3, étape 1) et `jq`. Choisir un dépôt restic (NAS par
SSH, rest-server, Backblaze B2, S3…), une copie **hors de la maison** si le dépôt n'y est pas
déjà, et un sujet [ntfy](https://ntfy.sh) pour les alertes (application sur le téléphone) :

```bash
sudo /opt/lifeos/infra/scripts/install-backups.sh --repository sftp:backup@nas.lan:/srv/restic/lifeos \
  --copy-repository b2:mon-bucket:lifeos --alert-url https://ntfy.sh/<sujet long et aléatoire>
sudo /opt/lifeos/infra/scripts/export-keys.sh   # fiche de récupération
```

Le premier script installe les sauvegardes automatiques : chaque nuit, avec copie hors site ;
une restauration de test chaque dimanche ; une alerte à chaque échec. Il lance aussi une
première sauvegarde et les tests (détails : [exploitation.md §4](exploitation.md#4-sauvegardes-restic)).
Les identifiants d'un dépôt en ligne (`B2_ACCOUNT_ID`…) se mettent dans
`/etc/lifeos/backup.env`.

Le second affiche la **fiche de récupération** : clé des documents, mots de passe restic,
réglages des dépôts, domaine. La copier dans votre gestionnaire de mots de passe : sans elle,
aucune restauration n'est possible ([restauration.md](restauration.md)).

## 9. Vérifier que tout va bien

```bash
cd /opt/lifeos/infra
docker compose ps                         # tous « running » / « healthy »
docker compose exec ml lifeos-ml selftest # « OCR prêt »
docker compose logs --tail 50 api worker  # aucun contenu personnel dans les logs
```

Dans l'application, l'accueil affiche le briefing du jour, et la page **Recherche** montre la
progression de l'index sémantique.

## 10. Mettre à jour

```bash
cd /opt/lifeos && git pull
cd infra && ./scripts/init-secrets.sh && docker compose up -d --build
```

Faire une sauvegarde avant toute mise à jour (les migrations de base s'appliquent seules).

## Problèmes fréquents

| Symptôme | Cause probable |
|---|---|
| `traefik` redémarre sans cesse, colonne PORTS vide dans `docker compose ps` | les ports 80/443 sont déjà pris par un autre reverse proxy : relancer avec `--behind-proxy` (§4) |
| Nginx Proxy Manager affiche « 502 Bad Gateway » pour LifeOS | conteneur de Nginx Proxy Manager absent du réseau `lifeos-proxy` : `docker network connect lifeos-proxy <conteneur>` (§4, étape 3) |
| « Opération annulée ou refusée par l'appareil » à l'enrôlement | domaine différent de `LIFEOS_WEBAUTHN_RP_ID`, ou pas de HTTPS (certificat autosigné : Chrome refuse alors les passkeys) |
| Le micro n'apparaît pas | dictée désactivée (`LIFEOS_STT_PROVIDER`), navigateur sans enregistrement audio, ou page non servie en HTTPS |
| Dictée « en attente » sans fin | modèle de transcription absent : `docker compose --profile setup run --rm stt-download` |
| Documents « lecture locale indisponible » | service `ml` arrêté : `docker compose up -d ml` puis `docker compose logs ml` |
| « Le stockage des documents n'est pas configuré » | `infra/secrets/lifeos_documents_key` absent : relancer `./scripts/init-secrets.sh` |
| « Les comptes connectés ne sont pas configurés sur ce serveur » | `LIFEOS_CONNECTORS_PROVIDER=disabled`, ou client Google incomplet : [guide Google](guide-google.md) |
| Google affiche « redirect_uri_mismatch » | URI de redirection du client OAuth différente de `https://<votre domaine>/connexions/retour` |
| Alerte « LifeOS: lifeos-backup failed » | `journalctl -u lifeos-backup.service` sur la VM ([exploitation.md §4](exploitation.md#4-sauvegardes-restic)) |
| L'assistant répond « quota du jour atteint » | plafond gratuit Gemini : il se renouvelle chaque jour vers 9 h (heure de Paris) |
