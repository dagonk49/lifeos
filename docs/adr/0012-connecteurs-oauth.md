# ADR 0012 — Comptes connectés : OAuth 2.0 avec PKCE, jetons chiffrés, consentement par espace

- Statut : acceptée
- Date : 2026-10-20

## Contexte

L'étape 5 (README) relie LifeOS à l'agenda et à la messagerie du propriétaire. Le cahier des
charges demande une synchronisation « seulement avec consentement et connecteur compatible »
(F03) et l'historique de chaque action : initiateur, données consultées, autorisation,
résultat (F08). Le modèle de menaces prévoit pour cette étape le vol de jetons OAuth (T19) et
les requêtes forgées vers d'autres hôtes (SSRF, T18).

Le propriétaire a choisi un premier fournisseur : **Google** (Google Agenda et Gmail, par
OAuth 2.0). D'autres pourront suivre (Microsoft 365, CalDAV et IMAP).

Contraintes propres à LifeOS :

- une seule personne, un serveur chez elle, joint en VPN ou derrière un reverse proxy, sans
  point d'entrée public garanti ;
- le cookie de session est `SameSite=Strict` (T2, T3) : le navigateur ne l'envoie pas sur
  une redirection venue d'un autre site, comme le retour de la page de consentement de Google ;
- Google n'accepte comme adresse de retour qu'une URL HTTPS sous un domaine public ;
- les tâches suivantes (synchronisation de l'agenda, conflits, écriture dans l'agenda,
  lecture puis envoi d'e-mails) doivent se brancher sur ce socle sans le refaire.

## Décision

1. **Un module `connectors`, indépendant du fournisseur.**
   - Le protocole `OAuthProvider` couvre la page de consentement, l'échange du code, le
     renouvellement et la révocation.
   - `CalendarAdapter` et `MailAdapter` recevront les méthodes de l'agenda et de la
     messagerie (tâches 14 à 18).
   - Une hiérarchie d'erreurs commune indique si une nouvelle tentative a un sens
     (`retryable`) : compte à reconnecter, quota, fournisseur injoignable, conflit, objet
     disparu, curseur expiré, issue inconnue d'une écriture.
   - `LIFEOS_CONNECTORS_PROVIDER` choisit le fournisseur : `disabled` (par défaut), `google`,
     ou `fake`, un fournisseur en mémoire pour les tests et les parcours de bout en bout,
     sans réseau.
   - Le contrat tient compte de Microsoft (jetons de renouvellement qui changent à chaque
     renouvellement, pas de révocation par jeton pour les comptes personnels) et de
     CalDAV/IMAP (mot de passe d'application scellé de la même façon, garde SSRF sur des
     hôtes choisis par l'utilisateur).
2. **Flux « code d'autorisation » avec PKCE (S256), client confidentiel de type
   « Application Web ».**
   - `POST /v1/connectors/oauth/start` est une opération renforcée (connexion par passkey de
     moins de 10 minutes). LifeOS tire un `state` de 256 bits et n'en garde que l'empreinte.
     Il scelle le vérificateur PKCE. L'état ne sert qu'une fois, pendant 10 minutes
     (`LIFEOS_OAUTH_STATE_MINUTES`). L'API renvoie l'URL de la page de consentement.
   - Google renvoie le navigateur vers la page web **`/connexions/retour`**, pas vers l'API.
     Le cookie `SameSite=Strict` n'accompagne pas la redirection de Google, mais il
     accompagne la requête que cette page envoie ensuite au même site. La page retire le
     code de la barre d'adresse et de l'historique, puis le transmet **une fois** à
     `POST /v1/connectors/oauth/callback`.
   - L'API réclame l'état dans une transaction à part : même propriétaire, non expiré, pas
     encore utilisé. Un code rejoué tombe donc sur un état déjà consommé. Elle échange le
     code avec le vérificateur, enregistre les scopes **réellement accordés** (le
     propriétaire peut en décocher chez Google) et ne crée de consentements que pour ces
     usages-là.
   - Paramètres Google : `access_type=offline` (jeton de renouvellement),
     `include_granted_scopes=true` (consentement incrémental), `login_hint` pour un compte
     déjà connu, `prompt=consent` seulement quand LifeOS n'a pas de jeton de renouvellement
     valable.
   - L'identité du compte (`sub`, adresse) vient du jeton d'identité reçu directement du
     point de jeton de Google, en TLS. OpenID Connect permet alors de se passer de la
     vérification de signature ; l'émetteur, l'audience et l'expiration sont vérifiés.
3. **Scopes minimaux, par usage.**

   | Usage | Scopes Google |
   |---|---|
   | (identité, toujours) | `openid`, `userinfo.email` |
   | `calendar.read` | `calendar.calendarlist.readonly`, `calendar.events.readonly` |
   | `calendar.write` (implique la lecture) | `calendar.events` |
   | `mail.read` | `gmail.readonly` (scope « restreint ») |
   | `mail.send` | `gmail.send` |

   `calendar.readonly` donnerait plus que nécessaire. `gmail.metadata` ne permet pas de
   chercher (`q`), dont la lecture des e-mails aura besoin.
4. **Jetons scellés avec une clé à part.**
   - AES-256-GCM, avec le format `LOS1` déjà utilisé pour les originaux des documents. Le
     code est mis en commun dans `core/crypto.py`.
   - La clé est le secret Docker `lifeos_connectors_key`, distinct de la clé des documents.
   - Les données associées sont l'identifiant du compte et la nature du jeton : un jeton
     recopié sur un autre compte ou dans une autre colonne ne se déchiffre pas.
   - Le secret peut contenir un trousseau `id:clé,…` pour une rotation : la première clé
     scelle, toutes ouvrent, et un jeton est scellé de nouveau au renouvellement suivant.
   - Aucun jeton, chiffré ou non, ne sort par l'API, les journaux, l'audit ni l'export.
   - Perdre cette clé oblige seulement à reconnecter les comptes. Elle peut figurer sur la
     fiche de récupération, mais jamais avec les sauvegardes : clé et sauvegarde ensemble
     rendraient les jetons lisibles.
5. **Renouvellement sous verrou de ligne.**
   - `tokens.access_token` rend le jeton d'accès en cache s'il reste valable plus de
     60 secondes.
   - Sinon, il le renouvelle sous `SELECT … FOR UPDATE` : l'API et le worker ne
     renouvellent jamais le même compte en même temps (un fournisseur qui change le jeton
     de renouvellement à chaque fois invaliderait l'un des deux). Un nouveau jeton de
     renouvellement est toujours enregistré.
   - Si le fournisseur refuse (`invalid_grant`), ou si la clé ne déchiffre plus le jeton, le
     compte passe à `reauth_required` : jetons effacés, une notification « importante » par
     connexion, échec audité (code de motif seulement).
6. **Consentement par compte, par espace et par usage** (`connector_grants`).
   - Dans Réglages > Comptes connectés, le propriétaire choisit quels usages de quel compte
     servent dans quel espace.
   - Accorder est renforcé (passkey récente), retirer est immédiat, comme pour les
     permissions d'agent.
   - Les agendas et libellés de chaque compte (`connector_sources`) sont rattachés à un
     espace par le propriétaire, parmi les espaces où l'usage est autorisé (tâches 14 et 17).
   - Chaque synchronisation et chaque action passent d'abord par `require_capability`. Il
     faut un compte actif, le consentement de **cet** espace pour **cet** usage, et le scope
     accordé par le fournisseur. Un agent a besoin en plus de sa propre permission sur
     `calendar` ou `email` (nouvelles ressources de `agent_permissions`). Hors des espaces du
     principal, la réponse est 404, comme partout ailleurs.
7. **Déconnexion immédiate, sans réauthentification.**
   - LifeOS révoque l'accès chez le fournisseur, avec le jeton dans le corps de la requête,
     jamais dans l'URL.
   - Puis il efface les jetons, même si la révocation a échoué. Le compte passe à `revoked`,
     les consentements sont retirés et les sources désactivées. Les approbations en attente
     qui utiliseraient le compte expirent ; les consentements en cours chez ce fournisseur (états OAuth) sont supprimés.
   - Le propriétaire choisit **à chaque déconnexion** de garder les données déjà
     synchronisées (choix par défaut) ou de les supprimer, par cascade depuis les sources.
   - Si la révocation a échoué, l'interface lui demande de retirer LifeOS des applications
     autorisées de son compte.
8. **Appels réseau bornés (T18).**
   - Seuls les hôtes d'une liste blanche codée en dur sont appelés, en HTTPS, avec un délai
     explicite et sans suivre de redirection.
   - Les corps de réponse ne sont ni journalisés ni recopiés dans une erreur : au plus le
     code `error` de Google, s'il s'agit d'un simple identifiant.
   - En défense en profondeur, un filtre des journaux masque toute valeur qui ressemble à un
     jeton ou à un code (`ya29.`, `1//`, JWT, `code=`, `access_token=`…).
9. **Synchronisation par interrogation périodique** (tâches 14 et 17). Les notifications
   poussées (Calendar `watch`, Gmail Pub/Sub) exigent une URL publique, qu'une installation
   en VPN n'a pas.
10. **Agenda : relecture complète d'une fenêtre glissante** (tâche 14, `calendar/sync.py`).
    - Chaque agenda suivi est relu toutes les 15 minutes sur `[J-30, J+365]`, séries
      développées par Google (`singleEvents=true`) dans leur propre fuseau : chaque
      occurrence a un identifiant stable, et son heure locale reste juste au changement
      d'heure. LifeOS stocke des instants UTC (ou des dates pour la journée entière) et ne
      développe aucune règle de récurrence lui-même.
    - Une occurrence est unique par source et identifiant : une relecture la met à jour,
      jamais en double. Chaque page est validée avec le jeton de la page suivante : un
      passage interrompu reprend là où il s'est arrêté. Seul un passage complet supprime ce
      que Google ne renvoie plus (annulé, supprimé, sorti de la fenêtre) ; un passage en
      échec ne supprime rien.
    - Les jetons de synchronisation incrémentale de Google (`syncToken`) ne se combinent pas
      avec une fenêtre (`timeMin`/`timeMax`) ; sans fenêtre, une série infinie serait
      développée sans fin. La relecture d'une fenêtre coûte quelques requêtes par agenda et
      par passage (250 occurrences par page) : négligeable pour un usage personnel. Le
      `syncToken` reste possible plus tard si un agenda très chargé le demande.

## Conséquences

- Le propriétaire sait à tout moment quels comptes sont autorisés, quels usages Google a
  réellement accordés et dans quels espaces chacun sert. L'export de ses données les liste,
  sans les jetons.
- Il faut un **domaine public** pour Google : un nom en `.lan` est refusé. Le domaine n'a
  pas besoin d'être joignable depuis Internet, seul le navigateur du propriétaire y revient.
  Une installation sur un domaine purement local ne peut pas connecter Google telle quelle.
- L'application Google n'est pas validée par Google. En mode « Test », les jetons de
  renouvellement expirent au bout d'environ 7 jours : il faut la passer « en production »
  et accepter l'avertissement « application non validée ». `gmail.readonly` est un scope
  restreint. Ces règles de Google changent : à revérifier lors de la mise en service
  ([guide-google.md](../guide-google.md)).
- Google révoque tout l'accord d'un coup. Pour réduire les usages accordés chez Google, il
  faut déconnecter puis reconnecter avec moins de cases cochées. Dans LifeOS, décocher un
  usage dans un espace suffit à le couper.
- Une nouvelle clé à conserver (facultative) : `lifeos_connectors_key`.
- Le fournisseur factice permet de tester tout le parcours, dans pytest comme dans le
  navigateur, sans compte Google ni réseau.

## Options écartées

- **Retour OAuth directement sur l'API (GET)** : le cookie `SameSite=Strict` n'y est pas
  envoyé. Passer le cookie en `Lax` affaiblirait la protection contre le CSRF (T3).
- **Client « installé » ou flux pour appareils** : client public sans secret, et le flux
  pour appareils n'accepte qu'une liste réduite de scopes.
- **Jetons en clair, ou chiffrés avec la clé des documents** : des clés séparées limitent
  l'effet d'une fuite, et l'on peut perdre l'une sans l'autre.
- **Une bibliothèque OAuth** : une dépendance de plus pour un flux court et entièrement
  testé ; httpx était déjà verrouillé.
- **Notifications poussées** : elles demandent une URL publique (voir 9).
