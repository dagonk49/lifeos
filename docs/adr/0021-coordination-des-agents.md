# ADR 0021 — Coordination d'agents : étapes locales, une seule synthèse, coût borné

- Statut : proposée (à valider par le propriétaire)
- Date : 2026-10-30

## Contexte

Le cas d'usage UC03 du cahier des charges (« achat important ») demande que l'agent
Finances calcule la marge budgétaire, que l'agent Coaching compare aux objectifs, puis que
Core formule une recommandation avec ses incertitudes ; le §9 exige que chaque
recommandation financière importante montre les hypothèses et les données utilisées.
Jusqu'ici, chaque message n'utilisait qu'un agent à la fois (tâche 27), et rien ne bornait
le nombre d'appels au LLM d'une même requête (T15).

Options considérées :

1. **Un appel au LLM par agent, puis un appel de synthèse** (trois appels) : chaque agent
   « raisonne », mais le coût triple, le quota gratuit du jour s'épuise plus vite et chaque
   appel ajoute une surface d'injection. Les chiffres resteraient de toute façon à calculer
   localement (le LLM ne calcule pas les montants).
2. **Un seul appel avec tout le contexte des deux agents** : simple, mais l'appel verrait
   les opérations, les libellés et le journal du coach ; plus de séparation des droits.
3. **Étapes locales sous les droits de chaque agent, une seule synthèse sur leurs
   résultats** (retenue).

## Décision

- `POST /v1/agents/workflows/purchase` (montant exact, libellé), démarré par le
  propriétaire seulement (`workflow.purchase`, interdit aux agents).
- **Étape Finances**, locale, avec le principal de l'agent Finances limité à « finances » :
  solde du mois, budget restant avant et après l'achat, solde estimé du mois (projection
  locale) et marge après l'achat avec sa fourchette, moyennes des trois mois précédents,
  part de l'achat dans les revenus, mois de solde moyen nécessaires. Chaque chiffre porte une
  clé (`F1`…), l'agent qui l'a produit et son calcul. Hypothèses déterministes (mois
  incomplet, historique court, opérations non classées ou dans une autre devise, absence de
  solde bancaire, hypothèses de la projection).
- **Étape Coaching**, locale, avec le principal de l'agent Coaching limité à « objectifs »
  (jamais son journal ni ses bilans) : objectifs actifs (clé `G1`…), avancement, objectifs
  financiers (épargne, en euros), reste à atteindre, nombre de mois d'épargne prévue que
  représente l'achat.
- Un agent sans la permission est sauté, l'étape le dit (`missing`), et aucune de ses
  données n'apparaît. **Sans l'agent Finances, rien n'est envoyé au LLM.**
- **Synthèse** : un seul appel (`workflow.purchase`, palier rapide, sortie bornée), sur le
  seul JSON des résultats, présentés comme données. Réponse structurée : verdict, confiance,
  recommandation, raisons avec les clés citées, hypothèses, incertitudes.
- **Contrôle déterministe** de la synthèse : clés inconnues écartées ; chaque raison
  attribuée aux agents dont elle cite les clés (jamais selon le LLM) ; montants (€, %) écrits
  par le LLM sans correspondance avec un calcul signalés ; confiance ramenée à « faible » si
  une donnée manque ou si un montant n'est pas vérifié. Les chiffres affichés comme
  référence sont toujours ceux des étapes locales.
- **Coût borné par la passerelle** : l'exécution passe un `CallLimit` (1 appel) ; ses appels
  portent son identifiant (`llm_calls.correlation_id`) et sont comptés sous le même verrou
  que les quotas (ADR 0010). Au-delà : `429 workflow_call_limit`, mesuré et audité, rien
  n'est envoyé. Une requête rejouée avec la même clé d'idempotence rend le résultat gardé.
- **Trace** : table `agent_runs` (question, étapes, résultats, synthèse vérifiée, code
  d'erreur, nombre d'appels), un événement d'audit `agent.read` par étape (agent, ressources,
  éléments, sans contenu) et `workflow.purchase`. Conservée 90 jours (purge quotidienne avec
  les messages), exportée, effacée avec l'objectif qu'elle cite, supprimable.

## Conséquences

- Les permissions restent séparées : chaque étape lit avec les droits de son agent, et la
  synthèse ne voit ni opération, ni commerçant, ni catégorie, ni journal.
- Un appel au plus par évaluation : le coût est prévisible et le quota profond reste aux
  bilans.
- La synthèse est faite avec le principal du propriétaire (il l'a demandée) : le journal
  d'audit distingue les lectures des agents (`agent.read`) de l'appel (`llm.cloud_call`).
- Le LLM peut encore écrire un chiffre arrondi ou faux dans son texte : il est signalé, la
  confiance baisse, l'interface montre les chiffres calculés comme référence.
- Les objectifs « financiers » sont reconnus par des règles simples (devise, unité en
  euros, mots « épargne », « économiser », « apport »…) ; le seuil d'un « achat important »
  n'est pas fixé : **à définir par le propriétaire**.
- D'autres coordinations (par exemple Projets + Planification) réutiliseront le même
  schéma : étapes locales par agent, une synthèse, `CallLimit`, `agent_runs`.
