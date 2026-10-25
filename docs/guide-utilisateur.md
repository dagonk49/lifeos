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
passkeys** ; cette page liste aussi les sessions ouvertes, que vous pouvez révoquer une à une.
Téléphone perdu, session que vous ne reconnaissez pas : **Déconnecter les autres sessions**
ferme d'un coup toutes les sessions sauf celle de l'appareil que vous tenez (votre passkey
est demandée si votre connexion date de plus de 10 minutes). L'appareil déconnecté revient à
la page de connexion à sa prochaine action.

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
  le message, et jamais celui d'un document sensible ou gardé en local. « Vérifier le
  texte » ouvre le document pour corriger la lecture avant d'envoyer.
- **Avant l'envoi** : si un identifiant de votre message ou d'une pièce jointe va être
  masqué (IBAN, téléphone, e-mail…), ou si une pièce jointe reste sur votre serveur, un
  encadré « Avant l'envoi à l'assistant IA » le dit (par type et par nombre, jamais les
  valeurs). **Confirmer l'envoi** envoie le message ; **Retirer** enlève une pièce jointe ;
  **Annuler** (ou Échap) garde le message sans rien envoyer. Sans rien à signaler, le
  message part directement.
- **Dicter** : le texte dicté s'ajoute au message, que vous relisez avant d'envoyer.
- **Écouter** : lit une réponse à voix haute avec une voix de votre appareil. Le bouton
  n'apparaît que si l'appareil a une voix française locale.
- **Conserver / Mémoriser** : les conversations sont effacées après 90 jours, sauf les
  messages conservés. « Mémoriser » enregistre un message comme souvenir.
- **Retenir l'essentiel** : l'assistant propose les faits durables d'une conversation, à
  approuver.

### Tâches

Une tâche se crée avec une priorité, une échéance, un rappel et des **étiquettes**. Les
étiquettes sont libres et séparées par des virgules, par exemple « maison, urgent ». Elles
sont enregistrées en minuscules, 10 au plus. Les rappels respectent vos plages de
tranquillité (**Réglages → Préférences**).

**Dépendances.** Une tâche peut en attendre d'autres du même espace. Pour la lier :

- à la création, choisissez **Dépend de** ;
- ensuite, ouvrez **Dépendances** sous la tâche, choisissez une tâche dans **Attendre
  aussi**, puis **Lier**.

**Retirer** supprime un lien.

- Une tâche qui attend une tâche encore ouverte est **bloquée**. Elle affiche « Bloquée
  par « … » » et ne peut être ni commencée ni terminée. Elle peut toujours être annulée ou
  supprimée.
- Quand la dernière tâche attendue est terminée ou annulée, la tâche est libérée. Vous
  recevez une notification « Débloquée : … », une seule fois.
- LifeOS refuse tout lien qui créerait une boucle (A attend B qui attend A) et ne change
  rien.
- Supprimer une tâche attendue libère celles qui l'attendaient.

Le filtre **Afficher** montre les tâches ouvertes, bloquées ou terminées. Le filtre
**Étiquette** apparaît dès qu'une tâche en porte une. Les tâches bloquées sont listées en
dernier. L'assistant de planification voit, pour chaque tâche ouverte, ce qui la bloque.

Il n'y a pas encore de **tâches récurrentes** : recréez la tâche après l'avoir terminée
(décision : [ADR 0013](adr/0013-taches-recurrentes.md)).

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
  raisons sont affichées, sans les valeurs). Sont reconnus notamment les pièces d'identité,
  passeports, titres de séjour, permis, cartes grises, avis d'impôt et de taxe foncière ou
  d'habitation (identité) ; ordonnances, comptes rendus, analyses, arrêts de travail,
  décomptes de mutuelle (santé) ; RIB, relevés, bulletins de paie, offres de prêt (banque),
  même mal lus par l'OCR.
- **Toujours garder en local** (section « Assistant IA » du document) : votre propre refus,
  en plus de la détection. Le document n'est plus jamais envoyé à l'IA, même après une
  correction du texte ou une nouvelle lecture (badge « Gardé en local »). Le décocher demande
  une confirmation par passkey. Une catégorie détectée ne se lève pas à la main : si la
  détection s'est trompée, corrigez le texte, elle est refaite.
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

- **Appareils et passkeys** : vos appareils, vos sessions, leur révocation une à une ou
  **Déconnecter les autres sessions**.
- **Espaces et assistant IA** : autoriser l'IA par espace, consommation du jour et du mois,
  **pause de l'assistant IA** (arrêt d'urgence : plus aucun appel cloud).
- **Agents spécialisés / Permissions des agents** : accès accordés, avec leur échéance et
  leur retrait.
- **Comptes connectés** (si l'administrateur les a activés, [guide Google](guide-google.md)) :
  - **Connecter un compte** : choisir l'espace et cocher les usages (lire ou modifier
    l'agenda, lire ou envoyer des e-mails), puis accepter chez Google, où vous pouvez encore
    décocher des accès. Une confirmation par passkey est demandée.
  - Pour chaque compte, le panneau affiche ce que Google a **réellement** accordé et, espace
    par espace, les usages autorisés. Cocher un usage demande une passkey (et un nouvel
    accord chez Google s'il ne l'a pas encore donné) ; décocher le coupe aussitôt.
  - **Reconnexion nécessaire** : Google n'accepte plus l'accès (autorisation retirée, mot de
    passe changé, expiration). Les usages sont suspendus jusqu'à « Reconnecter le compte ».
  - **Déconnecter** révoque l'accès chez Google et efface les jetons. LifeOS demande chaque
    fois s'il faut garder (par défaut) ou supprimer les données déjà synchronisées. Un
    compte déconnecté reste listé, sans aucun accès, jusqu'à ce que vous le supprimiez.
  - **Choisir les agendas à suivre** : pour chaque agenda du compte, l'espace qu'il
    alimente (seuls les espaces où la lecture de l'agenda est autorisée sont proposés).
    LifeOS le lit environ toutes les 15 minutes, sur 30 jours passés et un an à venir ;
    **Synchroniser** le relit tout de suite. **Ne plus suivre** supprime de LifeOS les
    événements copiés (pas chez Google). Si l'autorisation de l'espace est retirée, la
    synchronisation s'arrête et le dit.
  - Page **Agenda** : la semaine de vos agendas suivis, avec la source et l'heure de
    lecture de chaque événement. En dessous, les **conflits** (événements qui se
    chevauchent) et **Trouver un créneau** : jusqu'à trois créneaux libres pour une durée,
    chacun avec ses hypothèses (durée, disponibilités du lundi au vendredi de 9 h à 18 h,
    marge de 10 min, événements provisoires comptés comme occupés, date de lecture de
    l'agenda) et les événements autour. Rien n'est ajouté à votre agenda. Les événements
    d'un autre espace comptent comme « Occupé (autre espace) », sans titre.
  - L'**agent de planification**, s'il a la permission « agenda » dans l'espace, voit les 7
    prochains jours de l'agenda de cet espace, les conflits et deux créneaux calculés par
    LifeOS ; sans cette permission, il planifie à partir des tâches seules.
  - Les jetons d'accès restent chiffrés sur votre serveur : ils ne sont jamais affichés,
    exportés ni journalisés.
- **Préférences** : nom, fuseau horaire, plages de tranquillité.
- **Mes données** : export complet (JSON).
- **Journal d'audit** : qui a fait quoi, sans le contenu. Les refus apparaissent en rouge
  (`denied`), par exemple :
  - `auth.session.rejected` : une session révoquée ou expirée a été réutilisée (un ancien
    appareil, ou un cookie copié) ;
  - `http.origin_mismatch` : une écriture venue d'un autre site a été bloquée ;
  - `space.access` : une requête nommait un espace qui n'est pas le vôtre ;
  - `approval.replay` : une décision a été envoyée deux fois sur la même approbation ;
  - `chat.agent_reply`, `document.attach` : un agent sans accès a été arrêté.

  `auth.session.ua_changed` signale une session utilisée depuis un autre navigateur ou
  système que celui de la connexion : si ce n'est pas vous, utilisez **Déconnecter les autres
  sessions**. Le détail est dans la [recette de sécurité](recette-securite.md).

## Ce que l'assistant voit — et ne voit jamais

| Envoyé à Gemini (espace autorisé seulement, après masquage) | Jamais envoyé |
|---|---|
| votre message et les derniers échanges de la conversation | vos documents (fichiers, images) |
| les souvenirs et faits pertinents du même espace | le texte d'un document sensible ou gardé en local |
| le texte d'une autre pièce jointe, avec ce message | votre voix (transcrite sur le serveur) |
| pour un agent : uniquement les ressources que vous lui avez ouvertes | les vecteurs de recherche, les budgets, les faits des bilans |

Les e-mails, numéros de téléphone, IBAN, numéros de carte, numéros de sécurité sociale, mots
de passe et codes annoncés, et numéros de passeport, de pièce d'identité ou fiscaux écrits
après le nom du document sont masqués avant tout envoi, même écrits avec des espaces
insécables, des points, des tirets ou mal lus par l'OCR. La détection reste au mieux : elle
ne reconnaît ni les noms ni les adresses. Sans compte de facturation Google, l'assistant ne
peut rien vous coûter : au-delà du quota gratuit du jour, il répond simplement qu'il est
indisponible.

## Ce que cette version ne fait pas encore

Un compte Google peut être connecté et autorisé espace par espace, mais l'agenda et les
e-mails ne sont pas encore synchronisés ni utilisés par l'assistant. Pas de notifications
push sur le téléphone (les rappels s'affichent dans l'application) et pas d'application
mobile native : utilisez le site dans le navigateur du téléphone. Voir le [journal des versions](../CHANGELOG.md).
