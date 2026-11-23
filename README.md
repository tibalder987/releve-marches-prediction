# Relevés de marchés de prédiction (lecture seule)

Scripts qui lisent des API publiques. Aucun compte, aucun ordre, aucune clé.

| Fichier | Rôle |
|---|---|
| `polymarket_live.py` | Relève les carnets d'ordres de Polymarket toutes les 30 secondes quand le meilleur vendeur d'une issue est entre 0,98 et 0,999, puis évalue des règles d'achat au prix vendeur réellement affiché (`settle`). |
| `file_5m.py` | Relève chaque seconde, autour de la fin des marchés crypto « Up or Down » de 5 minutes, les ordres d'achat en attente à 0,90 et plus (taille de la file). |
| `kalshi_replay.py` | Rejoue sur Kalshi l'achat de contrats entre 0,98 et 0,999 à partir des chandeliers horaires du meilleur vendeur. |
| `diag.py` | Vérifie que les API répondent. |

## Sorties

- `data/` : relevés des carnets Polymarket (`obs-*.jsonl.gz`), marchés connus (`meta.jsonl`), une ligne par tranche dans `etat.jsonl`.
- `file/` : mesures de la file d'attente (`file-*.jsonl.gz`), une ligne par tranche dans `etat.jsonl`.
- `kalshi/` : rejeu Kalshi (`rows.csv`, `summary.json`, `status.json` ; `DONE` quand il est terminé).
- `status/bilan.txt` : bilan des règles sur les relevés, recalculé à chaque exécution de `releve2`.

## Tâches (GitHub Actions)

`releve2`, `file2` et `kalshi2`. Chaque exécution dure environ 5 h 30 et programme elle-même la suivante dès son démarrage ; elle repart toujours du dernier état de `main`.
Les anciennes tâches `releve`, `file`, `kalshi` et `bilan` sont désactivées : leur déclencheur horaire n'est jamais parti.

Pour relancer une tâche à la main : modifier `relance/releve.txt`, `relance/file.txt` ou `relance/kalshi.txt`.

Les relevés s'arrêtent d'eux-mêmes le 7 décembre 2026 à 8 h UTC.
