# ADR 0002 — Authentification intégrée par passkeys

- Statut : acceptée ; précisée le 2026-10-30 (pas de client mobile à ce jour) ; amendée par l'ADR 0022 (passkeys du navigateur sur le téléphone, pas de client natif)
- Date : 2026-10-11

## Contexte

L'architecture proposait Authentik ou Keycloak (OIDC). En V1 il n'y a qu'un compte
propriétaire : un fournisseur d'identité séparé ajoute un service lourd à exploiter, à
sauvegarder et à mettre à jour, pour un seul utilisateur. Le cahier des charges exige une
authentification forte, des sessions révocables et la liste des appareils autorisés.

## Décision

L'authentification est intégrée au backend :

- **Passkeys (WebAuthn)** comme seul facteur de connexion, avec vérification utilisateur
  obligatoire (biométrie ou code de l'appareil). Une passkey est résistante au phishing et
  combine possession et vérification locale : c'est déjà une authentification forte.
- **Bootstrap et récupération par la ligne de commande du serveur** :
  `lifeos-admin create-owner` crée le propriétaire et émet un jeton d'enrôlement à usage
  unique (15 minutes) ; `lifeos-admin enrollment-token` en émet un nouveau en cas de perte de
  tous les appareils. Avoir un shell sur le serveur est le facteur de récupération.
- **Sessions côté serveur** : jeton aléatoire de 256 bits dans un cookie `HttpOnly`, `Secure`,
  `SameSite=Strict` ; seule son empreinte SHA-256 est stockée. Expiration absolue 30 jours,
  inactivité 7 jours, révocation unitaire ou globale, liste des sessions et des passkeys.
- **Protection CSRF** : `SameSite=Strict` + vérification de l'en-tête `Origin` sur toute
  requête non sûre.
- **Réauthentification récente** (moins de 10 minutes) exigée pour les opérations de
  « validation renforcée » (révoquer une passkey, modifier les permissions).
- Limitation de débit des routes `/v1/auth` au niveau de Traefik.

Le schéma (`users`, `passkey_credentials`, `user_sessions`) reste compatible avec l'ajout
ultérieur d'un fournisseur OIDC (table d'identités externes à ajouter).

## Écart par rapport à la proposition initiale (TOTP de secours)

Un code TOTP seul comme secours serait **plus faible** que la passkey (un seul facteur,
phishable). Le faire correctement imposerait mot de passe + TOTP, donc une seconde surface
d'attaque à maintenir. Le secours retenu est : plusieurs passkeys (téléphone, PC, clé de
sécurité) + récupération par le serveur. Un mot de passe + TOTP pourra être ajouté si un cas
d'usage réel l'exige (accès depuis un appareil sans passkey).

## Conséquences

- Aucun service d'identité supplémentaire à déployer.
- Le client web doit être servi sur un domaine stable (RP ID WebAuthn) en HTTPS ; `localhost`
  est accepté en développement.
- Le client mobile (plus tard) utilisera les passkeys Android (Credential Manager).
  Précision (2026-10-30) : aucun client mobile n'existe ; sur le téléphone, les passkeys
  s'utilisent dans le navigateur, avec le client web. Un client natif reste à décider
  (périmètre Android, [docs/README.md](../README.md#points-encore-ouverts)).
  Décidé par l'[ADR 0022](0022-client-mobile-web-dabord.md) : passkeys du navigateur, y
  compris dans l'application installée (qui a ses propres cookies : une connexion de plus).
  Un client natif demanderait `assetlinks.json` public et l'origine `android:apk-key-hash:`
  côté WebAuthn.

## Alternatives écartées

- Authentik / Keycloak : surdimensionné pour un utilisateur, réévaluable en multi-utilisateur.
- Mot de passe + TOTP comme facteur principal : phishable, moins ergonomique.
