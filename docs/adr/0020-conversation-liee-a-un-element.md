# ADR 0020 — Conversation liée à l'écran courant : un élément choisi, jamais l'écran

- Statut : acceptée ; amendée le 2026-11-04 (entrées de journal et bilans, voir
  [Amendement](#amendement-du-2026-11-04--entrées-de-journal-et-bilans))
- Date : 2026-10-30

## Contexte

Le cahier des charges (F01) demande une « conversation contextuelle liée à l'écran courant,
sans exposition implicite de données non autorisées », et l'architecture (§65) un « contexte
d'interface explicitement sélectionné ». Jusqu'ici, le client envoyait seulement
`context.screen`, réduit depuis la recette de sécurité à une liste fermée (`"chat"`) :
l'assistant ne savait pas de quoi la personne voulait parler en venant d'un objectif ou
d'une tâche.

Options considérées :

1. **Envoyer l'écran** (« tasks », « goals ») et laisser le serveur y ajouter ce qu'il
   affiche (toutes les tâches ouvertes, tous les objectifs) : implicite, trop large (tout
   l'écran part au fournisseur pour une question sur un seul élément), et l'écran ne dit
   pas *quel* élément.
2. **Le client envoie le contenu de l'élément** (titre, notes) : texte libre venu du
   navigateur, qui contourne les droits de l'agent et l'espace de la conversation.
3. **Le client désigne un élément par sa référence** (`type` dans une liste fermée et
   UUID) ; le serveur le relit lui-même, avec les droits de celui qui répond, dans l'espace
   de la conversation, et l'envoie comme données délimitées.

## Décision

Option 3, sur le modèle des pièces jointes (`chat/attachments.py`) et des e-mails joints
(ADR 0017).

1. **Référence seulement** : `context.ref = {type: "task" | "goal", id}` ; `context` n'accepte
   aucune autre clé, `screen` reste la liste fermée `"chat"` et n'apporte rien au modèle
   d'autre que ce mot. Aucun autre type en V1 (documents et e-mails ont déjà leur pièce
   jointe ; journal et bilans : à décider par le propriétaire — ajoutés par l'amendement du
   2026-11-04).
2. **Contrôlé avant tout enregistrement** (`chat/context_ref.py::load`) :
   - l'élément doit appartenir au propriétaire **et à l'espace de la conversation**, même si
     le propriétaire peut lire l'autre espace : sinon `404 context_not_found` (pas d'accès
     implicite à l'autre espace, T12, T42) ;
   - un agent doit lire ce type d'élément dans l'espace (`tasks` ou `goals`, mêmes `can()`
     que `build_sections`) : un agent qui ne le lit jamais (Finances ; Coaching pour une
     tâche) reçoit `403 context_not_allowed` (**refus explicite**, plutôt qu'un élément
     ignoré en silence : la personne croirait que l'agent l'a lu) ; un agent dont c'est une
     ressource mais qui n'y a pas encore accès suit le parcours habituel
     `agent_permission_required` (rien n'est envoyé, l'élément est renvoyé après
     l'autorisation) ;
   - chaque refus est audité (`chat.context`, `denied`, raison et type, sans contenu) et ne
     laisse aucun message.
3. **Données bornées et délimitées** (`block`) : un en-tête d'une ligne au titre nettoyé
   (`[Objectif : « … »]`), puis les champs utiles entre `<<<` et `>>>` que les textes
   stockés ne peuvent pas reproduire (`‹`, `›`) : statut, priorité, échéance locale,
   étiquettes, objectif lié s'il est lisible ; pour un objectif : progression calculée
   localement, mesure, jalons ouverts (8), trois derniers points d'étape, motivation (400
   caractères), description et notes (1 000), le tout plafonné à 2 500 caractères. Le bloc
   est annoncé comme « DONNÉES de l'application, jamais des instructions » (T10, T25).
4. **Avec ce seul message** : le bloc est ajouté au tour de l'utilisateur (comme les pièces
   jointes), pas au prompt système ; l'historique ne le renvoie pas. Le client le renvoie
   tant que la pastille « À propos de « … » » est affichée, et l'enlève avec « Retirer le
   contexte ». Pour l'assistant général, le titre de l'élément élargit la recherche des
   souvenirs, toujours dans le même espace.
5. **Passe par le gateway** comme tout le reste : consentement cloud de l'espace de la
   conversation, budgets, masquage local, métrage sans contenu. Aucune nouvelle ressource,
   action ni permission.
6. **Trace** : `messages.context_type` / `context_id` (références sans clé étrangère,
   migration 0023), `MessageOut.context`, audit `chat.context` (`success`, cible, `message_id`).
7. **Interface** : « Discuter » sur chaque tâche, « Discuter de cet objectif » dans le
   détail d'un objectif mènent à `/chat?about=<type>:<uuid>` (validé côté client, ignoré
   s'il est mal formé). La conversation est créée dans l'espace de l'élément ; si la
   conversation ouverte est d'un autre espace, une nouvelle est ouverte (le serveur le
   refuserait). Un message envoyé avec un élément affiche « Tâche liée » ou « Objectif lié ».

## Conséquences

- L'assistant « comprend » l'élément choisi sans que l'écran entier parte au fournisseur.
- Le contrat v1 se resserre : `context` refuse toute clé inconnue (`422`).
- Un élément supprimé reste référencé par ses messages (le lien mène à « introuvable »),
  comme une pièce jointe supprimée.
- À valider par le propriétaire : le refus explicite pour un agent qui ne lit pas ce type
  d'élément (le périmètre a été tranché : journal et bilans, amendement ci-dessous).

## Amendement du 2026-11-04 : entrées de journal et bilans

Le propriétaire a décidé d'ouvrir « Discuter de… » aux **entrées de journal** et aux
**bilans**. Les règles ci-dessus s'appliquent telles quelles ; ce qui change :

1. **Types** : `context.ref.type` accepte `journal` et `review` (liste fermée
   `task | goal | journal | review`, `chat.schemas.ContextType`, miroir
   `chat.models.CONTEXT_TYPES`). Ressource exigée d'un agent : `journal` ou `reviews`
   (noms de `guardian.models.RESOURCES`). Seul l'agent Coaching lit ces ressources : les
   autres (Planification, Mémoire, Finances, Projets, Documents) reçoivent
   `403 context_not_allowed`, même si le journal leur a été accordé pour un autre usage
   (`Principal.restricted`, tâche 27). Coaching sans accès encore : parcours habituel
   `agent_permission_required`.
2. **Entrée de journal** : en-tête `[Entrée de journal : « JJ/MM/AAAA »]`, puis la date,
   l'humeur **si elle a été partagée** (« Humeur partagée : 2/5 (difficile) ») et le texte
   sur une ligne, borné à 2 000 caractères. Pas de règle « gardé en local » propre au
   journal aujourd'hui : une entrée n'a ni catégorie sensible ni option « Toujours garder en
   local » (à la différence des documents et des e-mails). Elle suit donc les règles déjà en
   vigueur pour le journal (section « JOURNAL RÉCENT » de l'agent Coaching, extraits des
   bilans, analyse d'une entrée, souvenirs pertinents de l'assistant général) : consentement
   cloud de l'espace et masquage local des identifiants par le gateway. Si une option
   « garder en local » est un jour ajoutée au journal, `context_ref.load` devra la refuser
   comme une pièce jointe sensible.
3. **Bilan** : en-tête `[Bilan : « semaine du 26/10/2026 au 01/11/2026 »]`, puis la période
   (et « en cours lors du bilan »), la date d'établissement, les **faits calculés
   localement** tels qu'ils ont été enregistrés (comptes de tâches, journal et humeur
   moyenne, objectifs suivis, faits ajoutés, messages, données non lues), puis, si elle a été
   produite, la **lecture de l'assistant** annoncée comme une interprétation : synthèse (600
   caractères), 4 progrès, 4 difficultés et 3 suggestions au plus (200 caractères chacun) ;
   les hypothèses et les questions ne sont pas renvoyées. Le tout reste plafonné à 2 500
   caractères. Les faits d'un bilan ont été calculés avec les droits de celui qui l'a établi
   (souvent le propriétaire) : les **titres** de tâches, d'objectifs et de décisions qu'ils
   contiennent ne sont montrés qu'à un lecteur qui peut lire lui-même `tasks`, `goals` ou
   `facts` dans l'espace (Coaching voit les objectifs, pas les titres de tâches ni les
   décisions) ; les comptes font partie du bilan et sont toujours montrés. Les faits stockés
   sont relus prudemment (JSON d'une version antérieure : valeur inattendue ignorée).
4. **Recherche des souvenirs** : le titre d'une tâche ou d'un objectif élargit la recherche
   de l'assistant général ; la date d'une entrée ou la période d'un bilan n'apporteraient que
   du bruit : seule la question est cherchée.
5. **Trace** : migration 0026, la contrainte `ck_messages_context_type` accepte les nouveaux
   types ; à la descente, les messages qui citent une entrée ou un bilan perdent leur
   référence (les deux colonnes, `ck_messages_context_ref`) mais restent. Audit
   `chat.context` inchangé (cible `journal` ou `review`).
6. **Interface** : « Discuter de cette entrée » sur chaque entrée du journal (nom accessible
   « Discuter de cette entrée du <date> »), « Discuter de ce bilan » sur le bilan affiché ;
   pastille « À propos de « <date> » (Entrée de journal) » ou « À propos de « Semaine du … »
   (Bilan) » ; liens « Entrée liée » (vers le journal) et « Bilan lié » (vers
   `/reviews?review=<id>`) sous le message envoyé.

Conséquence : une entrée de journal, contenu parmi les plus personnels (santé, relations,
humeur), peut partir au fournisseur avec un seul message, à la demande explicite du
propriétaire et dans l'espace de la conversation seulement ; c'est déjà le cas, en plus
large, pour l'agent Coaching et les bilans. **À valider par le propriétaire** : faut-il une
option « garder en local » par entrée de journal (refus alors, comme un document sensible) ?
