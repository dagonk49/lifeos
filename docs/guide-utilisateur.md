# Guide d'utilisation — LifeOS 1.0

LifeOS est votre assistant personnel, hébergé chez vous. Vos données restent sur votre
serveur. L'assistant IA (Gemini) n'est sollicité que pour les espaces où vous l'avez
autorisé, avec un contexte minimal et masqué. Rien d'important n'est fait sans votre accord.

## Les principes à connaître

- **Deux espaces** : *Personnel* et *Professionnel*. Le sélecteur en haut de page filtre ce
  que vous voyez (« Tous les espaces » pour tout voir). Chaque donnée appartient à un espace.
- **Vous validez** : les propositions de l'assistant (tâches et faits tirés du journal ou
  d'une conversation, opérations lues sur un ticket) attendent votre accord dans
  **Approbations** ou dans la page concernée.
- **Local d'abord** : la recherche, la lecture des documents (OCR), la dictée, les budgets et
  les bilans chiffrés sont calculés sur votre serveur, même sans assistant IA.
- **Documents sensibles** (identité, santé, banque) : reconnus localement, leur contenu n'est
  **jamais** envoyé à l'IA.

## Connexion

Vous vous connectez avec une **passkey** (empreinte, visage ou code de l'appareil), sans mot
de passe. Ajoutez une deuxième passkey sur un autre appareil dans **Réglages → Appareils et
passkeys** ; cette page liste aussi les sessions ouvertes, que vous pouvez révoquer.

## Pages

### Accueil

Briefing du jour :

- tâches en retard, du jour et à venir ;
- journal du jour ;
- approbations en attente ;
- état de la mémoire ;
- **budget du mois** (catégories proches de leur budget) ;
- consommation de l'assistant IA.

### Assistant

- Conversation avec l'assistant, qui s'appuie sur les souvenirs et faits pertinents de
  l'espace.
- **Agents spécialisés** (Planification, Coaching, Mémoire, Finances) : choisissez-en un
  dans la liste. À la première utilisation, il demande un accès en lecture limité à ce dont
  il a besoin (90 jours, révocable).
- **Joindre un document** : le fichier est lu sur votre serveur. Seul son texte accompagne
  le message, et jamais celui d'un document sensible. « Vérifier le texte » ouvre le document
  pour corriger la lecture avant d'envoyer.
- **Dicter** : le texte dicté s'ajoute au message, que vous relisez avant d'envoyer.
- **Écouter** : lit une réponse à voix haute avec une voix de votre appareil. Le bouton
  n'apparaît que si l'appareil a une voix française locale.
- **Conserver / Mémoriser** : les conversations sont effacées après 90 jours, sauf les
  messages conservés. « Mémoriser » enregistre un message comme souvenir.
- **Retenir l'essentiel** : l'assistant propose les faits durables d'une conversation, à
  approuver.

### Tâches

Création avec priorité, échéance et rappel. Les rappels respectent vos plages de
tranquillité (**Réglages → Préférences**).

### Journal (et journal oral)

- Écrivez votre journée, ou **Dicter ma journée** : l'enregistrement est transcrit sur le
  serveur puis supprimé ; le texte s'ajoute au formulaire pour que vous le corrigiez avant
  d'enregistrer.
- **Extraire tâches et faits** : l'assistant propose des tâches et des faits tirés de
  l'entrée. Cochez ceux à garder, ajustez-les, puis approuvez.

### Objectifs

Objectifs mesurables (valeur de départ et cible) ou qualitatifs, avec jalons, points d'étape
et tâches rattachées. La progression est calculée localement.

### Documents

- **Envoyer un document** : PDF, photo (JPEG, PNG, WebP, HEIC) ou texte. Choisissez
  « Document » ou « Ticket de caisse ».
- La lecture se fait en arrière-plan (quelques secondes, plus pour un PDF scanné). Le statut
  passe de « Lecture en cours » à « Prêt ».
- **Texte extrait** : corrigez-le si la lecture est incertaine (la confiance est affichée).
  Une correction refait la détection des données sensibles.
- **Original** : il est conservé chiffré 90 jours par défaut. Cochez « Conserver l'original
  sans limite » pour le garder. Après la purge, le texte extrait reste disponible.
- Badges **Identité / Santé / Banque** : document sensible, jamais transmis à l'IA (les
  raisons sont affichées, sans les valeurs).
- Les documents sont retrouvables dans **Recherche**.

### Finances

- **Mois** : naviguez avec « Mois précédent » et « Mois suivant ». Le budget du mois est calculé
  localement, en montants exacts.
- **Catégories** : créez-les, ou « Créer les catégories par défaut », et fixez un budget
  mensuel.
- **Opérations** : saisie manuelle (montant à la française, ex. `12,50`), filtres, correction
  de la catégorie. LifeOS retient votre choix pour ce commerçant : les opérations suivantes
  sont classées seules, marquées « classée automatiquement », et restent corrigeables.
- **Scanner un ticket** : photographiez le ticket. LifeOS lit le commerçant, la date et le
  total en indiquant la ligne d'où vient chaque valeur. Vérifiez et corrigez, puis
  **Ajouter l'opération**. Rien n'est enregistré sans votre clic, et LifeOS prévient si le
  ticket a déjà été enregistré.
- Aucune connexion bancaire, aucun paiement.

### Bilans

Bilans du jour, de la semaine et du mois :

- **faits** calculés localement (tâches terminées, objectifs, journal) ;
- **lecture** de l'assistant, si autorisée, séparée des faits ; ses hypothèses sont
  présentées comme telles ;
- génération automatique possible, sur demande.

### Mémoire

- **Faits**, avec leur origine (déclaré, confirmé, extrait, inféré) et l'historique de leurs
  corrections, et **souvenirs**, dont les souvenirs protégés.
- Onglet **À vérifier** : doublons, contradictions possibles, préférences anciennes à
  confirmer.

### Recherche

Recherche par les mots **et par le sens** (« ce que j'ai décidé pour la voiture »), filtres
par type (tâches, journal, faits, souvenirs, objectifs, documents, conversations), par espace
et par période. Des expressions comme « la semaine dernière » ou « en mars » sont comprises.

### Approbations

Toute action importante proposée par l'assistant attend ici votre accord : vous voyez
exactement ce qui sera fait, vous pouvez décocher des éléments, approuver ou refuser.

### Réglages

- **Appareils et passkeys** : vos appareils, vos sessions, et leur révocation.
- **Espaces et assistant IA** : autoriser l'IA par espace, consommation du jour et du mois,
  **pause de l'assistant IA** (arrêt d'urgence : plus aucun appel cloud).
- **Agents spécialisés / Permissions des agents** : accès accordés, avec leur échéance et
  leur retrait.
- **Préférences** : nom, fuseau horaire, plages de tranquillité.
- **Mes données** : export complet (JSON).
- **Journal d'audit** : qui a fait quoi, sans le contenu.

## Ce que l'assistant voit — et ne voit jamais

| Envoyé à Gemini (espace autorisé seulement, après masquage) | Jamais envoyé |
|---|---|
| votre message et les derniers échanges de la conversation | vos documents (fichiers, images) |
| les souvenirs et faits pertinents du même espace | le texte d'un document sensible |
| le texte d'une pièce jointe non sensible, avec ce message | votre voix (transcrite sur le serveur) |
| pour un agent : uniquement les ressources que vous lui avez ouvertes | les vecteurs de recherche, les budgets, les faits des bilans |

Les e-mails, numéros de téléphone, IBAN, numéros de carte et numéros de sécurité sociale sont
masqués avant tout envoi. Sans compte de facturation Google, l'assistant ne peut rien vous
coûter : au-delà du quota gratuit du jour, il répond simplement qu'il est indisponible.

## Ce que cette version ne fait pas encore

Pas de synchronisation d'agenda ni d'e-mail, pas de notifications push sur le téléphone (les
rappels s'affichent dans l'application) et pas d'application mobile native : utilisez le site
dans le navigateur du téléphone. Voir le [journal des versions](../CHANGELOG.md).
