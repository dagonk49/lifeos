# ADR 0022 — Client mobile : l'application web installée d'abord, pas d'application native

- Statut : acceptée ; amende l'ADR 0001 (pas de `apps/mobile/`), l'ADR 0002 (passkeys du
  navigateur sur le téléphone) et l'ADR 0019 (le service worker sert aussi le partage et la
  page hors connexion)
- Date : 2026-10-30

## Contexte

L'architecture (§2) annonçait un client mobile React Native + Expo, Android d'abord, et
laissait ouvert son périmètre (§14 : notification, micro, partage de fichiers, widget). Le
cahier des charges attend du téléphone : les rappels et alertes (F09, « critique : immédiat
si risque réel »), la dictée et la lecture à voix haute (F01, F03), la photo d'un ticket de
caisse (UC02), la connexion par passkey, l'ouverture d'une page depuis une notification ou un
e-mail, et la capture rapide. Les « fonctions mobiles natives » sont rangées en V2 (§11).

Ce qui existe déjà dans le client web, au téléphone :

- passkeys dans le navigateur (`@simplewebauthn/browser`) ;
- dictée (`getUserMedia`, `MediaRecorder`, `audio/mp4` pour Safari) et lecture à voix haute
  par les voix françaises locales (`speechSynthesis`) ;
- photo du ticket par `<input capture="environment">` ;
- notifications Web Push (ADR 0019), manifeste et icônes : LifeOS s'installe sur l'écran
  d'accueil ;
- contrôles automatiques au format téléphone (360, 375 et 412 px, écran tactile) et
  WCAG 2.2 AA ([accessibilite.md](../accessibilite.md)).

Contraintes propres à LifeOS : un seul utilisateur ; serveur domestique, souvent joignable
par le réseau local ou un VPN seulement ([installation.md §3](../installation.md)) ; session
par cookie `HttpOnly`, `SameSite=Strict`, contrôle `Origin` sur toute écriture ; CSP avec un
nonce par requête et pages `no-store` ; aucune dépendance React Native ou Expo ne peut être
installée dans l'environnement de développement actuel.

### Matrice des capacités

« Web installé » = LifeOS ajouté à l'écran d'accueil depuis Chrome ou Edge (Android) ou
Safari (iOS et iPadOS 16.4 ou plus récent). « À vérifier » : à confirmer par le protocole
ci-dessous sur le téléphone du propriétaire.

| Besoin | Android, web installé | iOS, web installé | Natif (Expo) |
| --- | --- | --- | --- |
| Rappels et alertes, application fermée | oui (Web Push, ADR 0019) ; `normal` peut être retardé en Doze | oui, seulement depuis l'écran d'accueil | oui (FCM, APNs) |
| Alerte critique qui traverse « Ne pas déranger » | **non** | **non** | Android : accès « Ne pas déranger » à accorder ; iOS : droit Critical Alerts accordé par Apple |
| Dictée | oui (micro autorisé pour le site) | oui, à vérifier en mode installé | oui |
| Lecture à voix haute (voix locale) | oui si une voix française est installée | oui | oui |
| Photo d'un ticket | oui (`capture`) | oui | oui |
| « Partager → LifeOS » (photo, PDF) | **oui** (Web Share Target, cette décision) | **non** (pas de Web Share Target sur iOS) | oui (extension de partage) |
| Passkey | oui (gestionnaire de passkeys d'Android) | oui ; l'app installée a ses propres cookies : reconnexion une fois | oui, mais `assetlinks.json` / `apple-app-site-association` publics sur le domaine |
| Ouvrir une page depuis une notification | oui (même origine) | oui | oui (liens d'application) |
| Lien d'un e-mail vers LifeOS | s'ouvre dans le navigateur, pas dans l'app installée | idem | oui (App Links, Universal Links : fichiers publics) |
| Raccourcis de capture | raccourcis sur l'icône (appui long) | non | widgets, raccourcis |
| Widget d'écran d'accueil | non | non | oui (modules natifs) |
| Lecture hors connexion | non : page d'information seulement (aucune donnée stockée) | idem | possible, avec un cache chiffré à concevoir |
| Verrouillage biométrique de l'app | non (la passkey le fait à la connexion) | non | oui (`expo-local-authentication`) |
| Capture en arrière-plan | non | non | limitée par Android et iOS |

Les pages sont rendues côté client et les appels à l'API partent de la même origine : une
page ouverte depuis une notification ou un lien charge sa session normalement (le cookie
`SameSite=Strict` accompagne les appels de la page elle-même).

## Décision

1. **Pas d'application native pour l'instant.** Le client mobile est le client web,
   **installé sur l'écran d'accueil**. Pas de `apps/mobile/`, pas de dépendance Expo.
2. **Service worker sur toutes les pages** (`/sw.js`, enregistré par
   `components/ServiceWorkerRegistration.tsx`, plus seulement à l'activation du push). Il ne
   **stocke rien** : ni Cache Storage, ni IndexedDB, ni `importScripts`. En plus du push
   (ADR 0019) :
   - **navigations** (GET, même origine) : passées au réseau telles quelles, par le
     préchargement de navigation (une seule requête, avec les cookies et en-têtes du
     navigateur ; nonce et `no-store` intacts). Seulement si le réseau échoue, une **page
     d'information fixe** construite par le worker : « LifeOS n'est pas joignable », sans
     script ni donnée, avec sa propre CSP (`default-src 'none'`) et un lien « Réessayer » ;
   - **partage** : le manifeste déclare `share_target` (POST `multipart/form-data` vers
     `/partage`, fichiers PDF, images, texte : les types que l'envoi de documents accepte).
     Le worker garde les fichiers **en mémoire**, au plus 2 minutes, les remet une seule fois
     à la page Documents, puis les oublie. Le propriétaire y choisit le type (document ou
     ticket de caisse) et l'espace, puis envoie : l'envoi suit exactement le chemin habituel
     (`POST /v1/documents`, contrôle `Origin`, idempotence, antivirus, chiffrement). Rien
     n'est envoyé avant son clic. Sans worker actif, `/partage` (route Next.js) ne garde rien
     et renvoie vers Documents, qui demande de choisir le fichier à nouveau ;
   - les autres requêtes (scripts, styles, images, API) ne passent pas par lui.
3. **Raccourcis** du manifeste (appui long sur l'icône, Android) : nouvelle tâche, assistant,
   envoi d'un document.
4. **Installation guidée** : Réglages → « Installer LifeOS sur le téléphone » propose la
   boîte d'installation de Chrome ou Edge quand elle existe, sinon les étapes (Safari :
   Partager, « Sur l'écran d'accueil » ; autres : menu du navigateur).
5. **CSP inchangée** pour les pages : `worker-src 'self'` et `manifest-src 'self'` existaient
   déjà (ADR 0019) ; aucune source externe ajoutée.
6. **Déclencheur d'un client natif** : un besoin confirmé par le propriétaire que le web ne
   couvre pas — alerte critique qui traverse « Ne pas déranger », partage depuis iOS, widget,
   lecture hors connexion, verrouillage biométrique — **et** un accès au serveur qui permet
   les fichiers d'association publics (`/.well-known/assetlinks.json`,
   `apple-app-site-association`) avec un certificat reconnu. Le cadrage se fera alors dans
   un document dédié (Expo et EAS, Android d'abord, `packages/shared` extrait de `src/lib`,
   passkeys Credential Manager avec l'origine `android:apk-key-hash:`, jeton de session en
   stockage sécurisé et contrôle `Origin` adapté, liens d'application, FCM ou UnifiedPush,
   tests, distribution), sans développement avant décision.

## Conséquences

- Aucun stockage local de données personnelles : hors connexion ou hors VPN, LifeOS ne
  montre que la page d'information. Une lecture hors connexion demanderait un cache chiffré
  et une nouvelle décision.
- Le service worker intercepte les navigations : une erreur dans son code pourrait gêner
  toutes les pages. Il est volontairement court, testé (`src/lib/serviceWorker.test.ts`) et
  servi `no-cache` (une correction arrive au chargement suivant). Les réglages du site dans
  le navigateur permettent de le supprimer.
- Les fichiers partagés ne vivent que dans la mémoire du worker puis de la page : fermer la
  page avant l'envoi les perd (il faut les repartager). Sur iOS, le partage reste
  « Fichier » dans la page Documents.
- Aucun changement du backend : ni origine Android dans WebAuthn, ni jeton hors cookie.
- Les limites de l'ADR 0019 restent : pas de « Ne pas déranger » traversé, notifications
  iOS seulement depuis l'écran d'accueil, appui sans effet hors VPN.

### Protocole sur le téléphone (à faire par le propriétaire)

Résultats à noter dans [accessibilite.md](../accessibilite.md) (appareils réels) :

1. Installer LifeOS (Réglages → « Installer LifeOS sur le téléphone »), l'ouvrir depuis
   l'icône ; se connecter par passkey en mode installé.
2. Dicter un message (autorisation du micro en mode installé) ; lire une réponse à voix
   haute (voix française locale).
3. Photographier un ticket depuis Finances ; partager une photo de la galerie vers LifeOS
   (Android) et l'envoyer comme ticket de caisse.
4. Activer les notifications, envoyer un test : application fermée, écran verrouillé, puis
   « Ne pas déranger » actif ; toucher la notification (ouverture de `/tasks` ou
   `/approvals`).
5. Couper le Wi-Fi et les données (ou le VPN) : la page « LifeOS n'est pas joignable »
   s'affiche ; « Réessayer » une fois le réseau revenu.
6. Durée de session : reconnexion demandée après 7 jours sans usage, 30 jours au plus.

## Alternatives écartées

- **Application Expo / React Native maintenant** : deux applications à publier et maintenir,
  comptes développeur (Google Play, Apple Developer), changements d'authentification côté
  serveur (origine Android, jeton hors cookie, contrôle `Origin`), fichiers d'association
  publics incompatibles avec un domaine réservé au réseau local ou au VPN ; et aucune
  dépendance téléchargeable ici. Disproportionné tant qu'aucun besoin natif n'est confirmé.
- **Cache hors connexion des pages ou des données** (Workbox, Cache Storage) : une page en
  cache rejouerait un ancien nonce CSP, et des données personnelles resteraient en clair sur
  le téléphone.
- **Partage reçu par le serveur Next.js** (sans worker) : il faudrait que le serveur web
  reçoive et transmette les fichiers à l'API hors du chemin d'envoi habituel ; le worker les
  remet à la page, qui envoie comme d'habitude.
- **Enveloppe native (Capacitor, TWA)** : une TWA Android ajoute seulement une fiche Play
  Store et `assetlinks.json` public, pour les mêmes capacités que le web installé ;
  Capacitor ramène la maintenance d'une application native.
