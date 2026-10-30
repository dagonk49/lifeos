# Dépannage

Ce guide sert quand LifeOS fonctionne mal **après** l'installation. Les problèmes de la
première installation sont dans [installation.md](installation.md#problèmes-fréquents), ceux
d'une restauration dans [restauration.md](restauration.md#7-problèmes-fréquents), ceux des
comptes Google dans [guide-google.md](guide-google.md#problèmes-fréquents).

Toutes les commandes se lancent sur la VM, depuis `/opt/lifeos/infra`.

## 1. Par où commencer

```bash
cd /opt/lifeos/infra
docker compose ps                                   # chaque service « running », « healthy » s'il a un contrôle
docker compose logs --tail 100 api worker scheduler # journaux JSON, sans contenu personnel
df -h / /var/lib/docker                             # espace disque
```

Puis, selon ce que vous voyez :

| Ce que vous constatez | Section |
|---|---|
| Le site ne répond pas, erreur 502 ou 504, certificat refusé | §2 |
| Page blanche, alors que le site répond | §5 |
| Passkey refusée, « Requête refusée : elle ne provient pas de l'adresse de LifeOS » | §6 |
| Rappels, notifications, bilans, agendas ou e-mails qui ne se mettent plus à jour | §3 |
| Documents « Lecture en cours » ou dictées en attente sans fin, recherche par le sens incomplète | §4 |
| L'assistant IA répond qu'il est indisponible | §7 |
| Disque presque plein | §8 |
| Une alerte reçue sur le téléphone | le texte de l'alerte dit quoi faire ; le détail de chaque alerte est dans [exploitation.md §5](exploitation.md#5-supervision-et-alertes) |

Si rien ne s'explique : §9 (signaler un problème sans donnée personnelle).

## 2. Le site ne répond pas

1. **Vivacité de l'API**, depuis un appareil du réseau :
   `curl -s https://lifeos.mondomaine.fr/healthz` doit répondre `{"status":"ok"}`.
2. **Disponibilité** (base et Redis), qui n'est pas routée par Traefik :

   ```bash
   docker compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5).read())"
   ```

   `{"status":"ok",…}` : tout va bien. Une erreur 503 indique `"database":"error"` ou
   `"redis":"error"` : `docker compose ps postgres redis`, puis
   `docker compose logs --tail 50 postgres` (ou `redis`) ; `docker compose up -d` les relance.
3. **Traefik** : `docker compose ps traefik`. S'il redémarre en boucle, un autre reverse proxy
   occupe sans doute les ports 80/443 ([installation.md §4](installation.md#derrière-un-reverse-proxy-existant-nginx-proxy-manager)).
   Derrière Nginx Proxy Manager, une erreur 502 veut dire que son conteneur n'est plus sur le
   réseau `lifeos-proxy` : `docker network connect lifeos-proxy <conteneur>`.
4. **Certificat expiré ou refusé** (alerte `CertificatRefuse` ou `CertificatBientotExpire`) :

   ```bash
   openssl x509 -noout -enddate -subject -in traefik/certs/fullchain.pem
   ```

   Renouveler le certificat, recopier `fullchain.pem` et `privkey.pem` dans
   `infra/traefik/certs/`, puis `docker compose restart traefik`
   ([exploitation.md §2](exploitation.md#2-domaine-et-certificat-tls)). Un nom différent de
   `LIFEOS_DOMAIN` dans `subject` est refusé aussi. Derrière Nginx Proxy Manager, renouveler
   depuis son interface.
5. Juste après une mise à jour : `infra/scripts/update.sh --status`, puis, si besoin,
   `infra/scripts/update.sh --rollback` ([exploitation.md §6](exploitation.md#6-mise-à-jour-et-retour-arrière)).

## 3. Tâches de fond, rappels et synchronisations

Rappels, regroupements de suggestions, bilans automatiques, purges, indexation, lecture des
agendas et des e-mails : tout passe par deux services. Le **planificateur** (`scheduler`)
envoie les tâches à l'heure dans Redis ; le **worker** les exécute. Si l'un des deux s'arrête,
plus rien n'avance, sans erreur visible dans les pages.

```bash
docker compose ps scheduler worker
docker compose logs --tail 100 scheduler worker
docker compose up -d scheduler worker      # les relancer
```

- Le worker écrit `job_failed` (nom de la tâche et trace de l'erreur) quand une tâche
  échoue, `job_done` quand elle réussit ; ses métriques (`lifeos_job_runs_total`) alimentent les alertes
  `EchecsDeTaches` et `TachesAbandonnees`.
- Au redémarrage, les tâches manquées sont rattrapées une fois (pas une fois par exécution
  manquée). Un rappel dont l'heure est passée part au redémarrage (ou à la fin de vos plages
  de tranquillité).
- **Redis perdu** (volume effacé) : seuls les messages en file sont perdus, rien d'autre (la
  base reste la référence). Les tâches périodiques repartent d'elles-mêmes, et les filets de
  sécurité reprennent chaque minute les documents et dictées en attente.

Notifications qui n'arrivent pas, alors que le worker tourne : vérifier dans **Réglages →
Notifications** la catégorie (désactivée ?), les créneaux de livraison, le maximum par jour
et les heures de regroupement des suggestions, puis les plages de tranquillité dans
**Réglages → Préférences** ([guide d'utilisation](guide-utilisateur.md#réglages)). Il n'y a pas
encore de notification push : tout s'affiche dans le menu **Notifications** de l'application.

Agenda ou e-mails qui ne se mettent plus à jour :

- sous l'agenda ou le libellé, dans **Réglages → Comptes connectés**, un message dit pourquoi
  la dernière lecture a échoué ([guide-google.md](guide-google.md#problèmes-fréquents)) ;
- « Reconnexion nécessaire » sur le compte : « Reconnecter le compte »
  ([guide-google.md §8](guide-google.md#8--reconnexion-nécessaire-)) ;
- sans message : planificateur ou worker arrêté (ci-dessus), ou
  `LIFEOS_CONNECTORS_PROVIDER=disabled` dans `infra/.env` (aucune synchronisation n'est alors
  planifiée).

« Le fournisseur n'a pas confirmé la modification » après une approbation d'agenda : le
worker vérifie de lui-même toutes les 5 minutes, sans rien renvoyer
([ADR 0016](adr/0016-ecritures-externes-sous-approbation.md)). Si la proposition reste dans cet
état, vérifier que le worker tourne.

## 4. Documents, dictées et recherche

| Symptôme | Que faire |
|---|---|
| Document « Lecture en cours » depuis longtemps (alerte `DocumentsEnAttente`) | `docker compose ps ml worker` ; `docker compose logs --tail 50 ml`. Le worker reprend chaque minute les documents en attente. Échec définitif : « Relire le document » dans sa page |
| Lecture échouée `too_large` ou `extraction_crashed` | mémoire de lecture trop faible pour ce fichier : [exploitation.md §7](exploitation.md#7-dimensionnement-vm-10-go-i7-6700-gtx-1050-ti) (« Fichiers non fiables ») |
| « Le stockage des documents n'est pas configuré » | `infra/secrets/lifeos_documents_key` absent. **Ne pas** en recréer une si des documents existent déjà : remettre celle de la fiche de récupération ([restauration.md](restauration.md)) |
| Dictée en attente sans fin (alerte `DicteesEnAttente`) | modèle de transcription absent : `docker compose --profile setup run --rm stt-download`, puis `docker compose restart stt` ; sinon `docker compose logs --tail 50 stt` |
| La recherche par le sens ne trouve pas les éléments récents | la page **Recherche** montre la progression de l'index ; le worker indexe toutes les 2 minutes. Modèle absent : `docker compose --profile setup run --rm ml-download` puis `docker compose restart ml` |
| Après un changement de `LIFEOS_EMBEDDINGS_MODEL` | tout est réindexé automatiquement ; la progression est visible dans **Recherche** |

Import de relevé CSV refusé : le fichier dépasse `LIFEOS_FINANCE_IMPORT_MAX_BYTES` (2 Mio) ou
`LIFEOS_FINANCE_IMPORT_MAX_ROWS` (5 000 lignes), ou l'aperçu a dépassé 24 h (renvoyer le
fichier). Les lignes « Illisible » donnent leur raison (date, montant à plus de deux
décimales…) : corriger les colonnes choisies, puis **Relire avec ces colonnes**.

Export (**Réglages → Mes données**) refusé avec les originaux : clé des documents absente
(ligne ci-dessus) ; l'export sans originaux reste possible.

## 5. Page blanche

Le site répond mais la page reste blanche, ou les boutons ne réagissent pas : le navigateur
bloque les scripts, presque toujours parce qu'un reverse proxy en amont ajoute sa propre
politique de sécurité du contenu (CSP) ou met les pages en cache.

1. Vérifier les en-têtes, deux fois de suite :

   ```bash
   for _ in 1 2; do curl -sI https://lifeos.mondomaine.fr/login | grep -i -E '^(content-security-policy|cache-control):'; done
   ```

   Attendu : **une seule** ligne `content-security-policy`, avec un nonce différent à chaque
   appel, et `cache-control` avec `no-store`.
2. Deux lignes, ou le même nonce deux fois : corriger le reverse proxy (Nginx Proxy Manager :
   *Cache Assets* décoché, rien sur cet en-tête ni sur le cache dans *Advanced*), voir
   [exploitation.md §3](exploitation.md#politique-de-sécurité-du-contenu-csp).
3. Dans le navigateur, la console de développement (F12) affiche
   « Refused to execute … Content Security Policy » : même cause. Une extension du navigateur
   qui injecte des scripts peut aussi le provoquer : essayer en navigation privée.

## 6. Connexion et passkeys

| Symptôme | Cause, solution |
|---|---|
| « Opération annulée ou refusée par l'appareil » | domaine ouvert différent de `LIFEOS_WEBAUTHN_RP_ID`, ou pas de HTTPS valide : ouvrir exactement `https://<LIFEOS_DOMAIN>/` |
| La passkey n'est plus proposée après un changement de domaine | les passkeys sont liées au domaine : remettre l'ancien domaine, ou créer un lien d'enrôlement (`lifeos-admin enrollment-token`) pour une nouvelle passkey |
| « Requête refusée : elle ne provient pas de l'adresse de LifeOS » | `LIFEOS_PUBLIC_ORIGIN` différent de l'adresse affichée par le navigateur (`https://` + domaine, sans chemin) : corriger `infra/.env`, puis `docker compose up -d` |
| « Lien d'enrôlement invalide ou expiré » | le lien dure 15 minutes et ne sert qu'une fois : en créer un autre |
| Déconnecté sans raison | session révoquée (une à une, par « Déconnecter les autres sessions », en révoquant sa passkey, ou par `lifeos-admin revoke-sessions`) ou expirée (30 jours, ou 7 jours sans usage) |
| Appareil perdu ou volé | [guide d'utilisation](guide-utilisateur.md#appareil-perdu-ou-volé) |

Dans le journal d'audit (**Réglages → Journal d'audit**), les refus apparaissent en rouge, avec
leur action ([recette-securite.md](recette-securite.md)).

## 7. Assistant IA indisponible

Sans assistant, tout le reste fonctionne. Selon le message :

- « quota du jour atteint » : plafond du niveau gratuit de Gemini, renouvelé chaque jour vers
  9 h (heure de Paris) ([guide-gemini.md](guide-gemini.md)) ;
- assistant en pause : **Réglages → Espaces et assistant IA**, lever la pause ;
- l'IA n'est pas autorisée pour cet espace : l'autoriser dans les mêmes réglages ;
- sinon : `LIFEOS_LLM_PROVIDER=gemini` dans `infra/.env`, clé présente dans
  `infra/secrets/lifeos_gemini_api_key`, et `docker compose logs --tail 50 api`.

Le panneau d'usage compte des appels « en cours » qui ne finissent pas : une réservation reste
en suspens si un processus a été arrêté pendant l'appel. Elle est libérée au bout de
10 minutes ; pour vérifier qu'aucune ne traîne :

```bash
docker compose exec postgres psql -U postgres -d lifeos -c "SELECT count(*) FROM llm_calls WHERE outcome = 'reserved'"
```

([exploitation.md §5](exploitation.md#quotas-ia), [ADR 0010](adr/0010-reservation-des-quotas-llm.md)).

## 8. Disque presque plein

Alerte `DisquePresquePlein` ou `DisqueSeRemplit`. Mesurer d'abord :

```bash
df -h
docker system df                          # images, volumes, cache de construction
sudo du -sh /var/backups/lifeos /var/lib/docker/volumes/lifeos_* 2>/dev/null
```

Ce qui peut être libéré sans risque :

- le cache de construction Docker : `docker builder prune` ;
- les images des anciennes versions : `infra/scripts/update.sh --status` les liste ; les
  mises à jour gardent les 3 dernières (`--keep N`) ;
- `/var/backups/lifeos/` : les sauvegardes de sécurité laissées par `restore.sh`, une fois la
  restauration validée.

Les journaux des conteneurs sont déjà limités (10 Mo × 5 par conteneur). Ne jamais supprimer
les volumes `lifeos_pgdata` ni `lifeos_documents`, ni un dépôt restic à la main. Si la base ou
les documents grossissent normalement, agrandir le disque de la VM.

## 9. Signaler un problème sans donnée personnelle

Les journaux, métriques et alertes de LifeOS sont conçus sans contenu personnel ni adresse
e-mail ([politique-de-donnees.md §5](politique-de-donnees.md#5-journalisation)). Joindre :

- la version : `git -C /opt/lifeos describe --tags --always` et
  `infra/scripts/update.sh --status` ;
- `docker compose ps` ;
- le **code** de l'erreur (le mot en `snake_case` des réponses de l'API, par exemple
  `last_passkey`) et l'**identifiant de requête** : en-tête `x-request-id` de la réponse
  (outils de développement du navigateur, onglet Réseau) ; les lignes du journal
  correspondantes : `docker compose logs api | grep <identifiant>` ;
- les lignes `error` ou `job_failed` du worker autour de l'heure du problème, après les
  avoir relues : une trace d'erreur peut citer une valeur.

Ne jamais joindre : le dossier `infra/secrets/`, `/etc/lifeos/`, la fiche de récupération
(`export-keys.sh`), une archive d'export, une sauvegarde, ni une capture d'écran qui montre
vos données. Pour reproduire un problème de lecture de document, fournir un document
**synthétique** de même structure, avec des valeurs inventées.
