#!/usr/bin/env python3
"""Rejeu Kalshi des achats « quasi certains », sur prix vendeurs réels (chandeliers horaires).

Lecture seule : API publique, aucun compte, aucun ordre. Python 3.9+, bibliothèque standard.

Règle rejouée (écrite avant de lire la moindre donnée) : pour chaque marché réglé (hors
combinés), et pour chaque côté, la première heure des 7 jours avant la clôture où le prix
vendeur de clôture de l'heure tombe dans une tranche ET où le meilleur acheteur du même côté
est à 0,95 ou plus. Tranches : b0 = 0,980-0,989 ; b1 = 0,990-0,995 ; b2 = 0,996-0,999.
Côté Non : vendeur = 1 - acheteur Oui, acheteur = 1 - vendeur Oui.
Frais preneur : 0,07 x P x (1 - P) par contrat, arrondi au centime supérieur sur un ordre de
100 contrats.

Le travail se fait par tranches de temps (--budget-seconds) et reprend où il s'est arrêté :
  liste des marchés -> kalshi/markets.jsonl ; achats -> kalshi/rows.csv ;
  marchés traités -> kalshi/done.txt ; synthèse -> kalshi/summary.json ; état -> kalshi/status.json.
"""
import argparse
import csv
import json
import math
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

BASES = ["https://api.elections.kalshi.com/trade-api/v2", "https://external-api.kalshi.com/trade-api/v2"]
BASE = [BASES[0]]
UA = {"User-Agent": "rejeu-lecture-seule/1.0", "Accept": "application/json"}
STATUS = {"http": {}, "erreurs": [], "exemples": {}}
LOCK = threading.Lock()
RATE = {"gap": 0.12, "next": 0.0}
BANDS = (("b0", 0.98, 0.9895), ("b1", 0.9895, 0.9955), ("b2", 0.9955, 0.9995))
SPLIT = datetime(2026, 8, 20, tzinfo=timezone.utc).timestamp()  # validation avant, découverte après


def note(code):
    with LOCK:
        STATUS["http"][str(code)] = STATUS["http"].get(str(code), 0) + 1


def get(path, params=None, tries=5):
    url = BASE[0] + path + ("?" + urllib.parse.urlencode(params) if params else "")
    for i in range(tries):
        with LOCK:
            now = time.time()
            wait = max(0.0, RATE["next"] - now)
            RATE["next"] = max(now, RATE["next"]) + RATE["gap"]
        if wait:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                note(r.status)
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            note(e.code)
            if e.code == 404:
                return None
            if e.code == 429:
                with LOCK:
                    RATE["gap"] = min(0.5, RATE["gap"] * 1.3)
                time.sleep(2 + 2 * i)
            else:
                with LOCK:
                    if len(STATUS["erreurs"]) < 20:
                        STATUS["erreurs"].append(f"{e.code} {path} {str(e.read()[:120])}")
                time.sleep(1 + i)
        except Exception as e:
            note(type(e).__name__)
            with LOCK:
                if len(STATUS["erreurs"]) < 20:
                    STATUS["erreurs"].append(f"{type(e).__name__} {path} {str(e)[:120]}")
            time.sleep(1 + i)
    return None


def num(d, key):
    """Lit un prix : champ <key>_dollars (chaîne en dollars) ou <key> (centimes ou dollars)."""
    if not isinstance(d, dict):
        return None
    v = d.get(key + "_dollars")
    if v is None:
        v = d.get(key)
        if v is None:
            return None
        v = float(v)
        return v / 100 if v > 1.0001 else v
    try:
        return float(v)
    except Exception:
        return None


def ts(s):
    try:
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def fee(p):
    return math.ceil(0.07 * 100 * p * (1 - p) * 100 - 1e-9) / 100 / 100  # dollars par contrat


def wilson(n, k, z=1.959964):
    if not n:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), (c + h) / d)


def save_status(d, **kw):
    STATUS.update(kw)
    STATUS["maj"] = datetime.now(timezone.utc).isoformat()
    STATUS["base"] = BASE[0]
    with open(os.path.join(d, "status.json"), "w") as f:
        json.dump(STATUS, f, ensure_ascii=False, indent=1)


VARIANTS = (
    lambda d: {"limit": 1000, "min_close_ts": d, "max_close_ts": d + 86400 - 1, "mve_filter": "exclude"},
    lambda d: {"limit": 1000, "min_close_ts": d, "max_close_ts": d + 86400 - 1},
    lambda d: {"limit": 1000, "status": "settled", "min_settled_ts": d, "max_settled_ts": d + 86400 - 1},
)
VARIANT = [0]


def pick_variant(day0):
    """Choisit le premier jeu de paramètres que l'API accepte pour lister les marchés d'un jour."""
    for i, v in enumerate(VARIANTS):
        j = get("/markets", v(day0), tries=2)
        if j is not None and j.get("markets"):
            VARIANT[0] = i
            STATUS["variante_liste"] = i
            return True
    STATUS["variante_liste"] = "aucune"
    return False


def page_rows(ms):
    out = []
    for m in ms:
        if str(m.get("ticker", "")).startswith("KXMVE"):
            continue
        v = m.get("volume_fp")
        if v is None:
            v = m.get("volume")
        try:
            v = float(v or 0)
        except Exception:
            v = 0.0
        out.append([m.get("ticker"), m.get("event_ticker"), ts(m.get("close_time")), m.get("result"), v, ts(m.get("open_time"))])
    return out


def list_day(day0, st, left, sink):
    """Avance la liste d'un jour, page par page, tant qu'il reste du temps. st = état repris d'un passage à l'autre."""
    while left() > 25 and not st.get("fin"):
        p = VARIANTS[VARIANT[0]](day0)
        if st.get("cur"):
            p["cursor"] = st["cur"]
        j = get(st.get("ep") or "/markets", p)
        if not j:
            if not st.get("n") and not st.get("ep") and not st.get("cur"):
                st["ep"] = "/historical/markets"  # un jour vide ou refusé : on essaie l'historique une fois
                continue
            st["echecs"] = st.get("echecs", 0) + 1
            if st["echecs"] >= 3 and st.get("ep") == "/historical/markets" and not st.get("n"):
                st["fin"] = True  # rien à lire pour ce jour
            return
        ms = j.get("markets", [])
        if ms and "marche" not in STATUS["exemples"]:
            STATUS["exemples"]["marche"] = {k: ms[0][k] for k in list(ms[0])[:60]}
        rows = page_rows(ms)
        st["n"] = st.get("n", 0) + len(ms)
        st["k"] = st.get("k", 0) + sink(rows)
        st["cur"] = j.get("cursor") or None
        if not st["cur"] or not ms:
            if not st["n"] and not st.get("ep"):
                st["ep"] = "/historical/markets"
                continue
            st["fin"] = True


def candles(ticker, series, close):
    p = {"start_ts": close - 7 * 86400, "end_ts": close, "period_interval": 60}
    c = get(f"/series/{series}/markets/{ticker}/candlesticks", p)
    src = "live"
    if not c or not c.get("candlesticks"):
        h = get(f"/historical/markets/{ticker}/candlesticks", p)
        if h and h.get("candlesticks"):
            c, src = h, "hist"
    return c, src


def process(m, series_cache):
    ticker, ev, close, res, vol, _ = m
    series = series_cache.get(ev) or (ev or ticker).split("-")[0]
    c, src = candles(ticker, series, close)
    if c is None and ev not in series_cache:
        e = get(f"/events/{ev}")
        s2 = ((e or {}).get("event") or {}).get("series_ticker")
        series_cache[ev] = s2 or series
        if s2 and s2 != series:
            series = s2
            c, src = candles(ticker, series, close)
    if c is None:
        return ticker, None, src
    ks = c.get("candlesticks") or []
    if ks and "chandelier" not in STATUS["exemples"]:
        STATUS["exemples"]["chandelier"] = ks[-1]
    rows, done = [], set()
    for k in ks:
        ya, yb = num(k.get("yes_ask"), "close"), num(k.get("yes_bid"), "close")
        if ya is None or yb is None:
            continue
        t = k.get("end_period_ts") or 0
        for side, ask, bid in (("yes", ya, yb), ("no", 1 - yb, 1 - ya)):
            if bid < 0.95 or ask >= 1:
                continue
            for name, lo, hi in BANDS:
                if lo <= ask < hi and (side, name) not in done:
                    done.add((side, name))
                    win = 1 if res == side else 0
                    rows.append([ticker, ev, series, side, name, round(ask, 4), round(bid, 4), t, close, round((close - t) / 3600, 2), win, int(vol)])
    return ticker, rows, src


def stats(rows, B=400):
    n = len(rows)
    if not n:
        return None
    L = sum(1 for r in rows if not r["win"])
    pnl = cost = 0.0
    cl = {}
    days = set()
    for r in rows:
        f = fee(r["ask"])
        pl, c = r["win"] - r["ask"] - f, r["ask"] + f
        pnl += pl
        cost += c
        a = cl.setdefault(r["ev"], [0.0, 0.0])
        a[0] += pl
        a[1] += c
        days.add(r["t"] // 86400)
    arr = list(cl.values())
    bs = []
    for _ in range(B):
        sp = sc = 0.0
        for _ in range(len(arr)):
            a = arr[random.randrange(len(arr))]
            sp += a[0]
            sc += a[1]
        bs.append(sp / sc)
    bs.sort()
    lo, hi = wilson(n, L)
    return {"n": n, "pertes": L, "perte_ic": [round(100 * lo, 3), round(100 * hi, 3)], "seuil": round(100 * cost / n, 3),
            "rdt": round(100 * pnl / cost, 3), "rdt_ic": [round(100 * bs[int(B * 0.025)], 3), round(100 * bs[int(B * 0.975) - 1], 3)],
            "jours": len(days), "evenements": len(arr)}


def window(h):
    return ">72h" if h > 72 else "24-72h" if h > 24 else "6-24h" if h > 6 else "1-6h" if h > 1 else "<=1h"


def summarize(d, cats):
    rows = []
    path = os.path.join(d, "rows.csv")
    if os.path.exists(path):
        with open(path) as f:
            for r in csv.reader(f):
                if len(r) < 12 or r[0] == "ticker":
                    continue
                rows.append({"ticker": r[0], "ev": r[1], "series": r[2], "side": r[3], "band": r[4], "ask": float(r[5]), "bid": float(r[6]),
                             "t": int(r[7]), "close": int(r[8]), "h": float(r[9]), "win": int(r[10]), "vol": int(r[11])})
    out = {"achats": len(rows), "cellules": {}}
    groups = {}
    for r in rows:
        per = "disc" if r["close"] >= SPLIT else "val"
        cat = cats.get(r["series"], "?")
        for key in (f"{per}|TOUT|toutes|{r['band']}", f"{per}|TOUT|{window(r['h'])}|{r['band']}", f"{per}|{cat}|toutes|{r['band']}",
                    f"{per}|{cat}|{window(r['h'])}|{r['band']}", f"{per}|TOUT|{'vol>=10000' if r['vol'] >= 10000 else 'vol<10000'}|{r['band']}"):
            groups.setdefault(key, []).append(r)
    for key in sorted(groups):
        if len(groups[key]) >= 30:
            out["cellules"][key] = stats(groups[key])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-05-20")
    ap.add_argument("--end", default="2026-11-19")
    ap.add_argument("--min-vol", type=float, default=500)
    ap.add_argument("--budget-seconds", type=float, default=1200)
    ap.add_argument("--dir", default="kalshi")
    ap.add_argument("--threads", type=int, default=8)
    a = ap.parse_args()
    t_begin = time.time()
    os.makedirs(a.dir, exist_ok=True)
    left = lambda: a.budget_seconds - (time.time() - t_begin)
    # base qui répond
    for b in BASES:
        BASE[0] = b
        s = get("/exchange/status", tries=2)
        if s is not None:
            STATUS["exemples"]["exchange_status"] = s
            break
    STATUS["exemples"]["historical_cutoff"] = get("/historical/cutoff", tries=2)
    save_status(a.dir, phase="liste")
    # --- 1) liste, jour par jour ---
    t0 = int(datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc).timestamp())
    t1 = int(datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc).timestamp())
    days = list(range(t0, t1, 86400))
    days_path, mk_path = os.path.join(a.dir, "days.json"), os.path.join(a.dir, "markets.jsonl")
    done_days = json.load(open(days_path)) if os.path.exists(days_path) else {}
    for k, v in list(done_days.items()):
        if isinstance(v, list):  # ancien format : jour terminé
            done_days[k] = {"n": v[0], "k": v[1], "fin": True}
    todo = [x for x in days if not done_days.get(str(x), {}).get("fin")]
    random.shuffle(todo)
    if todo and not pick_variant(days[-1]):
        save_status(a.dir, phase="liste impossible")
        return
    if todo:
        mf = open(mk_path, "a")

        def sink(rows):
            kept = 0
            with LOCK:
                for r in rows:
                    if r[3] in ("yes", "no") and r[4] >= a.min_vol and r[0] and r[2]:
                        mf.write(json.dumps(r) + "\n")
                        kept += 1
            return kept

        def one(day):
            st = done_days.setdefault(str(day), {})
            list_day(day, st, left, sink)
        with ThreadPoolExecutor(a.threads) as ex:
            list(ex.map(one, todo))
        mf.close()
        json.dump(done_days, open(days_path, "w"))
    fin = sum(1 for x in days if done_days.get(str(x), {}).get("fin"))
    n_listed = sum(v.get("n", 0) for v in done_days.values())
    n_kept = sum(v.get("k", 0) for v in done_days.values())
    print(f"liste : {fin}/{len(days)} jours, {n_listed} marchés lus, {n_kept} retenus", flush=True)
    save_status(a.dir, phase="liste", jours_faits=fin, jours_total=len(days), marches_listes=n_listed, marches_retenus=n_kept)
    if fin < len(days) or left() < 90:
        return
    # --- 2) chandeliers, par volume décroissant ---
    markets = {}
    with open(mk_path) as f:
        for line in f:
            try:
                r = json.loads(line)
                markets[r[0]] = r
            except Exception:
                pass
    done_path, rows_path = os.path.join(a.dir, "done.txt"), os.path.join(a.dir, "rows.csv")
    done = set(open(done_path).read().split()) if os.path.exists(done_path) else set()
    todo = sorted((m for t, m in markets.items() if t not in done), key=lambda m: -m[4])
    series_cache, n_none, srcs = {}, 0, {}
    stop = [False]

    def work(m):
        if stop[0] or left() < 45:
            stop[0] = True
            return None
        return process(m, series_cache)
    with ThreadPoolExecutor(a.threads) as ex, open(rows_path, "a", newline="") as rf, open(done_path, "a") as df:
        w = csv.writer(rf)
        for res in ex.map(work, todo):
            if res is None:
                continue
            ticker, rows, src = res
            srcs[src] = srcs.get(src, 0) + 1
            if rows is None:
                n_none += 1
            else:
                w.writerows(rows)
            df.write(ticker + "\n")
            done.add(ticker)
    remaining = [m for t, m in markets.items() if t not in done]
    # --- 3) catégories des séries présentes, puis synthèse ---
    cats_path = os.path.join(a.dir, "series.json")
    cats = json.load(open(cats_path)) if os.path.exists(cats_path) else {}
    present = set()
    if os.path.exists(rows_path):
        with open(rows_path) as f:
            for r in csv.reader(f):
                if len(r) >= 12:
                    present.add(r[2])
    for s in sorted(present):
        if s in cats or time.time() - t_begin > a.budget_seconds + 120:
            continue
        j = get(f"/series/{s}", tries=2)
        cats[s] = (((j or {}).get("series") or {}).get("category") or "?")
    json.dump(cats, open(cats_path, "w"), ensure_ascii=False)
    summ = summarize(a.dir, cats)
    summ["couverture"] = {"marches_retenus": len(markets), "traites": len(done), "restants": len(remaining),
                          "volume_min_traite": min((markets[t][4] for t in done if t in markets), default=None),
                          "sans_chandeliers_ce_passage": n_none, "sources_ce_passage": srcs, "seuil_volume": a.min_vol}
    json.dump(summ, open(os.path.join(a.dir, "summary.json"), "w"), ensure_ascii=False, indent=1)
    save_status(a.dir, phase="fini" if not remaining else "chandeliers", traites=len(done), restants=len(remaining))
    if not remaining:
        open(os.path.join(a.dir, "DONE"), "w").write(datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    main()
