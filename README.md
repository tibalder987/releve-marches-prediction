# Relevés de marchés de prédiction (lecture seule)

Scripts qui lisent des API publiques. Aucun compte, aucun ordre, aucune clé.

| Fichier | Rôle |
|---|---|
| `polymarket_live.py` | Relève les carnets d'ordres de Polymarket toutes les 30 secondes quand le meilleur vendeur d'une issue est entre 0,98 et 0,999, puis évalue des règles d'achat au prix vendeur réellement affiché (`settle`). |
| `kalshi_replay.py` | Rejoue sur Kalshi l'achat de contrats entre 0,98 et 0,999 à partir des chandeliers horaires du meilleur vendeur. |
| `diag.py` | Vérifie que les API répondent. |

Les relevés sont écrits dans `data/`, le rejeu Kalshi dans `kalshi/`, les diagnostics dans `status/`.
Le relevé s'arrête de lui-même le 7 décembre 2026 à 8 h UTC.
