# Accessibilité et usage mobile

Objectif : **WCAG 2.2 niveau AA** sur toutes les pages du client web, au clavier, avec un
lecteur d'écran, en thème clair et sombre, sur ordinateur et sur téléphone (cahier des
charges §8). Ce document décrit ce qui est vérifié automatiquement, le protocole manuel et le
tableau des résultats sur les vrais appareils, que le propriétaire remplit.

## 1. Ce qui est vérifié automatiquement

| Vérification | Où | Quand |
|---|---|---|
| Contrastes des jetons de couleur (texte 4,5:1, bordures de champ et contour de focus 3:1), calculés depuis la palette Tailwind installée | `apps/web/src/lib/contrast.test.ts` | `pnpm test` (CI) |
| Composants : champ décrit par son aide (`aria-describedby`), onglets (flèches, Début, Fin, `aria-selected`), panneau des notifications (Échap, clic extérieur, focus rendu, « Tout marquer comme lu »), tâche en retard dite en toutes lettres, Entrée du compositeur selon le pointeur | `src/components/*.test.tsx` | `pnpm test` (CI) |
| Chaque page, thème clair puis sombre, ordinateur et téléphone : langue, titre, un `main` et un `h1`, nom accessible de chaque champ, bouton et lien, identifiants uniques et références `aria-*` valides, contraste du texte et des placeholders, bordures des champs, taille des cibles (24 px ou espacement), zones défilantes atteignables au clavier | `apps/web/e2e/a11y.spec.ts` (contrôleur : `e2e/a11y-audit.ts`) | `pnpm e2e`, projets `chromium` et `mobile-chromium` |
| Clavier seul : lien d'évitement, navigation, titre de page annoncé, notifications (Entrée, Échap, focus rendu), onglets de la mémoire, compositeur (Maj+Entrée, Entrée), contrôle avant l'envoi annulé par Échap ; instantané ARIA de l'en-tête et de la navigation | `apps/web/e2e/keyboard.spec.ts` | `pnpm e2e` |
| Téléphone : aucune page plus large que l'écran à 360 et 375 px, panneau des notifications dans l'écran ; parcours au toucher (connexion par passkey, tâche, message envoyé par le bouton, dictée, photo de ticket avec `capture="environment"`) | `apps/web/e2e/mobile.spec.ts` | `pnpm e2e`, projet `mobile-chromium` (Pixel 7) |

Le contrôleur `e2e/a11y-audit.ts` est écrit à la main : `axe-core` (`@axe-core/playwright`)
n'est pas encore une dépendance du projet. Il couvre les règles ci-dessus, pas toutes celles
d'axe. Ajouter axe (version épinglée, `pnpm audit`) puis remplacer `audit()` par
`new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])`
est la prochaine étape. Toute exception (`EXCEPTIONS` dans `a11y-audit.ts`) porte sa
justification écrite ; la liste est vide.

Lancer seulement ces contrôles :

```bash
cd apps/web
pnpm build
pnpm e2e a11y.spec.ts keyboard.spec.ts mobile.spec.ts
pnpm e2e --project=mobile-chromium      # le téléphone seul
```

Ce que l'automatique ne remplace pas : la restitution réelle par un lecteur d'écran, les
passkeys sur iOS (WebKit n'a pas d'authentificateur virtuel), le micro et les voix de
l'appareil, l'appareil photo. D'où le protocole ci-dessous.

## 2. Règles de l'interface

- Chaque page a son titre « Page · LifeOS » (`PageHeader`), lu par l'annonceur de route de
  Next.js à chaque navigation.
- Le premier élément atteint au clavier est « Aller au contenu », qui donne le focus au
  contenu (`main#contenu`). L'en-tête est collant à partir de 640 px de large ; sur un
  téléphone il défile avec la page et ne cache jamais l'élément qui a le focus.
- Le contour de focus (teal) est visible sur tout élément atteint au clavier.
- Une information n'est jamais donnée par la seule couleur : une tâche en retard porte le
  badge « En retard », les états ont un libellé.
- Chaque champ a une étiquette visible ; son aide est liée au champ (`aria-describedby`).
- Les cibles tactiles font au moins 24 × 24 px (cases à cocher des tâches comprises).
- Les animations s'arrêtent si le système demande moins de mouvement.
- Thème sombre : il suit le réglage de l'appareil ; le texte secondaire (`text-muted`) y
  garde un contraste d'au moins 4,5:1.

Raccourcis :

| Où | Touche | Effet |
|---|---|---|
| Partout | Tab / Maj+Tab | Élément suivant / précédent |
| Panneau « Notifications » | Échap | Ferme le panneau, le focus revient au bouton |
| Onglets (Mémoire) | ← → , Début, Fin | Onglet précédent, suivant, premier, dernier |
| Message à l'assistant, sur ordinateur | Entrée / Maj+Entrée | Envoie / va à la ligne |
| Message à l'assistant, sur écran tactile | Entrée | Va à la ligne (le bouton « Envoyer » envoie) |
| Contrôle « Avant l'envoi » | Échap | Annule, le message reste tel quel |

## 3. Protocole manuel

À dérouler à chaque version majeure, et sur chaque nouvel appareil cible. Noter le résultat
dans le tableau du §4 (« OK », « KO » avec un mot d'explication, ou « — » si non applicable).

1. **Clavier seul** (ordinateur, sans souris) : se connecter, aller dans chaque page par la
   navigation, créer une tâche, la terminer, envoyer un message, ouvrir et fermer les
   notifications, changer d'onglet dans Mémoire, approuver une proposition. Le focus est
   toujours visible, jamais caché par l'en-tête, jamais perdu après la fermeture d'un panneau.
2. **NVDA avec Firefox ou Chrome** (Windows) : chaque page annonce son titre en arrivant ; les
   boutons d'une ligne disent l'élément concerné (« Supprimer « … » ») ; les messages d'erreur
   et d'état sont lus sans déplacer le focus ; le formulaire de tâche se remplit en
   n'entendant que les étiquettes et les aides.
3. **VoiceOver sur iOS** (Safari) : mêmes vérifications au balayage ; rotor « En-têtes » et
   « Formulaires » utilisables ; la passkey s'enregistre et se connecte.
4. **TalkBack sur Android** (Chrome) : mêmes vérifications ; double tapotement sur la case
   d'une tâche ; dictée dans l'assistant.
5. **Zoom 200 % et reflux à 320 px** : zoom du navigateur à 200 %, puis fenêtre de 320 px
   de large (ou téléphone en mode texte agrandi) ; aucune page ne défile horizontalement,
   aucun texte n'est coupé.
6. **Thème sombre** : passer l'appareil en sombre ; tout le texte se lit, les champs ont une
   bordure visible.
7. **Formulaires** : soumettre un formulaire avec une erreur (transaction sans montant, par
   exemple) ; le focus va au premier champ en erreur, le message est lu.
8. **Fonctions de l'appareil** (téléphone cible, en HTTPS sur le domaine, ou avec l'autorité
   mkcert installée sur le téléphone si l'accès est limité au réseau local) : enrôlement et
   connexion par passkey, autorisation du micro et dictée, « Écouter » avec une voix
   française locale, photo de ticket par l'appareil photo.
9. **Application installée** (ADR 0022, protocole complet dans l'ADR) : installer LifeOS sur
   l'écran d'accueil, refaire le point 8 depuis l'icône ; partager une photo vers LifeOS
   (Android) ; notification de test application fermée, écran verrouillé, « Ne pas
   déranger » actif ; page « LifeOS n'est pas joignable » sans réseau ou sans VPN.

## 4. Résultats sur les appareils

À remplir par le propriétaire (modèle, version du système et du navigateur). Le tableau est
vide tant que les tests n'ont pas été faits sur les vrais appareils.

| Appareil et navigateur | Date | Passkey : enrôlement | Passkey : connexion | Dictée (micro) | « Écouter » (voix française locale) | Photo de ticket | Lecteur d'écran | Zoom 200 % / 320 px | Thème sombre |
|---|---|---|---|---|---|---|---|---|---|
| Téléphone cible : _modèle, Android/iOS x, navigateur y_ | | | | | | | | | |
| Ordinateur : _système, navigateur_ | | | | | | | | | |

Application installée (point 9) :

| Appareil et navigateur | Date | Installation | Passkey en mode installé | Partage vers LifeOS | Push, app fermée | Push, écran verrouillé | Push, « Ne pas déranger » | Appui → page | Hors réseau / hors VPN |
|---|---|---|---|---|---|---|---|---|---|
| Téléphone cible : _modèle, Android/iOS x, navigateur y_ | | | | | | | | | |

Résultats automatiques : voir le dernier passage de la CI (job `e2e`, projets `chromium` et
`mobile-chromium`).
