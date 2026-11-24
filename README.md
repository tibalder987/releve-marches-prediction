# Relevés de marchés de prédiction (lecture seule)

Scripts qui lisent des API publiques. Aucun compte, aucun ordre, aucune clé.

| Fichier | Rôle |
|---|---|
| `polymarket_live.py` | Relève les carnets d'ordres de Polymarket toutes les 30 secondes quand le meilleur vendeur d'une issue est entre 0,98 et 0,999, puis évalue des règles d'achat au prix vendeur réellement affiché (`settle`). |
| `file_5m.py` | Relève chaque seconde, autour de la fin des marchés crypto « Up or Down » de 5 minutes, les ordres d'achat en attente à 0,90 et plus (taille de la file). |
| `pistes_live.py` | Deux pistes sur Polymarket : montant des récompenses de liquidité rapporté à la liquidité déjà posée (toutes les 20 min), et arbitrages de cohérence dans les événements à une seule issue gagnante (chaque minute, frais et taille exécutable compris). |
| `kalshi_replay.py` | Rejoue sur Kalshi l'achat de contrats entre 0,98 et 0,999 à partir des chandeliers horaires du meilleur vendeur. |
| `kalshi_check.py`, `kalshi_check2.py` | Recalcul indépendant des résultats Kalshi à partir de `kalshi/rows.csv`, contrôle du tirage et sensibilité. |
| `diag.py` | Vérifie que les API répondent. |

## Sorties

- `data/` : relevés des carnets Polymarket (`obs-*.jsonl.gz`), marchés connus (`meta.jsonl`), une ligne par tranche dans `etat.jsonl`.
- `file/` : mesures de la file d'attente (`file-*.jsonl.gz`), une ligne par tranche dans `etat.jsonl`.
- `file/pistes_polymarket/` : relevé des pistes (`arb-*.jsonl.gz`, `rec-*.jsonl.gz`, `evenements.jsonl`, `recompenses_meta.jsonl`, `etat.jsonl`, `exemples.json`).
- `kalshi/` : rejeu Kalshi (`rows.csv`, `summary.json`, `status.json` ; `DONE` quand il est terminé).
- `status/bilan.txt` : bilan des règles sur les relevés, recalculé à chaque exécution de `releve2`.

## Tâches (GitHub Actions)

`releve2`, `file2` et `kalshi2`. Chaque exécution dure environ 5 h 30 et programme elle-même la suivante dès son démarrage ; elle repart toujours du dernier état de `main`.
Les anciennes tâches `releve`, `file`, `kalshi` et `bilan` sont désactivées : leur déclencheur horaire n'est jamais parti.

Le relevé des pistes n'a pas encore sa propre tâche : `file_5m.py` le lance à côté de lui à chaque tranche (provisoire). Un fichier `file/pistes_polymarket/ARRET` l'empêche de démarrer.

Pour relancer une tâche à la main : modifier `relance/releve.txt`, `relance/file.txt` ou `relance/kalshi.txt`.

Les relevés s'arrêtent d'eux-mêmes le 7 décembre 2026 à 8 h UTC.
