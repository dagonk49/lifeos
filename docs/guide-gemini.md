# Configurer Gemini sans aucun frais

Objectif : brancher l'assistant de LifeOS sur la Gemini Developer API **sans qu'aucune
facturation ne soit possible** (voir [ADR 0007](adr/0007-couts-gemini-niveau-gratuit.md)).

L'abonnement Google AI Pro ne couvre pas l'API. Ce n'est pas un problème : le niveau gratuit
de l'API suffit pour un usage personnel, et un projet **sans compte de facturation** ne peut
rien coûter.

## 1. Créer la clé

1. Ouvrir <https://aistudio.google.com/apikey> avec votre compte Google.
2. « Create API key » : laisser AI Studio créer un **nouveau projet** (ou choisir un projet
   dédié à LifeOS).
3. **Ne pas** associer de compte de facturation à ce projet. Dans AI Studio, le projet doit
   apparaître au niveau « Free ». Si un jour un compte de facturation y est associé, la
   garantie « zéro frais » ne tient plus.
4. Copier la clé.

## 2. La donner à LifeOS

```bash
cd /opt/lifeos/infra
printf '%s' 'VOTRE_CLE' > secrets/lifeos_gemini_api_key   # jamais dans .env, jamais commitée
```

Dans `infra/.env` :

```dotenv
LIFEOS_LLM_PROVIDER=gemini
LIFEOS_GEMINI_BACKEND=developer
LIFEOS_GEMINI_MODEL=gemini-3.5-flash-lite
LIFEOS_GEMINI_MODEL_DEEP=gemini-3.8-flash
LIFEOS_LLM_BILLING=free
```

Puis `docker compose up -d` et, dans LifeOS, **Réglages → activer l'IA** pour chaque espace
concerné (personnel, professionnel).

## 3. Caler les plafonds sur vos vrais quotas

Dans AI Studio, la page des quotas (« Rate limits » / « Usage ») affiche, pour **votre
projet**, les requêtes par jour (RPD) autorisées pour chaque modèle. Régler les plafonds de
LifeOS un peu en dessous :

```dotenv
LIFEOS_LLM_DAILY_REQUESTS_FAST=400   # ≤ RPD de gemini-3.5-flash-lite
LIFEOS_LLM_DAILY_REQUESTS_DEEP=15    # ≤ RPD de gemini-3.8-flash
```

Les compteurs repartent de zéro à minuit, heure du Pacifique (vers 9 h à Paris), comme chez
Google. La consommation du jour est visible sur l'accueil et dans **Réglages → Assistant IA**.

## 4. Ce qui se passe quand le quota est atteint

L'assistant affiche « quota du jour atteint » et le reste de LifeOS fonctionne normalement :
tâches, journal, mémoire, recherche (y compris sémantique, calculée localement), objectifs et
faits des bilans. Rien n'est facturé.

## 5. Arrêt d'urgence

**Réglages → Assistant IA → Pause** : plus aucun appel cloud tant que la pause est active.

## 6. Confidentialité

- Seul un contexte minimal est envoyé, après masquage local des e-mails, téléphones, IBAN,
  cartes et numéros de sécurité sociale.
- En France (EEE), les conditions de Google appliquent au quota gratuit les règles des
  services payants : les contenus ne servent pas à améliorer les produits Google. Ils sont
  conservés un temps limité pour la détection d'abus.
- Les embeddings (recherche par le sens) sont calculés sur votre serveur et ne sont jamais
  envoyés à Google.
