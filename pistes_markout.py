#!/usr/bin/env python3
"""Piste 1, côté coût : ce que gagnent ou perdent les poseurs d'ordres sur les ordres servis.

Lecture seule, une fois par jour (UTC). Pour un tirage de marchés récompensés (tous ceux à
100 $ par jour ou plus, 200 tirés au sort entre 20 et 100 $, 200 en dessous de 20 $) :
  - transactions publiques des dernières 24 h (Data API v2, côté preneur connu) ;
  - prix minute par minute de l'issue Oui (CLOB prices-history).
Chaque transaction a un preneur et un poseur. Le résultat du poseur est mesuré contre le prix
5 min après, 60 min après et en fin de fenêtre : s'il a vendu à p, il gagne p - prix futur ;
s'il a acheté, prix futur - p. Seules les transactions vieilles d'au moins une heure comptent,
pour que les trois horizons portent sur les mêmes transactions.
Sortie : <dir>/mko-AAAAMMJJ.json, à rapprocher du montant des récompenses (pistes_analyse.py).
"""
import argparse
import bisect
import glob
import gzip
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

DATA = os.environ.get("PISTES_DATA", "https://data-api.polymarket.com")
CLOB = os.environ.get("PISTES_CLOB", "https://clob.polymarket.com")
UA = {"User-Agent": "releve-lecture-seule/1.0", "Accept": "application/json"}
STAT = {"ok": 0, "echecs": 0, "429": 0, "derniere_erreur": ""}
GAP = [0.25]
NEXT = [0.0]
LOCK = threading.Lock()


def http_json(url, tries=3):
    for i in range(tries):
        with LOCK:
            now = time.time()
            wait = NEXT[0] - now
            NEXT[0] = max(now, NEXT[0]) + GAP[0]
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                out = json.loads(r.read().decode())
            STAT["ok"] += 1
            return out
        except urllib.error.HTTPError as e:
            if e.code == 429:
                STAT["429"] += 1
                GAP[0] = min(1.0, GAP[0] * 1.5)
            try:
                body = e.read()[:160].decode("utf-8", "replace")
            except Exception:
                body = ""
            STAT["derniere_erreur"] = f"HTTP {e.code} {body!r} @ {url[:120]}"
        except Exception as e:
            STAT["derniere_erreur"] = (type(e).__name__ + " " + str(e))[:160] + " @ " + url[:120]
        if i + 1 < tries:
            time.sleep(1.5 * (i + 1))
    STAT["echecs"] += 1
    return None


def read_lines(pattern):
    for p in sorted(glob.glob(pattern)):
        f = gzip.open(p, "rt") if p.endswith(".gz") else open(p)
        with f:
            for line in f:
                try:
                    yield json.loads(line)
                except Exception:
                    pass


def trades(cid, t_stop, max_pages):
    """Transactions du marché, de la plus récente à la plus ancienne, jusqu'à t_stop (secondes)."""
    out, cur = [], None
    for _ in range(max_pages):
        j = http_json(f"{DATA}/v2/trades?condition={cid}&limit=1000" + ("&cursor=" + urllib.parse.quote(cur) if cur else ""))
        if not j or "data" not in j:
            return out, None
        data = j["data"] or []
        for x in data:
            try:
                out.append((int(x["timestamp"]), int(x["outcome_index"]), x["side"] == "BUY", float(x["price"]), float(x["size"])))
            except Exception:
                pass
        cur = (j.get("pagination") or {}).get("next_cursor")
        if not cur or not data or int(data[-1]["timestamp"]) < t_stop:
            return out, False
    return out, True


def one_market(m, now, hours, max_pages):
    t0 = now - hours * 3600
    tr, trunc = trades(m["c"], t0, max_pages)
    if trunc is None:
        return dict(m, erreur="transactions")
    tr = [x for x in tr if t0 <= x[0] <= now - 3600]
    res = dict(m, n=len(tr), tronque=bool(trunc), vol=0.0, mk0=0.0, mk5=0.0, mk60=0.0, mkfin=0.0)
    if not tr:
        return res
    start = min(x[0] for x in tr)
    res["couvert_h"] = round(((now - 3600) - (start if trunc else t0)) / 3600, 2)
    h = http_json(f"{CLOB}/prices-history?market={m['tk']}&startTs={int(start - 3600)}&endTs={int(now)}&fidelity=1")
    hist = sorted((int(x["t"]), float(x["p"])) for x in ((h or {}).get("history") or []))
    if not hist:
        return dict(res, erreur="prix")
    ts = [x[0] for x in hist]

    def price(t):
        i = bisect.bisect_right(ts, t) - 1
        return hist[max(i, 0)][1]
    last = hist[-1][1]
    for t, idx, buy, p, z in tr:
        buy_yes = buy == (idx == 0)          # le preneur achète la première issue (ou vend la seconde)
        py = p if idx == 0 else 1 - p
        sgn = 1 if buy_yes else -1           # le poseur a vendu la première issue : il gagne si son prix baisse ensuite
        res["vol"] += z * p
        res["mk0"] += z * sgn * (py - price(t))
        res["mk5"] += z * sgn * (py - price(t + 300))
        res["mk60"] += z * sgn * (py - price(t + 3600))
        res["mkfin"] += z * sgn * (py - last)
    for k in ("vol", "mk0", "mk5", "mk60", "mkfin"):
        res[k] = round(res[k], 2)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="pistes")
    ap.add_argument("--max-seconds", type=float, default=3000)
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    t_start = time.time()
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_path = os.path.join(a.dir, f"mko-{day}.json")
    if os.path.exists(os.path.join(a.dir, "ARRET")) or (os.path.exists(out_path) and not a.force):
        return
    snaps = {}
    for r in read_lines(os.path.join(a.dir, "rec-*.jsonl*")):
        snaps.setdefault(r["t"], []).append(r)
    if not snaps:
        return
    full = max(len(v) for v in snaps.values())
    t_snap = max(t for t, v in snaps.items() if len(v) >= 0.8 * full)      # dernier relevé complet
    rows = snaps[t_snap]
    meta = {m["c"]: m for m in read_lines(os.path.join(a.dir, "recompenses_meta.jsonl"))}
    # tk : jeton de la première issue (indice 0 dans les transactions), en général « Yes »
    pool = [{"c": r["c"], "tk": meta[r["c"]]["tk"], "r": r["r"], "cap": round(r.get("d1", 0) + r.get("d2", 0), 1), "mid": r.get("mid"), "v": r["v"]}
            for r in rows if r["c"] in meta and meta[r["c"]].get("tk")]
    rnd = random.Random(int(day))
    strata = {"A": [m for m in pool if m["r"] >= 100], "B": [m for m in pool if 20 <= m["r"] < 100], "C": [m for m in pool if m["r"] < 20]}
    sample, info = [], {}
    for k, ms in strata.items():
        pick = ms if k == "A" else rnd.sample(ms, min(200, len(ms)))
        info[k] = {"marches": len(ms), "tires": len(pick), "recompenses_par_jour": round(sum(m["r"] for m in ms), 2)}
        sample += [dict(m, strate=k) for m in pick]
    rnd.shuffle(sample)
    now = int(time.time())
    stop = [False]

    def work(m):
        if stop[0] or time.time() - t_start > a.max_seconds - 90:
            stop[0] = True
            return None
        try:
            return one_market(m, now, a.hours, a.max_pages)
        except Exception as e:
            return dict(m, erreur=(type(e).__name__ + " " + str(e))[:120])
    with ThreadPoolExecutor(4) as ex:
        done = [r for r in ex.map(work, sample) if r is not None]
    for r in done:
        r.pop("tk", None)
    out = {"t": now, "releve_recompenses": t_snap, "fenetre_h": a.hours, "strates": info, "faits": len(done), "prevus": len(sample),
           "complet": len(done) == len(sample), "delai": round(GAP[0], 3), "http": STAT, "marches": done}
    if len(done) >= 0.5 * len(sample):       # un passage trop incomplet n'est pas gardé : il sera refait à la tranche suivante
        json.dump(out, open(out_path, "w"), ensure_ascii=False)
    with open(os.path.join(a.dir, "etat_markout.jsonl"), "a") as f:
        f.write(json.dumps({k: v for k, v in out.items() if k != "marches"}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
