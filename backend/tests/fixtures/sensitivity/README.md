# Corpus du filtre local de sensibilité

Documents français **entièrement fictifs** (aucune donnée personnelle réelle), un fichier
texte par document, tels que la lecture locale (couche texte d'un PDF ou OCR) les produit :
fautes d'OCR (`0` pour `O`, `1` pour `I`, `«` pour `<` dans la bande MRZ), espaces
insécables (U+00A0) et fines (U+202F), points, tirets, minuscules.

`manifest.json` associe chaque fichier aux catégories attendues (`identity`, `health`,
`bank`, ou aucune). Le test `test_documents_core.py::test_french_corpus` le vérifie, et la
même vérification se lance sur le serveur :

```bash
lifeos-admin sensitivity-check tests/fixtures/sensitivity \
  --expect tests/fixtures/sensitivity/manifest.json
```

Valeurs d'identifiants utilisées :

| Valeur | Nature | Clé |
|---|---|---|
| `FR76 3000 6000 0112 3456 7890 189` | IBAN d'exemple de la norme française | valide (mod 97) |
| `2 90 02 69 123 456 86` | NIR inventé | valide (86) |
| `1 85 05 78 006 084 36` | NIR inventé | **invalide** exprès (la bonne serait 91) : il est quand même reconnu |
| `4111 1111 1111 1111` | numéro de carte de test | valide (Luhn) |
| `123 456 782 00002` | SIRET inventé | valide (Luhn) : ce n'est pas une carte |
| `990000000001`, `99XX99999`, `9900000001`, `30 99 999 999 999` | numéros de pièce d'identité, de passeport, de titre de séjour et fiscal inventés | sans clé |
| `01 99 00 12 34`, `06 39 98 12 34` | plages réservées par l'ARCEP à la fiction | — |
| `…@example.org`, `…@example.com` | domaines réservés | — |

Le préfixe du nom de fichier dit l'attendu : `identite-`, `sante-`, `banque-` (sensible),
`ordinaire-` (non sensible), `prudence-` (sensible par prudence : le mot « passeport »
suffit, même dans une note de voyage).

Pour ajouter un faux négatif trouvé sur un vrai document : en écrire une **reproduction
synthétique** (même structure, mêmes mots-clés, valeurs inventées), l'ajouter ici avec son
attendu dans `manifest.json`, puis corriger les règles (`documents/sensitivity.py`,
`guardian/privacy.py`) et incrémenter `CLASSIFIER_VERSION`.
