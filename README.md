# Relevés de marchés de prédiction (lecture seule)

Scripts qui lisent des API publiques. Aucun compte, aucun ordre, aucune clé.

| Fichier | Rôle |
|---|---|
| `polymarket_live.py` | Relève les carnets d'ordres de Polymarket toutes les 30 secondes quand le meilleur vendeur d'une issue est entre 0,98 et 0,999, puis évalue des règles d'achat au prix vendeur réellement affiché (`settle`). |
| `file_5m.py` | Relève chaque seconde, autour de la fin des marchés crypto « Up or Down » de 5 minutes, les ordres d'achat en attente à 0,90 et plus (taille de la file). |
| `pistes_live.py` | Deux pistes sur Polymarket. Récompenses de liquidité : montant par marché rapporté à la liquidité déjà posée (toutes les 3 h). Arbitrages de cohérence dans les événements à une seule issue gagnante : carnets relus en continu (un balayage dure environ 3 min), frais de preneur lus sur chaque marché (`feeSchedule`) et taille exécutable compris. Univers principal : événements d'au moins 1 000 $ de volume ; toutes les 3 h, un passage sur les autres (lignes `"u": 1`). |
| `pistes_markout.py` | Une fois par jour : sur un échantillon de marchés récompensés, résultat des ordres posés qui ont été servis, mesuré 5 min, 60 min et en fin de fenêtre après chaque transaction. |
| `pistes_analyse.py` | Lecture des relevés des pistes : récompenses par dollar posé, solde une fois déduites les pertes sur ordres servis, paniers nets positifs (fréquence, taille, durée). |
| `kalshi_replay.py` | Rejoue sur Kalshi l'achat de contrats entre 0,98 et 0,999 à partir des chandeliers horaires du meilleur vendeur. |
| `kalshi_check.py`, `kalshi_check2.py`, `kalshi_final.py` | Recalcul indépendant des résultats Kalshi à partir de `kalshi/rows.csv` : contrôle du tirage, sensibilité, puis chiffres du passage complet (toutes strates de volume, cellules, marchés sans chandelier). |
| `diag.py` | Vérifie que les API répondent. |

## Sorties

- `data/` : relevés des carnets Polymarket (`obs-*.jsonl.gz`), marchés connus (`meta.jsonl`), une ligne par tranche dans `etat.jsonl`.
- `file/` : mesures de la file d'attente (`file-*.jsonl.gz`), une ligne par tranche dans `etat.jsonl`.
- `file/pistes_polymarket/` : relevé des pistes (`arb-*.jsonl.gz` : paniers, résumés de balayage `cycle` et `cycle_large`, liste des événements suivis `univers` ; `rec-*.jsonl.gz` ; `mko-AAAAMMJJ.json` ; `evenements.jsonl`, `recompenses_meta.jsonl`, `etat.jsonl`, `etat_markout.jsonl`, `exemples.json`).
- `kalshi/` : rejeu Kalshi (`rows.csv`, `summary.json`, `status.json` ; `DONE` quand il est terminé).
- `status/bilan.txt` : bilan des règles sur les relevés, recalculé à chaque exécution de `releve2`.

## Tâches (GitHub Actions)

`releve2`, `file2` et `kalshi2`. Chaque exécution dure environ 5 h 30 et programme elle-même la suivante dès son démarrage ; elle repart toujours du dernier état de `main`.
Les anciennes tâches `releve`, `file`, `kalshi` et `bilan` sont désactivées : leur déclencheur horaire n'est jamais parti.

Le relevé des pistes n'a pas encore sa propre tâche : `file_5m.py` le lance à côté de lui à chaque tranche (provisoire). Un fichier `file/pistes_polymarket/ARRET` l'empêche de démarrer.

Pour relancer une tâche à la main : modifier `relance/releve.txt`, `relance/file.txt` ou `relance/kalshi.txt`.

Les relevés s'arrêtent d'eux-mêmes le 7 décembre 2026 à 8 h UTC.
