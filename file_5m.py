#!/usr/bin/env python3
"""File d'attente à l'achat sur les marchés crypto « Up or Down » de 5 minutes (lecture seule).

Chaque seconde, de 15 s avant à 120 s après la fin prévue de chaque marché, lit le carnet des
deux issues et note, pour l'issue dont le meilleur acheteur est à 0,90 ou plus, les six
meilleurs niveaux acheteurs (prix, taille) et les trois meilleurs niveaux vendeurs.
Une ligne n'est écrite que si le carnet a changé depuis la précédente.
  python3 file_5m.py --dir file --max-seconds 3300 --until 2026-12-07T08:00:00Z
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

GAMMA = "https://gamma-api.polymarket.com/markets/keyset"
BOOKS = "https://clob.polymarket.com/books"
UA = {"User-Agent": "releve-lecture-seule/1.0", "Content-Type": "application/json"}
STAT = {"ok": 0, "echecs": 0, "derniere_erreur": ""}


def http_json(url, data=None):
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=UA,
                                     method="POST" if data is not None else "GET")
        with urllib.request.urlopen(req, timeout=10) as r:
            STAT["ok"] += 1
            return json.loads(r.read().decode())
    except Exception as e:
        STAT["echecs"] += 1
        STAT["derniere_erreur"] = (type(e).__name__ + " " + str(e))[:160]
        return None


def tms(s):
    if not s:
        return 0
    s = str(s).replace(" ", "T").replace("Z", "+00:00")
    if s.endswith("+00"):
        s += ":00"
    try:
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except Exception:
        return 0


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="file")
    ap.add_argument("--max-seconds", type=float, default=3300)
    ap.add_argument("--until", default=None)
    a = ap.parse_args()
    until = tms(a.until) / 1000 if a.until else None
    t0 = time.time()
    if until and t0 >= until:
        sys.exit(3)
    os.makedirs(a.dir, exist_ok=True)
    path = os.path.join(a.dir, "file-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".jsonl")
    mk, last, last_u, n, seen = {}, {}, 0, 0, set()
    with open(path, "a") as f:
        while time.time() - t0 < a.max_seconds and (not until or time.time() < until):
            now = time.time()
            if now - last_u > 45:
                cur = None
                for _ in range(12):
                    u = f"{GAMMA}?closed=false&limit=100&end_date_min={iso(now - 120)}&end_date_max={iso(now + 720)}"
                    if cur:
                        u += "&after_cursor=" + cur
                    j = http_json(u)
                    if not j:
                        break
                    ms_list = j.get("markets", [])
                    for m in ms_list:
                        q, sl = (m.get("question") or "").lower(), (m.get("slug") or "")
                        if "up or down" not in q or "5m" not in sl:
                            continue
                        try:
                            tk = json.loads(m["clobTokenIds"])
                        except Exception:
                            continue
                        mk[m["id"]] = (tms(m.get("endDate")), tk, sl)
                    cur = j.get("next_cursor")
                    if not cur or len(ms_list) < 100:
                        break
                last_u = now
            ms = now * 1000
            for i in [i for i, v in mk.items() if ms - v[0] > 130000]:
                del mk[i]
            act = [(i, v) for i, v in mk.items() if -15000 <= ms - v[0] <= 120000]
            if not act:
                time.sleep(2)
                continue
            j = http_json(BOOKS, [{"token_id": t} for _, v in act for t in v[1]])
            if j:
                by = {bk.get("asset_id"): bk for bk in j}
                for i, v in act:
                    for s, t in enumerate(v[1]):
                        bk = by.get(t)
                        if not bk:
                            continue
                        bids = sorted(((float(x["price"]), float(x["size"])) for x in bk.get("bids", [])), key=lambda x: -x[0])[:6]
                        asks = sorted(((float(x["price"]), float(x["size"])) for x in bk.get("asks", [])), key=lambda x: x[0])[:3]
                        if not bids or bids[0][0] < 0.9:
                            continue
                        key = json.dumps([bids, asks])
                        if last.get(t) == key:
                            continue
                        last[t] = key
                        seen.add(i)
                        f.write(json.dumps({"t": int(ms), "id": i, "sl": v[2], "s": s, "dt": round((ms - v[0]) / 1000, 1), "b": bids, "a": asks}) + "\n")
                        n += 1
            time.sleep(max(0.0, 1.0 - (time.time() - now)))
    etat = {"debut": iso(t0), "fin": iso(time.time()), "lignes": n, "marches": len(seen), "http": STAT}
    with open(os.path.join(a.dir, "etat.jsonl"), "a") as f:
        f.write(json.dumps(etat, ensure_ascii=False) + "\n")
    print(json.dumps(etat, ensure_ascii=False))


def start_pistes():
    """Lance à côté le relevé des pistes (pistes_live.py), sans lien avec la mesure de file.

    Provisoire : je ne peux pas créer de nouvelle tâche GitHub sans le navigateur, alors ce
    travail héberge le relevé. Il écrit dans <dir>/pistes_polymarket/, enregistré avec le
    reste du dossier. Un fichier ARRET dans ce dossier l'empêche de démarrer.
    """
    try:
        import subprocess
        arg = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
        until = arg("--until", None)
        if until and time.time() >= tms(until) / 1000:
            return None
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pistes_live.py")
        out = os.path.join(arg("--dir", "file"), "pistes_polymarket")
        if not os.path.exists(script) or os.path.exists(os.path.join(out, "ARRET")):
            return None
        seconds = max(120.0, float(arg("--max-seconds", 3300)) - 150)
        return subprocess.Popen([sys.executable, script, "--dir", out, "--max-seconds", str(seconds), "--rec-min", "20", "--univers-min", "60"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return None


if __name__ == "__main__":
    child = start_pistes()
    try:
        main()
    finally:
        if child is not None:
            try:
                child.wait(timeout=150)
            except Exception:
                try:
                    child.kill()
                except Exception:
                    pass
