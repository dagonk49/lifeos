# ADR 0003 — Gemini comme fournisseur LLM, derrière une abstraction

- Statut : acceptée ; point 4 amendé par l'[ADR 0007](0007-couts-gemini-niveau-gratuit.md)
- Date : 2026-10-11

## Contexte

Le propriétaire a choisi Gemini (Google). L'architecture exige des modèles interchangeables
(réversibilité), des sorties structurées validées, aucun secret côté client, et une
transmission cloud minimale après filtrage local.

## Décision

1. **Abstraction** `LLMProvider` (module `lifeos.modules.llm`) avec deux opérations :
   génération de texte et génération structurée validée par un schéma Pydantic. Implémentations :
   - `gemini` : SDK officiel `google-genai`, compatible avec la **Gemini Developer API** et
     **Vertex AI** (choix par configuration) ;
   - `fake` : réponses déterministes pour les tests et le développement hors ligne ;
   - `disabled` (défaut) : toute demande échoue proprement → mode dégradé explicite.
2. **Modèle configuré explicitement** (`LIFEOS_GEMINI_MODEL`), sans valeur par défaut dans le
   code : la gamme Gemini évolue vite et les modèles « preview » sont retirés avec un préavis
   court. Utiliser un nom de modèle **stable** et versionné, pas un alias `-latest`.
3. **Passerelle cloud** (module `guardian`) : aucun module métier n'appelle le fournisseur
   directement. Chaque appel passe par la passerelle qui :
   - demande l'autorisation au Guardian (espace autorisé, consentement cloud de l'espace,
     finalité) ;
   - construit un paquet de contexte minimal et masque les données sensibles détectées
     localement (Privacy Engine) ;
   - vérifie le budget mensuel et refuse au-delà du seuil ;
   - journalise l'appel (finalité, modèle, jetons, issue) **sans le contenu**.
4. **Conditions d'utilisation** (amendé par l'ADR 0007 : en France, les conditions des
   services payants s'appliquent aussi au quota gratuit ; le niveau gratuit sans compte de
   facturation est retenu par défaut) :
   - le **niveau gratuit** de la Gemini Developer API est **interdit avec des données réelles** :
     selon les conditions de Google, les contenus des services non payants peuvent servir à
     améliorer leurs produits et être lus par des relecteurs humains ;
   - utiliser un projet avec facturation activée (services payants), ou Vertex AI dans une région
     européenne si la résidence des données dans l'UE est exigée ;
   - **à vérifier à la mise en service** : durée de conservation des requêtes pour la détection
     d'abus, options de rétention nulle, région de traitement. Le cahier des charges interdit
     toute promesse de « zéro rétention » non vérifiée.

## Conséquences

- Changer de fournisseur = écrire un adaptateur ; la mémoire structurée reste en local.
- Les tests n'ont jamais besoin d'une clé d'API.
- Le mode dégradé (fournisseur indisponible ou désactivé) est un chemin normal du produit.

## Alternatives écartées

- Appels HTTP directs à l'API REST : plus de code à maintenir que le SDK officiel.
- Frameworks d'orchestration (LangChain, etc.) : abstraction supplémentaire non nécessaire au
  MVP, surface d'attaque et dépendances accrues.
