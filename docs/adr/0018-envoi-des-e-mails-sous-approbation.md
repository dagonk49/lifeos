# ADR 0018 — Envoi des e-mails : brouillon, aperçu exact, approbation, jamais deux fois

- Statut : acceptée, amendée le 2026-11-04 (brouillons rédigés par un agent)
- Date : 2026-10-30
- Tâche : 18 (« Envoyer un e-mail sous approbation », UC04), étape 5
- Liens : [ADR 0012](0012-connecteurs-oauth.md) (comptes connectés),
  [ADR 0016](0016-ecritures-externes-sous-approbation.md) (réservation, clé d'idempotence,
  rapprochement), [ADR 0017](0017-lecture-des-e-mails.md) (lecture), menaces T7, T10, T14,
  T37

## Contexte

Le cahier des charges (UC04, §5) range l'envoi d'un e-mail parmi les actions « validation
obligatoire » : aperçu exact (destinataires, contenu, pièces jointes) puis accord ponctuel.
Trois risques propres à l'e-mail :

1. **L'exfiltration** : une instruction cachée dans un e-mail ou un document (T10) qui
   ferait envoyer des données à une adresse inconnue.
2. **Le double envoi** : Gmail (`users.messages.send`) n'a aucune idempotence. Un délai
   dépassé après l'envoi, un processus tué, un double clic ou une requête rejouée peuvent
   envoyer deux fois.
3. **L'écart entre ce qui est approuvé et ce qui part** : un brouillon modifié après la
   demande, une pièce jointe remplacée, une approbation modifiée en base.

## Décision

### 1. Un brouillon, puis une proposition

- `mail_drafts` (migration 0019) garde le message : compte d'envoi, To, Cc, Bcc (adresses
  vérifiées, en minuscules, 50 au plus), objet (une ligne, 300 caractères), texte brut
  (100 000 caractères), documents de **son** espace en pièces jointes (10 au plus, prêts,
  original conservé, `mail_max_send_bytes` au total, 10 Mo par défaut), message auquel il
  répond. Créer ou modifier un brouillon n'envoie rien.
- « Préparer l'envoi » (`POST /v1/mail/drafts/{id}/send`) crée une approbation
  (`target = mail_draft:<id>`, une seule en attente par brouillon). **Ses paramètres sont le
  message** : adresse d'envoi, To, Cc, Bcc, objet, texte, pièces jointes (titre, nom de
  fichier, type, taille, SHA-256 des octets lus à la demande, sensibilité), en-têtes de
  réponse, version du brouillon. L'aperçu rend exactement ces champs.
- Modifier ou supprimer le brouillon **invalide** l'approbation en attente
  (`draft_changed`, `draft_deleted`). Les paramètres ne sont pas modifiables à
  l'approbation (`editable=False`) : une correction passe par le brouillon.

### 2. Niveaux

- Tout e-mail : `email.send`, validation obligatoire.
- `email.send_reinforced` (approbation **renforcée**, passkey de moins de 10 minutes) dès
  que le message va à une adresse à laquelle LifeOS n'a jamais écrit depuis cet espace (le
  compte lui-même excepté), ou joint **un document, quel qu'il soit** (décision du
  propriétaire, 2026-11 : un fichier qui quitte le serveur demande toujours la passkey ;
  une pièce sensible, détectée ou « toujours garder en local », est en plus signalée).
  L'aperçu nomme ces nouveaux destinataires. Le niveau est décidé à partir des
  paramètres et revérifié à l'envoi : une approbation au mauvais niveau, ou une pièce
  devenue sensible depuis, est refusée (`approval_stale`).
- Les agents rédigent des brouillons, ils ne proposent ni n'envoient jamais un e-mail ;
  seul le propriétaire prépare l'envoi et l'approuve (voir l'amendement ci-dessous, qui
  remplace la règle initiale « rédiger et proposer »).

### 3. À l'envoi

Dans l'ordre, sous le verrou de l'approbation puis du brouillon (le même ordre pour une
modification : jamais d'interblocage) :

1. le brouillon existe, attend **cette** approbation, a la même version et le même contenu
   que les paramètres ; sinon `409 approval_stale` (ou `draft_already_sent`) ;
2. le consentement `mail.send` de l'espace (`require_capability`) et la même adresse
   d'envoi ;
3. chaque pièce jointe relue (déchiffrée) et comparée à son SHA-256 (`attachment_changed`) ;
4. message construit par la bibliothèque standard (`email.message.EmailMessage`, politique
   SMTP en 7 bits : objet RFC 2047, texte quoted-printable, pièces base64), en texte brut
   seulement ; aucune valeur d'en-tête ne peut contenir de retour à la ligne. Le `Bcc` reste
   dans le message brut : Gmail lit les destinataires dans les en-têtes, livre aux adresses
   en copie cachée et retire cet en-tête des copies livrées. `In-Reply-To`, `References` et
   `threadId` pour une réponse ;
5. envoi par `guardian/operations.run_external` (ADR 0016).

### 4. Jamais deux fois

- Le **Message-ID** est dérivé de l'approbation : `<clé d'idempotence@domaine de l'adresse
  d'envoi>` (la clé de l'ADR 0016). Il ne révèle pas le nom du serveur.
- Réservation avant l'appel, comme pour l'agenda. Une réservation existante (requête morte,
  nouvel accord après un plantage) n'est jamais suivie d'un envoi à l'aveugle : le
  rapprochement cherche d'abord le message chez Gmail, par `rfc822msgid:` puis, au cas où
  Gmail aurait réécrit le Message-ID, parmi les messages envoyés depuis la proposition
  (même objet, mêmes destinataires visibles). Trop de messages à comparer (plus de 100) :
  pas de conclusion, rien n'est renvoyé.
- Ce rapprochement exige la lecture de Gmail (`gmail.readonly`, capacité `mail.read` du
  compte). **Sans elle, un envoi incertain le reste** : le propriétaire reçoit une
  notification (une seule par approbation), vérifie ses messages envoyés et l'indique sur
  le brouillon (`POST /v1/mail/drafts/{id}/resolve {sent}`). « Il n'est pas parti » permet
  une nouvelle proposition, avec un nouveau Message-ID.
- Délai dépassé ou erreur `5xx` après l'envoi : issue inconnue (`outcome_unknown`), tâche de
  rapprochement mise en file. Une erreur avant l'envoi (connexion refusée, `4xx`, `429`) :
  rien n'est parti, le brouillon peut être proposé de nouveau.
- L'état du brouillon (`draft`, `awaiting_approval`, `sending`, `unknown`, `failed`,
  `sent`) se **déduit** de son approbation et de sa réservation : il ne peut pas les
  contredire. Seul `sent` est écrit, dans la même transaction que l'approbation exécutée
  (ou que le rapprochement). Un brouillon en cours d'envoi ou incertain ne se modifie ni
  ne se supprime.

### 5. Envois volumineux

Jusqu'à 4 Mo, le message part encodé en base64url dans le corps JSON de
`messages/send` ; au-delà, par le point d'envoi de fichiers
(`/upload/gmail/v1/users/me/messages/send?uploadType=multipart`, `message/rfc822`).

### 6. Ce qui est gardé

- Journal d'audit et journaux : identifiants et nombres seulement (`email.sent` :
  destinataires, pièces jointes, compte, approbation), jamais une adresse, un objet ou un
  texte (T7).
- Le contenu exact reste dans l'approbation tant que l'envoi peut être vérifié, puis
  `mail_approval_redact_days` (30 jours) après la décision ; il est alors **effacé** : il ne
  reste que l'empreinte et les nombres. L'approbation part ensuite avec les autres (90
  jours). Les brouillons envoyés partent `mail_retention_days` (90 jours) après l'envoi ;
  les autres restent jusqu'à leur suppression.

## Amendement du 2026-11-04 — un agent rédige, il n'envoie jamais

Décision du propriétaire : **les agents peuvent écrire des brouillons, jamais les
envoyer.** Le mécanisme est celui des propositions existantes (consolidation d'une
conversation, analyse du journal, propositions d'agenda de l'ADR 0016) : une demande
explicite du propriétaire, un seul appel au LLM par la passerelle, un résultat qu'il
contrôle avant tout effet.

1. **Qui.** L'assistant général (`core`), qui travaille pour le propriétaire dans la
   conversation avec ses droits, comme ses réponses et la consolidation ; et tout agent
   qui a la permission `email` **en écriture** dans l'espace de la conversation (accordée
   par une opération renforcée, révocable). Aucun agent spécialisé n'a `email` parmi ses
   ressources : il ne rédige qu'après cette décision explicite du propriétaire ; une
   permission en lecture ne suffit pas. Le refus est audité (`mail.draft.create`, refusé,
   `no_permission`) avant tout appel. La politique déclare `email.draft` (automatique pour
   le propriétaire, règle pour un agent) ; `email.send` et `email.send_reinforced` n'ont
   plus de niveau agent : un agent ne peut ni demander l'envoi (`403 action_denied`,
   `forbidden_for_agents`, audité), ni approuver, et `request_approval` refuse désormais
   à un agent toute action que la politique lui interdit.
2. **Comment.** « Proposer un brouillon d'e-mail » dans la conversation, ou « Proposer une
   réponse » sous un e-mail discuté (`POST /v1/chat/conversations/{id}/mail-draft`). Le
   contexte est minimal : les messages de la conversation (pour un agent spécialisé, les
   mots du propriétaire et ses propres réponses seulement) et, pour une réponse, l'e-mail
   répondu, délimité comme donnée de tiers, refusé s'il est sensible. Objectif `mail.draft`
   : consentement de l'espace, arrêt d'urgence, budgets, masquage et métrage du gateway.
3. **Ce qui est créé.** Un brouillon ordinaire, par le même chemin et les mêmes contrôles
   qu'un brouillon du propriétaire (adresses vérifiées, objet sur une ligne, taille), dans
   l'espace de la conversation, avec un compte autorisé à envoyer dans cet espace (celui
   par lequel l'e-mail répondu a été lu s'il peut envoyer). Il garde l'agent auteur et la
   conversation (migration 0025) ; l'interface affiche « Brouillon proposé par l'agent X ».
   Audit `mail.draft.create` avec l'agent pour acteur, des nombres seulement.
4. **Ce que le modèle écrit est une donnée.** Objet ramené à une ligne, texte brut nettoyé
   (caractères de contrôle et de mise en forme retirés), affiché en texte brut (T14). Le
   modèle ne propose que To, Cc, objet et texte : jamais de Cci, de pièce jointe ni de
   compte (champs hors schéma ignorés). Ses destinataires ne sont jamais crus : les
   adresses non valides (dont les adresses masquées `[EMAIL]`) sont retirées et comptées,
   les autres sont montrées telles quelles ; pour une réponse, l'expéditeur est ajouté par
   LifeOS, pas par le modèle.
5. **L'envoi reste au propriétaire.** Il ouvre le brouillon, le corrige s'il le veut, clique
   « Préparer l'envoi » et approuve le message exact, avec les règles du §2 : une adresse
   à laquelle LifeOS n'a jamais écrit depuis l'espace, y compris celle qu'un e-mail piégé
   aurait fait écrire au modèle, exige la passkey et est nommée dans l'aperçu.
6. **L'agent ne touche plus un brouillon du propriétaire.** Il ne modifie que le sien,
   dans la même conversation (« Proposer une autre version »), tant que le propriétaire ne
   l'a ni modifié ni préparé à l'envoi (`owner_edited_at`) ; sinon `409
   draft_not_editable_by_agent`, avant tout appel. Il ne supprime aucun brouillon.

Injection de prompt (T10, T37) : une demande cachée dans l'e-mail discuté peut au pire
finir dans un brouillon visible du propriétaire, jamais dans un e-mail envoyé ni dans une
approbation ; `backend/tests/test_agent_drafts.py` le vérifie avec le fournisseur factice
(destinataire pirate, Cci et pièce jointe ignorés, adresse masquée retirée, aperçu qui
nomme l'adresse inconnue, passkey exigée).

Alternatives écartées : un outil « créer un brouillon » appelé par le modèle pendant une
réponse ordinaire (le modèle déciderait seul quand écrire, à partir d'un contexte qui peut
contenir des données de tiers) ; une proposition d'envoi par l'agent à approuver (ce que
la règle initiale permettait : le propriétaire veut décider lui-même de préparer l'envoi) ;
des destinataires recopiés du modèle sans contrôle.

## Alternatives écartées

- **Corriger les paramètres au moment d'approuver** : la correction contournerait la
  vérification des adresses, le niveau renforcé et l'empreinte des pièces jointes.
- **Renvoyer automatiquement si le message n'est pas trouvé, sans lecture de Gmail** :
  impossible de distinguer « pas parti » de « parti » ; un double envoi est pire qu'un
  doute signalé au propriétaire.
- **Un en-tête maison (`X-LifeOS-…`) pour retrouver le message** : il signalerait l'outil à
  chaque destinataire ; l'objet et les destinataires suffisent en secours.
- **Approbation renforcée pour les seules pièces sensibles** (choix initial) : remplacé
  par la passkey pour toute pièce jointe, à la demande du propriétaire (2026-11).
- **SMTP** : un mot de passe d'application en plus, moins de contrôle des portées.

## Conséquences

- `backend/tests/test_mail_send.py` prouve : aucun envoi sans approbation (refus audité),
  aperçu égal aux paramètres, double approbation, approbations simultanées, requête
  rejouée, plantage après l'envoi, issue inconnue, Message-ID réécrit, absence de lecture
  de Gmail (notification unique, aucun renvoi), brouillon modifié, paramètres et fichier
  falsifiés, niveau renforcé, fil de réponse, Bcc, effacement. Les requêtes Gmail sont
  vérifiées par `httpx.MockTransport`.
- **À vérifier par un essai réel avant la mise en service** : Gmail conserve-t-il le
  Message-ID fourni ? retire-t-il bien l'en-tête Bcc des copies livrées ? limites de taille
  des deux points d'envoi ; une adresse « envoyer en tant que » doit être validée dans Gmail.
- Le statut « application non validée » de Google et les changements de règles peuvent
  bloquer `gmail.send` (scope sensible).
