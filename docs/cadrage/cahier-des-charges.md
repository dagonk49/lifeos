LifeOS • Version de conception 1.0 • 11 octobre 2026

Statut : document de cadrage — les technologies proposées restent à confirmer par prototypage.

> Document source, conservé tel quel. Ce qui a été retenu à la place de certaines
> propositions, et l'état de réalisation de chaque exigence : [realisation.md](../realisation.md).

# 1. Objet et vision

LifeOS est une application personnelle d’assistance et de coaching : un interlocuteur unique, accessible par texte, voix, images et documents, avec tableau de bord contextuel. Elle organise le quotidien, suit les objectifs, mémorise les informations pertinentes et prépare des actions, sans déposséder l’utilisateur de ses décisions importantes.

Périmètre : usage personnel d’abord, extensions éventuelles ensuite. La confidentialité et la fiabilité priment sur une autonomie illimitée.

# 2. Décisions produit validées

| Domaine       | Exigence validée                                                                                               |
|---------------|----------------------------------------------------------------------------------------------------------------|
| Interaction   | Interface hybride : tableau de bord + conversation IA permanente ; texte, voix, photos, captures et documents. |
| Autonomie     | Tâches simples automatisées ; validation avant actions importantes.                                            |
| Mémoire       | Mode Premium ; conversations brutes conservées 90 jours ; souvenirs importants protégés.                       |
| Données       | Mémoire persistante sur serveur personnel ; IA cloud sollicitée de façon ponctuelle.                           |
| Vie privée    | Moteur local de filtrage et de permissions avant toute transmission cloud.                                     |
| Traitements   | Événements importants en temps réel ; bilans approfondis planifiés.                                            |
| Notifications | Adaptatives : alertes pertinentes, regroupement des suggestions, plages de tranquillité.                       |
| IA            | Orchestrateur central ; agents spécialisés activés uniquement à la demande.                                    |
| Coaching      | Conseils honnêtes, constructifs, contextualisés et non culpabilisants.                                         |

# 3. Profils, accès et espaces

- Compte propriétaire unique pour V1 ; architecture extensible à plusieurs utilisateurs sans mélange de données.

- Espaces personnel et professionnel isolés logiquement ; règles d’accès, mémoire et partage séparées.

- Authentification forte, sessions révocables et liste des appareils autorisés.

- Permissions par source, finalité, durée et agent ; possibilité de retrait immédiat.

# 4. Fonctionnalités par module

## F01 — Conversation multimodale

- Saisie texte et pièces jointes ; dictée et lecture vocale si activées.

- Conversation contextuelle liée à l’écran courant, sans exposition implicite de données non autorisées.

- Transcription et analyse de documents ; affichage des informations extraites et possibilité de correction.

- Historique consultable, recherche, export et suppression selon la politique de conservation.

## F02 — Accueil et tableau de bord

- Briefing contextualisé : priorités, tâches, agenda, finances et suggestions utiles.

- Cartes configurables, vue quotidienne/hebdomadaire, accès direct à l’assistant.

- Affichage des sources et de la fraîcheur des informations importantes.

## F03 — Tâches et calendrier

- Création, classification, échéances, rappels, dépendances et suivi de résolution.

- Détection des conflits d’agenda et proposition de créneaux, sans modification sensible silencieuse.

- Synchronisation bidirectionnelle seulement avec consentement et connecteur compatible.

## F04 — Finances

- Import des opérations via source autorisée ou saisie manuelle ; catégorisation corrigeable.

- Budgets, dépenses récurrentes, objectifs d’épargne et prévisions explicitement incertaines.

- Aucune opération bancaire ni paiement sans consentement explicite et contrôle supplémentaire.

## F05 — Journal et coaching

- Résumé quotidien des faits, accomplissements, difficultés et ressentis volontairement partagés.

- Bilans hebdomadaires et mensuels avec progrès, obstacles, suggestions concrètes et nuance.

- Différenciation des faits, hypothèses et jugements ; corrections utilisateur prioritaires.

## F06 — Objectifs et projets

- Objectifs mesurables ou qualitatifs, jalons, indicateurs, révisions et archivage.

- Suivi de projets personnels/professionnels, tâches associées, décisions et raisons.

- Suggestions adaptatives respectant les contraintes de temps et les préférences.

## F07 — Mémoire Premium

- Conversations brutes : expiration automatique à 90 jours, sauf éléments explicitement conservés.

- Extraction de faits, événements, préférences, décisions, sources et dates ; recherche hybride et temporelle.

- Souvenirs importants protégés, éditables, exportables et supprimables par le propriétaire.

- Détection de contradictions, vieillissement des préférences, traçabilité des origines et contrôle des erreurs.

## F08 — Automatisation et agents

- Orchestrateur central, agents finance/planning/mémoire/coaching/projets/documents à la demande.

- Outils locaux privilégiés pour calculs, règles et planification ; cloud pour raisonnement complexe.

- Historique de chaque action : initiateur, données consultées, autorisation, résultat, erreur.

## F09 — Notifications

- Critique : immédiat si risque réel et permission de contourner le silence.

- Important : créneau opportun ; suggestions : regroupement dans briefing/bilan.

- Plages de tranquillité, anti-doublons, fréquence par catégorie et désactivation ciblée.

## F10 — Gestion documentaire

- Extraction locale préalable des informations sensibles lorsque techniquement possible.

- Conservation des originaux seulement si utile, autorisée et avec durée définie.

- Pièces jointes liées aux événements et retrouvables par provenance.

# 5. Politique d’autonomie et approbations

| Niveau                 | Exemples                                                             | Règle                                       |
|------------------------|----------------------------------------------------------------------|---------------------------------------------|
| Automatique            | Classer une dépense, résumer, détecter un doublon                    | Exécution journalisée et corrigeable        |
| Sous règle explicite   | Créer un rappel, déplacer une tâche personnelle                      | Autorisation limitée et révocable           |
| Validation obligatoire | Envoyer un message, modifier événement partagé, partager un document | Aperçu exact + accord ponctuel              |
| Validation renforcée   | Paiement, destruction importante, changement de permissions          | Authentification adaptée ; refus par défaut |

Les instructions contenues dans un e-mail, une page web ou un document importé ne constituent jamais une autorisation. Le moteur d’autorisation est indépendant du modèle IA.

# 6. Vie privée et sécurité fonctionnelle

- Minimisation des données : transmettre au cloud seulement les extraits nécessaires, après filtrage local.

- Masquage/pseudonymisation des données sensibles et séparation des contextes personnel/professionnel.

- Consentement explicite, audit des accès, gestion des durées de conservation et export complet.

- Aucune promesse de « zéro rétention cloud » sans vérification des conditions du fournisseur et des réglages API.

- Sauvegardes chiffrées, test de restauration et procédure de perte/vol d’appareil.

# 7. Parcours utilisateurs de référence

## UC01 — Journal oral

L’utilisateur dicte sa journée ; LifeOS extrait tâches et accomplissements, propose les corrections, enregistre les faits validés et programme le suivi.

## UC02 — Ticket de caisse

Une photo est analysée ; les montants, la date et le marchand sont vérifiables avant ajout au budget.

## UC03 — Achat important

L’agent Finance calcule la marge budgétaire ; Coaching compare aux objectifs ; Core formule une recommandation avec incertitudes.

## UC04 — Action externe

LifeOS prépare un e-mail, affiche destinataire, contenu et pièces jointes, puis attend une confirmation explicite.

## UC05 — Droit à l’oubli

L’utilisateur recherche un souvenir, corrige ou supprime ses données ; la suppression se propage aux index et sauvegardes selon la politique documentée.

# 8. Exigences non fonctionnelles (objectifs initiaux à mesurer)

| Critère          | Cible de conception                                                                                |
|------------------|----------------------------------------------------------------------------------------------------|
| Disponibilité    | Mode lecture des données locales malgré indisponibilité du fournisseur IA ; dégradation explicite. |
| Performance      | Navigation courante fluide ; tâches simples sans appel cloud ; temps IA indiqué à l’utilisateur.   |
| Fiabilité        | Actions idempotentes lorsque possible ; reprise sur erreur et absence de doubles écritures.        |
| Observabilité    | Logs structurés sans données personnelles brutes ; métriques, alertes et traçabilité.              |
| Accessibilité    | Navigation clavier, lecteurs d’écran, contrastes et sous-titres/transcriptions.                    |
| Interopérabilité | API versionnée, exports standards JSON/CSV/ICS selon les données.                                  |
| Réversibilité    | Migration de fournisseur IA et de connecteurs sans perdre la mémoire structurée.                   |

# 9. Critères d’acceptation prioritaires

- Un utilisateur peut consulter, corriger, exporter et supprimer ses souvenirs.

- Les conversations brutes sont automatiquement purgées au terme des 90 jours, sauf conservation explicitement autorisée ; les index correspondants sont traités.

- Une tentative d’envoi de mail sans confirmation est bloquée et journalisée.

- Une pièce jointe professionnelle n’est pas accessible à l’agent Finance sans permission.

- Une notification non critique respecte les plages de tranquillité.

- Une indisponibilité cloud ne bloque ni la consultation des tâches ni le calcul local du budget.

- Chaque recommandation financière importante montre les hypothèses et données utilisées.

- Les bilans distinguent clairement les faits des interprétations.

# 10. Hors périmètre initial et risques

- Pas d’autonomie financière transactionnelle, de diagnostic médical ou de décisions irréversibles sans contrôle humain.

- Les intégrations bancaires, messageries et systèmes mobiles dépendent des API, des droits et des réglementations.

- Le filtrage automatique de documents n’est pas infaillible : prévoir des refus et validations.

- La mémoire « Premium » ne garantit pas un rappel parfait : mesurer précision, erreurs et oublis.

# 11. Phasage fonctionnel

| Phase | Contenu                                                                                                                      |
|-------|------------------------------------------------------------------------------------------------------------------------------|
| MVP   | Compte, conversation texte, tableau de bord, tâches, journal, mémoire structurée, recherche, consentements, rappels simples. |
| V1    | Bilans, objectifs, documents/images, notifications adaptatives, agents à la demande, export et audit.                        |
| V1.5  | Voix, calendrier et e-mail connectés, finances via import sécurisé, workflows avec approbations.                             |
| V2    | Connecteurs avancés, automatisations contextuelles, amélioration continue de la mémoire et fonctions mobiles natives.        |
