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
   - `.../auth/gmail.send` (envoyer des e-mails).
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

## 5. Connecter le compte

Dans LifeOS, **Réglages → Comptes connectés** :

1. Choisir l'espace (personnel ou professionnel) et cocher les usages : lire l'agenda,
   modifier l'agenda, lire les e-mails, envoyer des e-mails. Au départ, seule la lecture de
   l'agenda est cochée.
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

## 6. Déconnecter, révoquer

- **Décocher un usage** dans un espace le coupe immédiatement, sans passkey.
- **Déconnecter** révoque l'accès chez Google, efface les jetons et retire toutes les
  autorisations du compte. LifeOS demande à chaque fois s'il faut garder (choix par défaut)
  ou supprimer les données déjà synchronisées.
- Si Google ne répond pas, la déconnexion se fait quand même dans LifeOS. Retirez alors
  LifeOS des applications autorisées : <https://myaccount.google.com/permissions>.
- Google révoque tout l'accord d'un coup. Pour réduire les accès accordés chez Google,
  déconnectez puis reconnectez avec moins de cases cochées.

## 7. « Reconnexion nécessaire »

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
