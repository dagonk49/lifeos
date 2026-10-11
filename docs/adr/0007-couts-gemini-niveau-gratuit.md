# ADR 0007 — Coûts Gemini : niveau gratuit sans facturation possible, quotas côté LifeOS

- Statut : acceptée (amende le point 4 de l'[ADR 0003](0003-gemini-derriere-une-abstraction.md))
- Date : 2026-10-11

## Contexte

Le propriétaire a un abonnement grand public Google AI Pro. Il veut utiliser la Gemini
Developer API « normale » (pas Vertex AI) **sans aucune facturation surprise**.

Recherche documentaire du 2026-10-11. Les sources sont citées en bas. Les pages officielles
n'étaient pas toutes accessibles directement, et plusieurs points reposent sur des extraits
concordants ; ils sont à revérifier si les conditions de Google changent.

1. **L'abonnement AI Pro n'inclut pas l'API.** Ses avantages s'appliquent dans l'interface
   web d'AI Studio. L'usage de l'API par clé est facturé et géré séparément.
2. AI Pro donne des **crédits Google Cloud** (Google Developer Program, 10 $/mois annoncés).
   Ils doivent être activés sur un **compte de facturation Cloud**. En facturation prépayée
   (« Prepay », mode par défaut depuis 2026), ils ne sont consommés que si le solde prépayé
   est positif. Utiliser ces crédits suppose donc d'attacher un moyen de paiement.
3. **Un projet sans compte de facturation est au niveau gratuit et ne peut pas être
   facturé.** Au-delà des quotas, l'API répond 429 et ne facture rien.
4. En prépayé, un solde à 0 $ coupe l'API (402) pour toutes les clés du compte. Les données
   de facturation ont environ 10 minutes de retard, et le solde peut passer légèrement
   négatif ; la dette est reprise sur l'achat suivant.
5. **Quotas gratuits.**
   - Ils ne sont plus publiés sous forme de tableau fixe : ils sont visibles dans AI Studio,
     par projet et par modèle, et remis à zéro à **minuit, heure du Pacifique** (≈ 9 h à
     Paris).
   - Ordres de grandeur rapportés : environ 500 requêtes par jour pour `gemini-3.5-flash-lite`,
     environ 20 par jour pour `gemini-3.8-flash`.
   - Les modèles Pro n'ont plus de niveau gratuit.
6. **Données, en France.** Dans l'EEE, en Suisse et au Royaume-Uni, les conditions des
   services payants s'appliquent aussi au quota gratuit : pas d'utilisation des contenus pour
   améliorer les produits Google, contrairement au niveau gratuit hors EEE.
   - Les mêmes conditions réservent aux services payants les « API Clients » mis à
     disposition d'utilisateurs dans l'EEE.
   - Pour un outil personnel dont le développeur est l'unique utilisateur, notre lecture est
     que cette clause ne s'applique pas. C'est une interprétation, à revérifier sur la page
     des conditions.

## Décision

1. **Niveau A (par défaut) : clé AI Studio d'un projet Google Cloud sans compte de
   facturation.**
   - Dans LifeOS : `LIFEOS_LLM_BILLING=free`.
   - **Zéro euro garanti par construction** : rien ne peut être facturé. Un quota épuisé
     donne un refus propre (`llm_quota_exceeded`), jamais un coût.
2. **Modèles.**
   - Palier `fast`, pour le chat, l'extraction et la consolidation : `gemini-3.5-flash-lite`,
     avec un raisonnement `minimal`.
   - Palier `deep`, pour les bilans : `gemini-3.8-flash`, avec un raisonnement `low`. Le
     niveau `minimal` est refusé par ce modèle ; l'adaptateur réessaie automatiquement en
     `low`, sans coût, car la réponse 400 n'est pas facturée.
   - Éviter les modèles 2.x (fermés aux nouveaux projets) et les `-preview`.
3. **Plafonds côté LifeOS**, appliqués par la passerelle avant tout appel :
   - **requêtes par jour et par palier** (400 / 15 par défaut, remises à zéro comme chez
     Google), à régler juste sous les limites affichées dans AI Studio ;
   - **budget mensuel de jetons** ;
   - **arrêt d'urgence** (`llm_paused`).

   En outre :
   - les erreurs 429 sont classées en quota du jour ou limite par minute ;
   - aucune nouvelle tentative automatique du SDK : chaque tentative compte dans le quota.

   Chaque appel est journalisé sans contenu (palier, modèle, jetons, coût estimé), et le
   tableau de bord ainsi que les réglages affichent la consommation du jour.
4. **Local d'abord, pour économiser le quota.** Plusieurs fonctions ne consomment aucune
   requête :
   - recherche (hybride, embeddings locaux, ADR 0006) ;
   - qualité de la mémoire ;
   - faits des bilans ;
   - progression des objectifs ;
   - contexte des agents.

   Le LLM n'est sollicité que pour :
   - les messages de l'assistant ;
   - l'analyse d'une entrée de journal ou la consolidation d'une conversation, à la demande ;
   - la lecture des bilans (une requête `deep`, désactivable). Les bilans automatiques ne
     tournent que si le propriétaire les active.
5. **Niveau B (optionnel, seulement par choix explicite).**
   - Rattacher un compte de facturation en **prépayé**, avec un petit montant et sans
     rechargement automatique (ou avec un plafond mensuel).
   - Passer `LIFEOS_LLM_BILLING=paid` : la passerelle applique alors des budgets en dollars
     (jour et mois, prix par palier configurables).
   - Le niveau de service Flex est utilisé pour les tâches de fond.
   - Attention : le prix de `gemini-3.8-flash` doublerait au 2027-01-01 selon la page des
     tarifs. Mettre à jour `LIFEOS_LLM_PRICE_*` en conséquence.

## Conséquences

- Un usage personnel typique tient largement dans les plafonds par défaut : quelques dizaines
  de messages par jour, une ou deux analyses de journal, un bilan quotidien et un bilan
  hebdomadaire.
- Quand le quota est atteint, l'assistant est indisponible jusqu'au lendemain 9 h environ.
  Tout le reste de LifeOS continue de fonctionner.
- Si Google modifie ses quotas gratuits, seul le réglage des plafonds change ; la garantie
  « zéro frais » reste vraie tant qu'aucun compte de facturation n'est rattaché au projet.
- Les plafonds sont vérifiés avant l'appel, sans réservation : deux appels simultanés peuvent
  dépasser un plafond d'une ou deux requêtes. Risque accepté (un seul utilisateur) : au-delà du
  quota de Google, l'API refuse sans facturer. Les appels en erreur comptent dans les plafonds
  et, s'ils ont consommé des jetons (réponse bloquée ou tronquée), dans le budget de jetons.

## Sources (consultées le 2026-10-11)

- Abonnements Google AI et API : <https://ai.google.dev/gemini-api/docs/google-ai-plans>
- Facturation (Prepay, crédits, 402) : <https://ai.google.dev/gemini-api/docs/billing>
- Avantages Google Developer Program : <https://developers.google.com/profile/help/faq>
- Quotas : <https://ai.google.dev/gemini-api/docs/rate-limits>
- Tarifs et modèles : <https://ai.google.dev/gemini-api/docs/pricing>,
  <https://ai.google.dev/gemini-api/docs/changelog>
- Conditions (usage des données, EEE) : <https://ai.google.dev/gemini-api/terms>
