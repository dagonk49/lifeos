# Connecter un compte Google (Agenda et Gmail)

Objectif : permettre à LifeOS d'accéder à votre agenda Google et à votre boîte Gmail par
OAuth 2.0, avec les accès que **vous** choisissez, espace par espace
([ADR 0012](adr/0012-connecteurs-oauth.md)). LifeOS ne voit jamais votre mot de passe
Google : il reçoit des jetons, chiffrés sur votre serveur, que vous pouvez révoquer à tout
moment.

Les règles de Google (écran de consentement, validation, durée des jetons) changent
régulièrement. Vérifiez chaque étape dans la console Google Cloud au moment de la mise en
service.

## 0. Prérequis : un domaine public

Google n'accepte comme adresse de retour qu'une URL **HTTPS** sous un **domaine public**,
par exemple `https://lifeos.mondomaine.fr/connexions/retour`.

- Un domaine que vous possédez convient, même s'il n'est résolu que par votre DNS local et
  que la VM reste injoignable depuis Internet : seul votre navigateur revient sur cette
  adresse.
- Un nom interne (`lifeos.maison.lan`, certificat `mkcert`) **ne convient pas** : Google
  refuse ces adresses.

L'adresse de retour est toujours `LIFEOS_PUBLIC_ORIGIN` + `/connexions/retour`.

## 1. Un projet Google Cloud

1. Ouvrir <https://console.cloud.google.com/> avec le compte Google qui administrera
   l'application (le vôtre).
2. Créer un projet dédié, par exemple « LifeOS connecteurs ». Mieux vaut le séparer du
   projet de la clé Gemini : aucun compte de facturation n'est nécessaire pour l'un comme
   pour l'autre.
3. « API et services » → « Bibliothèque » : activer **Google Calendar API** et **Gmail API**.

## 2. L'écran de consentement

« API et services » → « Écran de consentement OAuth » (ou « Google Auth Platform ») :

1. Type d'utilisateurs : **Externe** (un compte Gmail personnel ne peut pas choisir
   « Interne »).
2. Nom de l'application : « LifeOS » ; adresse d'assistance et adresse du développeur : la
   vôtre.
3. Domaines autorisés : le domaine enregistré de LifeOS (ex. `mondomaine.fr`).
4. Accès aux données (scopes) : ajouter ceux dont vous aurez besoin, pas plus :
   - `openid` et `.../auth/userinfo.email` (identité du compte, toujours) ;
   - `.../auth/calendar.calendarlist.readonly` et `.../auth/calendar.events.readonly`
     (lire l'agenda) ;
   - `.../auth/calendar.events` (modifier l'agenda) ;
   - `.../auth/gmail.readonly` (lire les e-mails, scope « restreint ») ;
   - `.../auth/gmail.send` (envoyer des e-mails : l'envoi n'est **pas encore disponible**
     dans cette version, inutile de l'ajouter pour l'instant).
5. Utilisateurs de test : ajouter votre ou vos adresses Google.
6. **Publier l'application** (« En production »). En mode « Test », Google fait expirer les
   jetons de renouvellement au bout d'environ 7 jours, et LifeOS demanderait de reconnecter
   le compte chaque semaine. Pour un usage personnel, l'application reste « non validée » :
   Google affiche un avertissement à la connexion (voir §5). Inutile de demander une
   validation.

Compte Google Workspace professionnel : vérifiez avec son administrateur que les
applications tierces non validées et l'accès à Gmail sont autorisés.

## 3. L'identifiant client

« API et services » → « Identifiants » → « Créer des identifiants » → « ID client OAuth » :

1. Type d'application : **Application Web**.
2. Nom : « LifeOS ».
3. Origines JavaScript autorisées : aucune.
4. URI de redirection autorisés : **`https://lifeos.mondomaine.fr/connexions/retour`**
   (votre `LIFEOS_PUBLIC_ORIGIN` suivi de `/connexions/retour`, à l'identique).
5. Créer, puis copier l'**ID client** et le **code secret du client**.

## 4. Les donner à LifeOS

```bash
cd /opt/lifeos/infra
./scripts/init-secrets.sh            # crée la clé des jetons (lifeos_connectors_key) si besoin
printf '%s' 'CODE_SECRET_DU_CLIENT' > secrets/lifeos_google_client_secret   # jamais dans .env
```

Dans `infra/.env` :

```dotenv
LIFEOS_CONNECTORS_PROVIDER=google
LIFEOS_GOOGLE_CLIENT_ID=123456789-xxxxxxxx.apps.googleusercontent.com
```

Puis `docker compose up -d`. Sans la clé des jetons ou sans le client Google, l'API refuse de
démarrer avec un message explicite (`docker compose logs api`).

Réglages facultatifs, dans `infra/.env` (valeurs par défaut entre parenthèses ; liste complète
dans [exploitation.md §1](exploitation.md#1-première-installation)) :

| Variable | Rôle |
|---|---|
| `LIFEOS_CALENDAR_SYNC_MINUTES` | chaque agenda suivi est relu toutes les N minutes (15 ; de 5 à 1 440) |
| `LIFEOS_CALENDAR_PAST_DAYS`, `LIFEOS_CALENDAR_FUTURE_DAYS` | fenêtre copiée : jours passés (30) et à venir (365) |
| `LIFEOS_MAIL_SYNC_MINUTES` | chaque libellé suivi est relu toutes les N minutes (15 ; de 5 à 1 440) |
| `LIFEOS_MAIL_RETENTION_DAYS` | e-mails copiés : fenêtre du premier passage et durée de conservation de chaque copie (90 ; de 1 à 365) |
| `LIFEOS_CONNECTORS_TIMEOUT_SECONDS` | délai de chaque appel à Google (15 s) |
| `LIFEOS_OAUTH_STATE_MINUTES` | délai pour revenir de la page de Google (10 min) |

Le planificateur vérifie toutes les 5 minutes quels agendas et libellés sont à relire : une
valeur sous 5 minutes n'aurait pas d'effet.

**Pare-feu sortant** : l'API et le worker n'appellent que `oauth2.googleapis.com` (jetons,
révocation), `www.googleapis.com` (Agenda) et `gmail.googleapis.com` (Gmail), en HTTPS.
`accounts.google.com` n'est ouvert que par votre navigateur, pendant la connexion.

## 5. Connecter le compte

Dans LifeOS, **Réglages → Comptes connectés** :

1. Choisir l'espace (personnel ou professionnel) et cocher les usages : lire l'agenda,
   modifier l'agenda, lire les e-mails, envoyer des e-mails. Au départ, seule la lecture de
   l'agenda est cochée. « Envoyer des e-mails » peut être accordé, mais LifeOS n'envoie encore
   aucun e-mail dans cette version.
2. « Connecter un compte Google » : LifeOS demande une confirmation par passkey si la
   dernière date de plus de 10 minutes.
3. Chez Google : choisir le compte. Avertissement « Google n'a pas validé cette
   application » : « Paramètres avancés » → « Accéder à LifeOS (non sécurisé) ». Ce message
   signifie seulement que Google n'a pas audité votre propre installation.
4. Google liste les accès demandés : vous pouvez en **décocher**. LifeOS n'utilise que ce que
   vous laissez coché, et vous le dit au retour.
5. Retour sur LifeOS : la page affiche le compte connecté et ce qui est autorisé dans
   l'espace choisi.

Ensuite, dans le même panneau, cochez ou décochez chaque usage espace par espace. Un usage
que Google n'a pas encore accordé ouvre de nouveau la page de Google, pour cet accès
seulement.

## 6. Choisir ce que LifeOS copie

Une fois le compte connecté, rien n'est copié tant que vous n'avez pas choisi quoi suivre,
toujours dans **Réglages → Comptes connectés** :

- **Choisir les agendas à suivre** : pour chaque agenda du compte, l'espace qu'il alimente
  (seuls les espaces où « Lire l'agenda » est autorisé sont proposés). LifeOS copie
  30 jours passés et un an à venir, et relit chaque agenda environ toutes les 15 minutes ;
  **Synchroniser** le relit tout de suite. Les événements apparaissent dans la page
  **Agenda** et sur l'accueil.
- **Modifier l'agenda** : créer, déplacer ou annuler un événement passe **toujours** par une
  approbation, avec l'aperçu exact du changement ([ADR 0016](adr/0016-ecritures-externes-sous-approbation.md)).
- **Choisir les libellés à suivre** (Gmail) : pour chaque libellé (Boîte de réception,
  Importants, vos libellés…), l'espace qu'il alimente, parmi ceux où « Lire les e-mails » est
  autorisé. Le scope `gmail.readonly` ouvre toute la boîte chez Google : c'est ce choix qui
  limite ce que LifeOS copie. Seuls les 90 derniers jours sont copiés, en texte brut ;
  chaque copie est supprimée 90 jours après sa réception, ou dès que l'e-mail quitte le
  libellé ([ADR 0017](adr/0017-lecture-des-e-mails.md)).
- **Ne plus suivre** un agenda ou un libellé supprime ses copies dans LifeOS, jamais chez
  Google.

Le détail de chaque page est dans le [guide d'utilisation](guide-utilisateur.md#réglages).

## 7. Déconnecter, révoquer

- **Décocher un usage** dans un espace le coupe immédiatement, sans passkey.
- **Déconnecter** révoque l'accès chez Google, efface les jetons et retire toutes les
  autorisations du compte. LifeOS demande à chaque fois s'il faut garder (choix par défaut)
  ou supprimer les données déjà synchronisées.
- Si Google ne répond pas, la déconnexion se fait quand même dans LifeOS. Retirez alors
  LifeOS des applications autorisées : <https://myaccount.google.com/permissions>.
- Google révoque tout l'accord d'un coup. Pour réduire les accès accordés chez Google,
  déconnectez puis reconnectez avec moins de cases cochées.

## 8. « Reconnexion nécessaire »

Le compte passe à « Reconnexion nécessaire » quand Google refuse de renouveler l'accès.
LifeOS efface alors les jetons et envoie une notification. Causes connues (à revérifier dans
la documentation de Google) :

- application restée en mode « Test » (jetons valables environ 7 jours) ;
- accès retiré dans <https://myaccount.google.com/permissions> ;
- mot de passe du compte Google changé (pour les accès Gmail) ;
- compte inutilisé pendant 6 mois ;
- plus de 100 jetons de renouvellement pour le même compte et le même client (Google
  invalide les plus anciens) ;
- clé `lifeos_connectors_key` perdue ou remplacée (restauration sans la clé).

Cliquer « Reconnecter le compte » : les autorisations par espace sont conservées.

## Problèmes fréquents

| Symptôme | Cause probable |
|---|---|
| Google affiche « Erreur 400 : redirect_uri_mismatch » | l'URI de redirection du client (§3) n'est pas exactement `LIFEOS_PUBLIC_ORIGIN` + `/connexions/retour` |
| Google affiche « Accès bloqué : … n'a pas terminé la procédure de validation » | application en mode « Test » et adresse absente des utilisateurs de test (§2) |
| « Les comptes connectés ne sont pas configurés sur ce serveur » | `LIFEOS_CONNECTORS_PROVIDER` vaut `disabled`, ou clé des jetons absente |
| « Cette demande de connexion a expiré ou a déjà servi » | plus de 10 minutes passées chez Google, ou page de retour rechargée : recommencer |
| « L'accès n'a pas été accordé chez le fournisseur » | consentement refusé ou fenêtre de Google fermée |
| Reconnexion demandée chaque semaine | application restée en mode « Test » (§2, étape 6) |
| « Fournisseur non configuré sur ce serveur » sous un agenda ou un libellé | `LIFEOS_CONNECTORS_PROVIDER` est repassé à `disabled` (par exemple après `restore-drill.sh`) : remettre `google` (§4) |
| « Lecture … non autorisée dans cet espace : synchronisation arrêtée » | l'usage a été décoché pour cet espace : le recocher, ou ne plus suivre cet agenda ou ce libellé |
| « Lecture … non accordée chez le fournisseur » | l'accès a été décoché chez Google : recocher l'usage dans LifeOS, qui rouvre la page de Google |
| « Trop de requêtes chez le fournisseur : nouvel essai automatique » | quota de Google atteint : LifeOS réessaie seul au passage suivant |
| Un agenda ou des e-mails ne se mettent plus à jour, sans message | worker ou planificateur arrêté : [dépannage](depannage.md#3-tâches-de-fond-rappels-et-synchronisations) |
| « Fournisseur injoignable : nouvel essai automatique » qui ne disparaît pas | Google Calendar API ou Gmail API non activée dans le projet (§1, étape 3), ou pare-feu sortant (§4) : `docker compose logs worker`, qui ne montre que le code d'erreur |
