# Recette sur le serveur cible

But : vérifier sur la vraie machine (VM dédiée, réseau, certificat, appareils du propriétaire)
que les fonctions principales passent **avec les ressources allouées**, et **mesurer** les
temps et les pointes de mémoire. Les limites de [exploitation.md §7](exploitation.md#7-dimensionnement-vm-10-go-i7-6700-gtx-1050-ti)
sont des estimations tant que cette recette n'a pas été faite.

La recette est terminée quand :

- chaque ligne de la liste du §3 est passée (✅), ou a une raison écrite ;
- `infra/scripts/recette.sh` sort sans erreur (code 0) sur la machine cible, et son rapport est
  rangé dans le journal (§4) ;
- les pointes mesurées sont reportées dans exploitation.md §7, et les limites ajustées (§5).

Rien de personnel n'entre dans les mesures automatiques (documents, scans et textes générés,
lecture d'un texte fourni ci-dessous) ni n'en sort (durées, statuts, tailles, mémoire). Dans le
journal, noter des résultats (« lu correctement », « 2 s »), jamais le contenu d'un document,
d'une dictée ou d'une réponse.

## 1. Préparer

| Élément | Attendu |
|---|---|
| VM | dédiée, Debian 13 (ou 12), CPU de type `host` (AVX2/FMA), 8 vCPU, 10 Go, disque SSD ([installation.md §1](installation.md#1-préparer-la-vm-proxmox)) ; GPU passé en PCI si la dictée doit l'utiliser (§7 du même guide) |
| LifeOS | installé par `install.sh`, modèles téléchargés, `docker compose ps` tout « healthy » |
| Accès | domaine définitif et certificat valide (Traefik, ou Nginx Proxy Manager) : les passkeys y sont liées |
| Sauvegardes, alertes | `install-backups.sh` puis `install-monitoring.sh` faits ([installation.md §8 et §9](installation.md#8-sauvegardes-à-faire-le-premier-jour)) |
| Appareils | un téléphone et un ordinateur au moins (idéalement un iPhone et un Android) |
| Assistant | une clé Gemini gratuite ([guide-gemini.md](guide-gemini.md)) et l'IA autorisée dans l'espace personnel, si l'assistant fait partie de la recette |
| Enregistrement | une minute de lecture du texte ci-dessous, au dictaphone du téléphone (m4a, ogg, webm, mp3 ou wav), copiée sur la VM (`scp lecture.m4a vm:/tmp/`) |

Texte à lire (inventé, sans donnée personnelle ; le lire deux fois pour atteindre une minute) :

> Rappel pour la semaine prochaine : prendre rendez-vous au garage pour la révision de la
> voiture, de préférence un mardi matin. Acheter des ampoules pour la cuisine et deux piles
> pour la télécommande. Penser à arroser les plantes du balcon avant le départ de jeudi.
> Préparer la liste des courses pour le repas de samedi : des tomates, du fromage de chèvre,
> une baguette et un melon. Relire le contrat d'assurance de la maison et noter les questions
> à poser. Enfin, réserver deux places pour le concert du mois prochain, si possible au
> premier rang.

## 2. Mesures automatiques

```bash
cd /opt/lifeos
infra/scripts/recette.sh --audio /tmp/lecture.m4a
```

Le script, lancé sur la VM pendant que LifeOS tourne normalement (environ 5 minutes) :

| Phase | Mesure |
|---|---|
| repos | la mémoire de chaque service, LifeOS au repos |
| ocr | OCR local de photos de tickets et de scans de 3 pages générés (service `ml`, comme le worker) : secondes par document et par page |
| indexation | lots de 32 passages transformés en vecteurs (recherche par le sens) |
| transcription | première dictée (chargement du modèle compris), puis la vitesse : « × le temps réel », sur le CPU ou le GPU |
| https | le certificat (vérifié comme par un navigateur, jours restants), HSTS, CSP avec nonce, `/metrics` non publié ; temps des pages |
| api (avec session) | recherche, un document lu par le worker de bout en bout (envoi, lecture, suppression), l'assistant |
| charge | tout à la fois pendant 90 s : deux OCR, l'indexation, des dictées, des pages et des recherches |

Mémoire : `docker stats` chaque seconde, par service et par phase, et la pointe vue par le
noyau (cgroup), qui attrape les pointes brèves ; processus tués faute de mémoire, services
redémarrés, mémoire disponible de la machine.

Rapport : `infra/state/recette/<date>/rapport.md` (machine, version, réglages, verdict, temps,
mémoire), avec les mesures brutes (`timings.jsonl`, `memory.tsv`). Le verdict échoue si une
mesure a échoué, si un processus a été tué ou un service redémarré, si un service dépasse 90 %
de sa limite, si la machine descend sous 5 % de mémoire disponible, ou si un contrôle HTTPS
échoue. Sans `--audio`, la dictée utilise un signal généré : le chargement et la mémoire sont
mesurés, pas la vitesse de transcription.

**Avec une session** (recherche, document, assistant par l'API, comme le navigateur) :

1. sur un ordinateur connecté à LifeOS, outils de développement du navigateur → Stockage (ou
   Application) → Cookies → `lifeos_session` : copier la valeur ;
2. sur la VM : `(umask 077; cat > /tmp/session)`, coller, Entrée puis Ctrl-D ;
3. `infra/scripts/recette.sh --audio /tmp/lecture.m4a --session-file /tmp/session` ;
4. ensuite : `rm /tmp/session`, et **se déconnecter de ce navigateur** (la session copiée est
   révoquée).

La conversation et le document de la recette sont supprimés à la fin. Chaque message à
l'assistant est une requête Gemini (`--chat 3` par défaut, plus une pendant la charge ;
`--chat 0` : aucune).

**Avec le GPU** : faire une première mesure sans `compose.gpu.yaml`, puis
`infra/scripts/install.sh --gpu` et une seconde mesure ; comparer les lignes
« Transcription » des deux rapports.

Options : `--rounds N` (éléments par phase, 3), `--load-seconds N` (durée de la charge, 90 ;
0 : aucune), `--out DIR`. Aide : `infra/scripts/recette.sh --help`.

L'outil lui-même est vérifié par la CI (`scripts/test-recette.sh`, [exploitation.md §8](exploitation.md#8-vérifications-locales-développement))
sur une machine de test : ses chiffres n'y valent rien, seule compte la mesure sur la machine
cible.

## 3. Liste de recette

À cocher dans le journal (§4), avec la date et la version (`infra/scripts/update.sh --status`).

**Machine et services**

- [ ] `docker compose ps` : tous les services « running » ou « healthy », aucun redémarrage
  en boucle ; `grep -o -w 'avx2\|fma' /proc/cpuinfo | sort -u` affiche les deux.
- [ ] `docker compose exec ml lifeos-ml selftest` : « OCR prêt ».
- [ ] `infra/scripts/recette.sh --audio …` : code de sortie 0, rapport rangé.

**HTTPS**

- [ ] Sur chaque appareil, LifeOS s'ouvre sans avertissement de certificat.
- [ ] Mode standard : `curl -sI http://<domaine>/` répond 301 vers `https://`.
- [ ] Les contrôles HTTPS du rapport sont tous ✅ ; domaine public : note A ou mieux sur SSL
  Labs.

**Passkeys (appareils réels)**

- [ ] Enrôlement du téléphone par le lien d'enrôlement (`create-owner` ou `enrollment-token`).
- [ ] Ajout de l'ordinateur dans **Réglages → Appareils et passkeys** ; connexion sur chacun.
- [ ] Une action renforcée (autoriser l'IA d'un espace) redemande la passkey quand la
  dernière vérification date de plus de 10 minutes.
- [ ] Un appareil retiré ne peut plus se connecter ; ses sessions sont fermées.
- [ ] Secours : `lifeos-admin enrollment-token` permet d'enrôler un nouvel appareil.

**Fonctions principales**

- [ ] Tâches et rappels (un rappel arrive à l'heure), journal, mémoire.
- [ ] Assistant (Gemini) : une réponse ; le contrôle avant envoi montre ce qui sera masqué.
- [ ] Recherche par mots et par le sens (la page **Recherche** montre l'index complet).
- [ ] Un PDF de plusieurs pages et la photo d'un **vrai** ticket : texte lu, ticket proposé
  en dépense avec le bon montant et la bonne date (noter seulement « correct » ou ce qui est
  faux, jamais le contenu).
- [ ] Dictée sur le téléphone : texte correct, délai de la première dictée noté ; avec le GPU,
  `docker compose exec stt python -c "import urllib.request;
  print(urllib.request.urlopen('http://127.0.0.1:8200/healthz').read())"` indique `cuda`.
- [ ] Pendant la phase « charge » du script, l'application reste utilisable sur le téléphone.

**Exploitation**

- [ ] Exercice de panne ([exploitation.md §5](exploitation.md#exercice-de-panne-une-fois-puis-après-chaque-changement-de-canal)) :
  l'alerte reçue nomme le problème et l'action, sans donnée personnelle.
- [ ] Mise à jour puis retour arrière ([exploitation.md §6](exploitation.md#6-mise-à-jour-et-retour-arrière)) :
  `update.sh --to <version>`, `update.sh --rollback`, données intactes ; si possible vers une
  version qui ajoute une migration.
- [ ] Sauvegarde et restauration : `sudo infra/scripts/restore-check.sh` réussi, exercice de
  [restauration.md §5](restauration.md#5-exercice-de-restauration) fait au moins une fois.

## 4. Journal de recette

Une ligne par passage (le rapport complet reste dans `infra/state/recette/`, à copier hors de
la machine si on veut le garder) :

| Date | Version | Matériel (CPU / GPU) | Résultat | Remarques |
|---|---|---|---|---|
| | | | | |

Mesures à reporter dans [exploitation.md §7](exploitation.md#7-dimensionnement-vm-10-go-i7-6700-gtx-1050-ti) :

| Mesure | CPU | GPU |
|---|---|---|
| OCR : secondes par page (scan), par photo de ticket | | — |
| Première dictée (chargement du modèle compris) | | |
| Transcription : × le temps réel | | |
| Pointe de `ml`, `stt`, `postgres`, `api`, `worker` (Mio, % de la limite) | | |
| Mémoire disponible minimale de la machine pendant la charge | | |

## 5. Interpréter et ajuster

- **Pointe au-delà de ≈ 80 % de la limite** d'un service : relever sa limite, sans modifier
  `compose.yaml` (une modification locale bloque `update.sh`) : un fichier
  `infra/compose.local.yaml` non suivi par git, par exemple
  `services: { ml: { mem_limit: 3500m } }`, ajouté à la fin de `COMPOSE_FILE` dans
  `infra/.env` (par exemple `COMPOSE_FILE=compose.yaml:compose.gpu.yaml:compose.local.yaml`),
  puis `docker compose up -d`. Signaler la
  valeur pour corriger la limite par défaut.
- **Processus tué faute de mémoire** : identifier le service dans le rapport ; si la machine
  manque de mémoire dans son ensemble, désactiver ce qui sert le moins (dictée :
  `LIFEOS_STT_PROVIDER=disabled` ; recherche par le sens : `LIFEOS_EMBEDDINGS_PROVIDER=disabled`)
  ou ajouter de la RAM à la VM.
- **Transcription plus lente que le temps réel sur le CPU** : la carte graphique est
  recommandée (§2, « Avec le GPU ») ; sans elle, une dictée d'une minute prend plus d'une
  minute.
- **Contrôle HTTPS en échec** : [exploitation.md §2 et §3](exploitation.md#2-domaine-et-certificat-tls)
  (certificat, en-têtes ; un reverse proxy ne doit ni réécrire la CSP ni mettre les pages en
  cache).
- **Mesure en échec** : `docker compose logs --tail 100 <service>` au moment indiqué ; les
  mesures brutes (`timings.jsonl`) donnent l'opération et le statut HTTP.
