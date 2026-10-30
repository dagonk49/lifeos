# ADR 0019 — Notifications sur le téléphone par Web Push (VAPID), contenu générique par défaut

- Statut : acceptée
- Date : 2026-10-28

## Contexte

Jusqu'ici, les notifications n'existaient que dans l'application : le menu
« Notifications » interroge l'API toutes les minutes, rien n'arrive quand LifeOS est fermé.
Le cahier des charges (F09) demande que les rappels et les alertes atteignent le
propriétaire sur son téléphone, sans perdre les règles de l'ADR 0015 (plages de
tranquillité, regroupement des suggestions, permission explicite des alertes critiques).

Options considérées :

1. **Application native** (Android, iOS) : notifications natives, alertes critiques iOS
   possibles, mais deux applications à publier et maintenir, des comptes développeur, un
   relais FCM/APNs signé par un compte du propriétaire. Disproportionné pour un seul
   utilisateur.
2. **ntfy ou Gotify** : un serveur de plus à héberger et sécuriser, une application tierce
   sur le téléphone, et sur iOS un relais par les serveurs publics de ntfy.
3. **Web Push** (RFC 8030, chiffrement RFC 8291, identification VAPID RFC 8292) : standard,
   intégré à Chrome, Firefox, Edge et Safari, sans application ni compte, avec le seul
   client web déjà servi.

## Décision

1. **Web Push avec VAPID.** Le navigateur s'abonne auprès de son service de push (Google
   FCM pour Chrome et Android, Apple pour Safari, Mozilla pour Firefox, Microsoft pour
   Edge) avec la clé publique du serveur ; LifeOS enregistre l'abonnement (`push_devices`).
   Le **worker** (seul processus avec une sortie vers Internet) chiffre chaque message pour
   l'appareil (`aes128gcm`, une clé éphémère P-256 par message) et le signe (jeton ES256
   valable 12 h, `aud` = origine du service de push, `sub` = `LIFEOS_VAPID_SUBJECT`). Les
   services de push ne voient que le message chiffré. Aucune dépendance nouvelle :
   chiffrement et signature sont écrits avec `cryptography` et vérifiés par le vecteur de
   test de la RFC 8291 (annexe A) ; l'envoi passe par `httpx`.
2. **Quoi envoyer** : le contrat de l'ADR 0015, `notifications.service.due(now)` (livrée,
   pas dans une synthèse, jamais une suggestion seule), plus le statut de l'application :
   seules les notifications en attente (une notification lue ou ignorée entre-temps est
   annulée). Les suggestions arrivent en **une seule notification de synthèse**. Une
   notification retenue par les plages de tranquillité part quand `deliver_after` passe,
   pas à sa création. Rien n'est poussé de ce qui était dû avant l'abonnement de l'appareil,
   ni plus de 6 heures après (`LIFEOS_PUSH_MAX_AGE_MINUTES`) : cela reste dans
   l'application.
3. **Marqueur d'envoi par appareil** : une ligne `push_deliveries` par couple
   (notification, appareil), unique ; la tâche `send_push` (chaque minute) crée les lignes
   manquantes, les réserve sous verrou `SKIP LOCKED` avec un bail de 2 minutes, envoie hors
   de toute transaction, puis enregistre l'issue. `201` : envoyé ; `404`/`410` : abonnement
   disparu, appareil révoqué (`expired`) ; `429` et `5xx`, délai dépassé : nouvel essai
   avec attente exponentielle (1 minute, doublée, 1 heure au plus) en respectant
   `Retry-After`, puis échec après `LIFEOS_PUSH_MAX_ATTEMPTS` (5). En-têtes `TTL` (6 h),
   `Topic` (identifiant de la notification : un message non remis est remplacé, jamais
   doublé).
4. **Urgence** : `high` seulement pour une alerte critique remise pendant les plages de
   tranquillité grâce à la permission `users.critical_bypass_quiet_hours` ; `normal` pour
   tout le reste (Android peut retarder ces messages en mode Doze).
5. **Écran verrouillé** : par défaut, un texte **générique** (« Nouveau rappel »,
   « Nouvelles suggestions », « Alerte »…), jamais le titre ni le corps. Par appareil, le
   propriétaire peut afficher le **titre** de la notification (jamais son corps) : c'est
   un consentement, donc une opération renforcée (passkey récente, action Guardian
   `notification.push_preview`, interdite aux agents). Un push ne contient jamais plus que
   la notification de l'application. Le message suffit à lui seul (titre, texte, chemin) :
   le service worker n'appelle pas l'API pour l'afficher.
6. **Appareils et révocation** : un appareil est lié à la session (et à la passkey) qui l'a
   enregistré. Révoquer l'appareil, se déconnecter, révoquer la session, la passkey,
   « Déconnecter les autres sessions » (tous les appareils sauf celui de la session
   courante) ou `lifeos-admin revoke-sessions` (tous) arrête les envois : les envois en
   attente sont annulés, et chaque envoi est revérifié au moment de partir. Une
   réauthentification (nouvelle session) garde les appareils. Une session simplement
   expirée ne les arrête pas : l'appareil reste un consentement distinct, révocable.
7. **Service worker** `/sw.js`, servi par Next.js lui-même (route, pas de `public/`) avec
   `Cache-Control: no-cache` : il ne gère que `push`, `notificationclick` (chemin de même
   origine seulement) et `pushsubscriptionchange` (renouvelle l'abonnement d'un appareil
   actif, n'en crée jamais un). Il ne met **rien en cache** : les pages portent un nonce
   CSP par requête et sont servies `no-store`. La CSP ajoute `worker-src 'self'` (sans
   lui, `'strict-dynamic'` bloquerait l'enregistrement) et `manifest-src 'self'`.
8. **Application installable** : manifeste `/manifest.webmanifest` et icônes PNG générées
   par le serveur (192, 512, masquable, Apple). Sur iPhone et iPad, Web Push n'est remis
   qu'à LifeOS **ajouté à l'écran d'accueil** (iOS 16.4 et plus récent).
9. **Liste blanche des services de push** : un abonnement dont l'adresse n'est pas en HTTPS
   sur `fcm.googleapis.com`, `push.apple.com`, `push.services.mozilla.com` ou
   `notify.windows.com` (et leurs sous-domaines) est refusé (`422
   push_endpoint_not_allowed`) : le worker n'appelle jamais une adresse choisie par un
   client (SSRF, T18). Liste réglable (`LIFEOS_PUSH_ALLOWED_HOSTS`).

## Conséquences

- Android (Chrome) : reçu application fermée ; l'optimisation de batterie peut retarder
  les messages `normal`. iOS : écran d'accueil obligatoire ; Apple peut retirer un
  abonnement (l'appareil passe alors `expired` au premier envoi).
- Les services de push sont des tiers (Google, Apple, Mozilla, Microsoft) : ils ne lisent
  pas le contenu, mais voient quand un message part, sa taille, et l'adresse IP du
  serveur (T39).
- L'adresse d'abonnement et ses clés sont une **capacité** (qui les détient peut écrire au
  téléphone) : jamais journalisées, auditées ni exportées (T40).
- Un push ne traverse pas le « Ne pas déranger » du téléphone (il faudrait des alertes
  critiques natives).
- Perdre la clé VAPID (`lifeos_vapid_private_key`) oblige seulement chaque téléphone à
  réactiver les notifications ; elle figure sur la fiche de récupération.
- La session `SameSite=Strict` peut ne pas accompagner l'ouverture par un appui sur la
  notification : à vérifier sur un vrai téléphone (au pire, la page de connexion s'ouvre).
- Accès par VPN seulement : la notification arrive (le message se suffit), mais l'appui
  n'ouvre rien sans le VPN.
