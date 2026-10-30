# ADR 0018 — Envoi des e-mails : brouillon, aperçu exact, approbation, jamais deux fois

- Statut : acceptée
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
  compte lui-même excepté), ou joint un document sensible (détecté, ou « toujours garder en
  local »). L'aperçu nomme ces nouveaux destinataires. Le niveau est décidé à partir des
  paramètres et revérifié à l'envoi : une approbation au mauvais niveau, ou une pièce
  devenue sensible depuis, est refusée (`approval_stale`).
- Les agents (permission `email` en écriture) peuvent seulement rédiger et proposer
  (`requested_by = agent:<nom>`) ; seul le propriétaire approuve. Aucun agent n'est câblé
  pour le faire en v1.

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

## Alternatives écartées

- **Corriger les paramètres au moment d'approuver** : la correction contournerait la
  vérification des adresses, le niveau renforcé et l'empreinte des pièces jointes.
- **Renvoyer automatiquement si le message n'est pas trouvé, sans lecture de Gmail** :
  impossible de distinguer « pas parti » de « parti » ; un double envoi est pire qu'un
  doute signalé au propriétaire.
- **Un en-tête maison (`X-LifeOS-…`) pour retrouver le message** : il signalerait l'outil à
  chaque destinataire ; l'objet et les destinataires suffisent en secours.
- **Approbation renforcée pour toute pièce jointe** : trop de friction pour un document
  ordinaire ; seules les pièces sensibles l'exigent (décision à confirmer par le
  propriétaire).
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
