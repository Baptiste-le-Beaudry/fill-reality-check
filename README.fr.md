# fill-reality-check

*[English version: README.md](README.md)*

Votre backtest annonce un profit factor de 1,4. Ces exécutions étaient-elles vraiment possibles ?

`fill-reality-check` est un petit outil local en ligne de commande. Vous lui donnez un
CSV de trades crypto. Il re-simule chaque trade sur les barres 1 minute publiques de
Binance et produit un rapport de robustesse en Markdown et en HTML. Le rapport sépare les
**erreurs certaines** (démontrables à partir de vos données et des barres de la bourse)
des **risques estimés** (sensibilités et statistiques qui dépendent d'hypothèses).

L'outil vient de l'audit d'un vrai bot : 2 549 trades avec un profit factor de 1,38 sont
passés à 0,95 quand les stops ont été exécutés comme le ferait un vrai ordre. Voir
[docs/case_study.md](docs/case_study.md) (en anglais).

## Ce qui est vérifié

**Erreurs certaines**

| Code | Contrôle |
|---|---|
| C1 / C2 | Prix d'entrée ou de sortie hors de la plage haut/bas de la barre 1 minute correspondante |
| C3 | Entrée horodatée avant la clôture de la bougie signal (look-ahead) |
| C4 | Sortie sur stop remplie à un meilleur prix que l'ouverture d'une barre qui a sauté le niveau (gap) |
| C5 | Stops vérifiés sur les clôtures mais remplis au niveau du stop : l'outil trouve la convention qui reproduit vos rendements et re-simule les mêmes trades de trois façons (au niveau sur clôture / sortie au close / stop au repos intrabar) |
| C6 | Niveau du stop traversé intrabar avant la sortie déclarée |
| C7 | Positions qui se chevauchent sur un même symbole (si vous déclarez une seule position par symbole) |
| C8 / C9 | Problèmes d'entrée (doublons, sortie avant l'entrée, côté inconnu) et trades introuvables sur les barres |

**Indices de look-ahead** (des erreurs si votre signal utilise la clôture de cette barre)

| Code | Contrôle |
|---|---|
| H1 | Entrée horodatée à l'ouverture d'une barre mais remplie à sa clôture |
| H2 | Entrée remplie à la clôture de la barre signal |
| H3 | Minutes manquantes dans la fenêtre du trade (un signal calculé à travers un trou de données est souvent un artefact) |

**Risques estimés**

- Sensibilité aux coûts et à la latence : PF et net par trade selon un coût
  supplémentaire par côté (0 à 0,20 %) et une latence de 0, 1, 2 ou 5 barres ; coût
  aller-retour du point mort.
- Bootstrap par blocs de la série nette journalière (blocs de jours, pas de trades).
- Probabilistic Sharpe Ratio ; Deflated Sharpe Ratio si vous donnez le nombre de
  variantes essayées (`--n-trials`) ; PBO (CSCV) si vous fournissez une matrice de
  rendements par variante (`--trials-matrix`).
- Concentration (résultat sans les 1 % ou 5 % meilleurs trades et sans les 5 meilleurs
  jours), résultats par année, et frais déclarés comparés au palier de base de Binance.

## Installation

Python 3.11 ou plus récent. Dépendances : `numpy` et `pandas` seulement.

```bash
git clone <ce dépôt> fill-reality-check
cd fill-reality-check
python -m pip install -e .            # ou : pip install -e ".[test]" pour lancer les tests
```

## Utilisation

```bash
fill-reality-check trades.csv --rules rules.toml --out mon_rapport --n-trials 120
```

L'outil écrit `mon_rapport/report.md`, `mon_rapport/report.html` (autonome, sans
ressource externe), `mon_rapport/flagged_trades.csv` (une ligne par trade signalé) et
`mon_rapport/summary.json`.

Les barres viennent de [data.binance.vision](https://data.binance.vision) : archives
mensuelles vérifiées avec la somme SHA-256 publiée, puis archives journalières et API
publique pour les derniers jours. Elles sont gardées en cache dans `.frc_cache/` (ignoré
par git). Aucune clé API n'est utilisée ni nécessaire. `--offline` n'utilise que le cache.
`--market um` prend les barres des perpétuels USD-M au lieu du spot. `--bars-dir DOSSIER
--bars-pattern "{symbol}.csv"` utilise vos propres fichiers de barres.

Exemple hors ligne, sans téléchargement :

```bash
python examples/make_synthetic.py
fill-reality-check examples/synthetic/trades.csv --rules examples/synthetic/rules.toml \
    --bars-dir examples/synthetic/bars --out examples/synthetic/report --n-trials 40
```

Des rapports d'exemple générés sont dans [examples/](examples/).

## Entrée : `trades.csv`

Une ligne par trade. Heures en UTC (ISO 8601 ; le suffixe `Z` ou `+00:00` est accepté).

| Colonne | Obligatoire | Exemple | Remarque |
|---|---|---|---|
| `symbol` | oui | `SOLUSDT` | symbole Binance (`SOL/USDT` est accepté) |
| `side` | oui | `long` / `short` | aussi `buy` / `sell` |
| `entry_time` | oui | `2024-03-02T14:32:00Z` | |
| `entry_price` | oui | `128.41` | |
| `exit_time` | oui | `2024-03-02T15:21:00Z` | |
| `exit_price` | oui | `129.02` | |
| `trade_id` | non | `17` | généré s'il manque |
| `signal_time` | recommandé | `2024-03-02T14:31:00Z` | heure d'ouverture de la barre signal |
| `exit_reason` | recommandé | `stop` / `trail` / `target` / `time` | |
| `stop_price` ou `stop_distance` | non | `126.10` ou `0.018` | stop par trade (distance en fraction) |
| `target_price` ou `target_distance` | non | `131.00` | objectif par trade |
| `fee_per_side_pct` | non | `0.05` | remplace le fichier de règles pour ce trade |

Sans `signal_time`, la barre signal est déduite : si le prix d'entrée est plus proche de
la clôture de la barre horodatée `entry_time` que de son ouverture, cette barre est la
barre signal (le trade a été rempli à sa clôture) ; sinon, c'est la barre précédente.

## Règles : `rules.toml`

Les hypothèses de votre backtest. Les sections sont facultatives, seules les clés comptent.
Voir l'exemple complet dans [README.md](README.md#rules-rulestoml) : frais par côté,
glissement, stop, objectif, trailing (activation et distance), nombre maximal de barres
(ou `"declared"`, la durée déclarée de chaque trade), une position par symbole, tolérance
de prix, nombre de variantes essayées, bootstrap, grille de sensibilité, marché.

## Conventions de remplissage

Les barres sont étiquetées par leur heure d'ouverture. Pour la barre signal `s` :

- **Vérifié sur clôture, rempli au niveau** (le raccourci de recherche) : entrée à
  `close[s]` ; stop, trailing et objectif testés sur chaque clôture 1 minute ; sortie
  enregistrée au niveau. Une règle qui ne regarde que les clôtures ne peut pas obtenir
  le niveau : cette convention n'est pas exécutable.
- **Sortie au close** : mêmes tests, sortie à cette clôture.
- **Stop au repos intrabar** : stops et trailing sont des ordres au repos testés sur le
  bas de chaque barre (le haut pour un short) ; un gap à travers le niveau est rempli à
  l'ouverture. Le niveau du trailing sur une barre utilise le pic observé jusqu'à la barre
  précédente. Un objectif n'est rempli que si la barre le traverse, jamais à un meilleur
  prix que l'objectif. Si une barre atteint le stop et l'objectif, le stop l'emporte.
- **Exécutable** : les mêmes sorties au repos, avec une entrée à l'ouverture de la barre
  `s + 1 + latence`.

Les sorties au temps sont remplies à la clôture de la dernière barre. Les distances de
stop et d'objectif sont gardées par rapport à votre prix d'entrée déclaré. Les coûts sont
déduits par trade en fraction aller-retour. Quand un choix était ambigu, l'option la moins
favorable a été retenue.

## Limites

- Barres 1 minute seulement. On ne sait rien de l'intérieur d'une barre au-delà de son
  ouverture, son haut, son bas et sa clôture : l'ordre du haut et du bas dans la barre
  n'est pas modélisé (on suppose que le stop passe en premier).
- Pas de carnet d'ordres : on suppose que votre taille s'exécute aux prix de la barre.
  Pas d'impact de marché, pas d'exécutions partielles, pas de funding, pas de pannes de
  la bourse.
- Frais : la table du palier de base est codée en dur (spot 0,10 % maker et taker ; USD-M
  0,02 % maker, 0,05 % taker). Vérifiez la grille actuelle.
- Sans variance mesurée des Sharpe de vos variantes, le DSR suppose que les variantes ne
  diffèrent que par du bruit. C'est une borne supérieure.
- L'outil vérifie les exécutions et les statistiques, pas votre logique de signal. « Aucune
  erreur certaine » ne veut pas dire qu'une stratégie est rentable.

## Tests

```bash
python -m pytest
```

## Provenance

Le moteur de remplissage (`fills.py`) est adapté d'un harness d'exécution interne
(v1.4.0) et de son diagnostic de réalisme des sorties. Les formules DSR, PSR et CSCV
suivent Bailey et López de Prado (2014) et Bailey, Borwein, López de Prado et Zhu (2017).

## Licence et avertissement

MIT, voir [LICENSE](LICENSE).

Cet outil fait une revue technique d'une simulation de trades. Ce n'est ni un conseil en
placement, ni une recommandation d'acheter ou de vendre un actif, ni une prévision de
rendement. Les résultats simulés ou passés ne garantissent pas les résultats futurs.
