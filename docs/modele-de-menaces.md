# Modèle de menaces

Version initiale (étape 0), à reprendre à chaque étape. Référence : OWASP Top 10 for LLM
Applications.

## 1. Actifs

| Actif | Sensibilité |
|---|---|
| Mémoire (faits, souvenirs, journal) | très élevée : vie privée, santé, relations |
| Conversations brutes | très élevée |
| Données financières (à venir) | élevée |
| Sessions et passkeys | élevée : accès à tout le reste |
| Clé d'API Gemini, secrets d'infrastructure | élevée : coût, accès |
| Sauvegardes | très élevée : copie complète |

## 2. Frontières de confiance

```
Navigateur / mobile ──HTTPS── Traefik ──┬── API ──┬── PostgreSQL (réseau interne)
                                        │         ├── Redis (réseau interne)
                                        └── Web   └── Passerelle cloud ──HTTPS── Gemini
Contenus importés (documents, e-mails, pages web) = NON FIABLES
Sorties du LLM = NON FIABLES (validées par schéma avant toute action)
```

## 3. Menaces et parades

| # | Menace | Parades | Étape |
|---|---|---|---|
| T1 | Phishing / vol d'identifiants | passkeys (résistantes au phishing), aucun mot de passe | 1 |
| T2 | Vol de cookie de session | `HttpOnly`, `Secure`, `SameSite=Strict`, empreinte seule en base, expiration absolue et d'inactivité, révocation, liste des sessions | 1 |
| T3 | CSRF | `SameSite=Strict` + vérification `Origin` sur les requêtes non sûres | 1 |
| T4 | Force brute / déni sur l'auth | limitation de débit Traefik, jetons d'enrôlement 256 bits à durée courte | 1 |
| T5 | Exposition des services internes | réseau Docker `internal`, seul Traefik publie 80/443, `/metrics` non routé | 1 |
| T6 | Secrets dans Git | `.gitignore`, secrets Docker, `.env.example` sans valeur | 1 |
| T7 | Fuite de données dans les logs | logs sans corps ni prompts ; audit sans contenu | 1 |
| T8 | Vol ou perte des sauvegardes | chiffrement restic, clé séparée des sauvegardes, copie hors machine | 1 |
| T9 | Dépendances compromises | versions verrouillées (`uv.lock`, `pnpm-lock.yaml`), audit des dépendances en CI | 1 |
| T10 | Injection de prompt indirecte (document, e-mail, contenu mémorisé) | les contenus importés ne valent jamais autorisation ; le Guardian décide hors LLM ; sorties structurées validées ; actions importantes soumises à approbation avec aperçu exact | 2 |
| T11 | Escalade d'agent | un agent ne peut pas s'octroyer de permission ; `agent_permissions` gérées uniquement par l'utilisateur avec réauthentification récente | 2 |
| T12 | Fuite inter-espaces | filtrage par espace dans chaque service ; tests dédiés ; RLS prévu en défense en profondeur | 2 |
| T13 | Fuite vers le fournisseur LLM | consentement par espace, paquet minimal, masquage local, budget, journal des appels | 2 |
| T14 | Exfiltration via la sortie du LLM (liens/images Markdown vers un domaine tiers) | réponses affichées en texte brut (aucun HTML ni Markdown interprété) ; CSP `img-src 'self' data:` et `connect-src 'self'` | 2 |
| T15 | Coût LLM incontrôlé (boucle, abus) | projet Gemini **sans compte de facturation** (rien ne peut être facturé, ADR 0007) ; plafonds quotidiens par palier et budget mensuel ; arrêt d'urgence ; aucune nouvelle tentative automatique ; limites de profondeur et d'appels par requête | 2, 3 |
| T16 | Hallucination présentée comme un fait | `origin` + `confidence` ; distinction fait / inférence dans l'interface ; corrections prioritaires | 2 |
| T17 | Fichiers malveillants | validation MIME/taille, quarantaine, antivirus, refus des formats dangereux | 4 |
| T18 | SSRF via connecteurs / URLs | liste d'hôtes autorisés, pas de requête vers des adresses privées | 5 |
| T19 | Vol de jetons OAuth de connecteurs | chiffrement au repos, scopes minimaux, révocation | 5 |
| T20 | Compromission de l'hôte / vol physique | chiffrement des volumes, mises à jour, pare-feu, accès distant par VPN | exploitation |
| T21 | Fuite par les embeddings (un vecteur permet en partie de reconstituer le texte) | calcul **local** (service `ml` sans accès sortant, modèle hors ligne), vecteurs dans la base comme le reste, filtrés par propriétaire et espace, supprimés par trigger avec l'objet source | 3 |
| T22 | Agent spécialisé qui lit trop | accès en lecture seulement aux ressources de l'agent, par espace, 90 jours, accordé avec réauthentification, révocable ; contexte construit hors LLM avec les droits de l'agent | 3 |
| T23 | Mémoire polluée par une extraction erronée (consolidation, journal) | propositions soumises à approbation avec aperçu exact ; seuls les messages du propriétaire sont analysés ; file « À vérifier » (doublons, contradictions) | 3 |

Limite connue : la CSP autorise `'unsafe-inline'` pour les scripts (hydratation Next.js) ;
passer à des nonces CSP est prévu à l'étape 6 (stabilisation).

## 4. Tests de sécurité associés

- Auth : rejeu de défi, défi expiré, jeton d'enrôlement réutilisé, session révoquée ou expirée,
  requête sans `Origin` valide.
- Guardian : action soumise à approbation exécutée sans approbation → refusée et auditée ;
  agent sans permission sur un espace → refusé ; permission expirée ou révoquée → refusée.
- Espaces : aucune recherche ni aucun paquet de contexte ne contient de donnée d'un autre
  espace.
- Cloud : espace sans consentement → aucun appel ; budget atteint → aucun appel ; données
  sensibles masquées dans le paquet envoyé.
