# Politique de données

Principe : les données restent sur le serveur personnel ; le cloud ne reçoit que des extraits
minimaux, filtrés localement, pour un espace dont le propriétaire a donné son consentement.
Les valeurs marquées **à valider** sont des propositions par défaut.

## 1. Durées de conservation

| Catégorie | Durée | Mécanisme |
|---|---|---|
| Messages de conversation (bruts) | **90 jours**, sauf conservation explicite | `purge_after` ; purge quotidienne avec événement d'audit (nombre supprimé) |
| Transcriptions audio (étape 4) | 90 jours | même mécanisme que les messages |
| Faits, souvenirs, journal, tâches | tant que le propriétaire ne les supprime pas | souvenirs `protected` jamais purgés automatiquement |
| Embeddings (étape 3) | durée de l'objet source | supprimés avec l'objet |
| Documents originaux (étape 4) | durée choisie à l'import, défaut 90 jours (**à valider**) | `retain_until`, aucune durée infinie par défaut |
| Sessions | expiration + 30 jours | purge quotidienne |
| Défis WebAuthn, jetons d'enrôlement | expiration + 1 jour | purge quotidienne |
| Clés d'idempotence | 24 heures | purge quotidienne |
| Journal d'audit | 1 an (**à valider**) | purge dédiée, seule exception au mode append-only |
| Journal des appels LLM (`llm_calls`) | 1 an (**à valider**) | ne contient aucun contenu |
| Logs applicatifs | 14 jours | rotation Docker ; aucun contenu personnel brut |
| Métriques Prometheus | 15 jours | rétention Prometheus |
| Sauvegardes | 14 quotidiennes + 4 hebdomadaires (≈ 35 jours) | `restic forget --prune` |

## 2. Effacement (droit à l'oubli)

- Supprimer un message, un fait, un souvenir ou une entrée de journal supprime la ligne, ses
  liens, ses embeddings et ses entrées de cache. L'audit ne conserve que l'identifiant de
  l'objet et l'action, **jamais le contenu** : il n'empêche donc pas l'effacement.
- Les faits extraits d'un message purgé restent ; leur provenance indique une source
  supprimée.
- **Sauvegardes** : elles ne sont pas réécrites. Une donnée supprimée disparaît des
  sauvegardes à la fin de leur rotation (**≈ 35 jours au plus**). En cas de restauration, les
  suppressions survenues après la date de la sauvegarde doivent être rejouées : le journal
  d'audit (identifiants d'objets supprimés) sert de liste de rejeu.
- Export complet sur demande : JSON (mémoire, journal, tâches), CSV (tâches, finances),
  ICS (tâches datées).

## 3. Transmission au fournisseur LLM (Gemini)

Conditions cumulatives pour tout appel cloud (vérifiées par le Guardian, hors LLM) :

1. le fournisseur est configuré et n'est pas désactivé ;
2. l'espace concerné a le consentement cloud activé (`spaces.cloud_ai_enabled`) ;
3. l'appelant (utilisateur ou agent) a accès aux données incluses ;
4. le budget mensuel de jetons n'est pas atteint.

Contenu transmis :

- uniquement le **paquet de contexte minimal** construit pour la requête (message courant,
  derniers échanges de la conversation, souvenirs pertinents du même espace) ;
- après **masquage local** des données détectées : adresses e-mail, numéros de téléphone,
  IBAN, numéros de carte bancaire (contrôle de Luhn), numéros de sécurité sociale ;
- jamais : identifiants, mots de passe, jetons, clés d'API, secrets.

Catégories sensibles proposées, jamais transmises même masquées (**à valider**) : santé
détaillée, identifiants bancaires, documents d'identité.

Fournisseur : services **payants** de Gemini ou Vertex AI uniquement (voir
[ADR 0003](adr/0003-gemini-derriere-une-abstraction.md)). Avant la mise en service, vérifier
et consigner ici : durée de conservation pour la détection d'abus, usage pour l'entraînement,
région de traitement.

## 4. Séparation des espaces

- Chaque donnée appartient à un espace (`personal` ou `professional`).
- Un agent n'accède à un espace et à une ressource que via une permission explicite
  (`agent_permissions`), limitée dans le temps et révocable.
- Les recherches et les paquets de contexte sont toujours filtrés par espace.

## 5. Journalisation

- Logs applicatifs structurés (JSON) : route, statut, durée, identifiant de requête. Jamais de
  corps de requête, de prompt, de réponse LLM, de pièce jointe, de jeton ni d'adresse e-mail.
- Audit : qui (acteur), quoi (action), sur quoi (identifiants), résultat, espace.
