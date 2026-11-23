#!/usr/bin/env python3
"""Relevé en direct des carnets Polymarket et évaluation des règles figées.

Lecture seule : API publiques, aucun compte, aucun ordre. Python 3.9+, bibliothèque
standard uniquement.

Utilisation
  python3 polymarket_live.py collect [--dir releve]     # tourne jusqu'à Ctrl-C
  python3 polymarket_live.py settle  [--dir releve]     # après règlement des marchés

collect
  - toutes les 10 min : univers des marchés ouverts dont la fin prévue tombe entre
    maintenant - 10 jours et maintenant + 36 h (volume >= 1 000 $) ;
  - toutes les 3 min : carnets de toutes les issues candidates (meilleur vendeur Oui
    >= 0,85, ou meilleur acheteur Oui <= 0,15, donc Non >= 0,85, ou match en cours) ;
  - toutes les 30 s : carnets des issues « chaudes » (fin prévue dans moins de 6 h, fin
    dépassée, match en cours).
  Une ligne est écrite dans obs.jsonl quand le meilleur vendeur d'une issue est entre
  0,98 et 0,999 (relevés à 30 s : seulement si le prix ou la taille change, ou toutes les
  5 min), et une ligne « gone » quand une offre quitte la tranche.

settle
  Lit obs.jsonl et meta.jsonl, interroge gamma-api pour les marchés réglés, applique
  les règles figées (RULES ci-dessous) au premier relevé de chaque issue qui les remplit,
  au meilleur prix vendeur réellement affiché, pour des mises de 100, 500, 1 000 et
  2 500 $ (la mise est plafonnée à la taille affichée jusqu'à 0,995 ou 0,999).
"""
import argparse
import gzip
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

GAMMA = "https://gamma-api.polymarket.com/markets/keyset"
GAMMA_ONE = "https://gamma-api.polymarket.com/markets/"
BOOKS = "https://clob.polymarket.com/books"
UA = {"User-Agent": "releve-lecture-seule/1.0", "Content-Type": "application/json"}

# Règles observées, figées le 21/11/2026 à 19 h 45 UTC d'après le rejeu (document,
# section « Règles retenues »). Aucune n'est validée à prix exécutables : ce relevé sert à
# les tester, pas à les appliquer.
# Champs : cat (None = toutes), disc (discipline sportive, None = toutes), sub (None = tous),
# ref ("start" = heure de début du match, "end" = fin prévue), t_min / t_max en secondes
# par rapport à ref, m_min / m_max = tranche du prix milieu (acheteur + vendeur) / 2,
# p_max = prix vendeur maximal accepté, b_min = meilleur acheteur minimal du même côté
# (écarte les offres isolées sans acheteur en face).
SOCCER = set("""fifwc fif chi lib sud bra bra2 epl es2 mls sea lal nor j1100 j2100 chi1 spl egy1 ucl uefa uel
uecl per1 ere elc col mex dfb itsb por fl1 bun bl2 tur arg aus kor jap ksa sau den swe sui bel aut sco gre cze
pol rou cro srb usl ecu uru par ven bol cdr fac efl ligamx mar1 ind1 tha1 chn1 jpn kor1 isl fin1 nwsl uwcl afc
caf conc gold copa wcq""".split())
RULES = [
    {"nom": "F1 football, 2-4 h après le coup d'envoi, milieu 0,990-0,995", "cat": "sport", "disc": "soccer", "sub": None,
     "ref": "start", "t_min": 2 * 3600, "t_max": 4 * 3600, "m_min": 0.990, "m_max": 0.995, "p_max": 0.999, "b_min": 0.95},
    {"nom": "F2 football plus/moins, 2-4 h après le coup d'envoi, milieu 0,990-0,995", "cat": "sport", "disc": "soccer",
     "sub": "plus_moins", "ref": "start", "t_min": 2 * 3600, "t_max": 4 * 3600, "m_min": 0.990, "m_max": 0.995,
     "p_max": 0.999, "b_min": 0.95},
    {"nom": "C1 crypto, après la fin prévue, milieu 0,990-0,995", "cat": "crypto", "disc": None, "sub": None,
     "ref": "end", "t_min": 0, "t_max": 3600, "m_min": 0.990, "m_max": 0.995, "p_max": 0.999, "b_min": 0.95},
    {"nom": "T0 témoin : tous marchés, toutes fenêtres, milieu 0,990-0,995", "cat": None, "disc": None, "sub": None,
     "ref": "end", "t_min": -10 ** 9, "t_max": 10 ** 9, "m_min": 0.990, "m_max": 0.995, "p_max": 0.999, "b_min": 0.95},
]

FEE = {"sport": 0.05, "tech": 0.05, "finance": 0.05, "autre": 0.05, "economie": 0.01, "culture": 0.01,
       "meteo": 0.01, "mentions": 0.01, "sans_frais": 0.0}


STAT = {"ok": 0, "echecs": 0, "derniere_erreur": ""}


def http_json(url, data=None, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=UA,
                                         method="POST" if data is not None else "GET")
            with urllib.request.urlopen(req, timeout=30) as r:
                STAT["ok"] += 1
                return json.loads(r.read().decode())
        except Exception as e:
            STAT["derniere_erreur"] = (type(e).__name__ + " " + str(e))[:200] + " @ " + url[:80]
            time.sleep(1.5 * (i + 1))
    STAT["echecs"] += 1
    return None


def tms(s):
    if not s:
        return 0
    s = str(s).replace(" ", "T")
    if s.endswith("+00"):
        s = s[:-3] + "+00:00"
    s = s.replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except Exception:
        return 0


def category(m):
    """Même classement que rp_analyze.js (abrégé)."""
    ft = m.get("ft") or ""
    q = (m.get("q") or "").lower()
    if ft.startswith("sports") or m.get("gst"):
        cat = "sport"
    elif ft.startswith("crypto"):
        cat = "crypto"
    elif ft.startswith("weather"):
        cat = "meteo"
    elif ft.startswith("finance"):
        cat = "finance"
    elif ft.startswith("politics"):
        cat = "politique"
    elif ft.startswith("culture"):
        cat = "culture"
    elif ft.startswith("economics"):
        cat = "economie"
    elif ft.startswith("tech"):
        cat = "tech"
    elif ft.startswith("mentions"):
        cat = "mentions"
    elif ft in ("nofee", "zero_fees"):
        cat = "sans_frais"
    else:
        cat = "autre"
    sub = "autre"
    if cat == "sport":
        if not m.get("gst"):
            sub = "saison"
        elif "exact score" in q:
            sub = "score_exact"
        elif "o/u" in q or "over/under" in q:
            sub = "plus_moins"
        elif "spread" in q or "handicap" in q:
            sub = "handicap"
        elif "both teams to score" in q:
            sub = "deux_buts"
        elif "draw" in q:
            sub = "nul"
        elif m.get("smt") == "moneyline" or " vs" in q or " win on " in q:
            sub = "vainqueur"
    fee = FEE.get(cat, 0.05)
    if cat == "politique":
        elect = any(w in q for w in ("election", "elected", "primary", "nominee", "presidential", "governor", "senate", "mayor"))
        sub = "elections" if elect else "autre"
        fee = 0.05 if elect else 0.01
    if cat == "crypto":
        fee = 0.05 if (("bitcoin" in q or "btc" in q) and "up or down" in q and ("5m" in m.get("sl", "") or "15m" in m.get("sl", ""))) else 0.01
    disc = ""
    if cat == "sport":
        league = (m.get("ev") or "").split("-")[0]
        if league in SOCCER or sub in ("nul", "score_exact", "deux_buts") or re.search(r" fc\b|united|city|real |atletico|sporting", q):
            disc = "soccer"
        else:
            disc = "autre"
    return cat, sub, fee, disc


def collect(d, max_seconds=None, until=None):
    os.makedirs(d, exist_ok=True)
    t_start = time.time()
    if until and t_start >= until:
        print("Date de fin dépassée : relevé terminé.")
        sys.exit(3)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    meta_path = os.path.join(d, "meta.jsonl")
    obs_path = os.path.join(d, "obs.jsonl" if max_seconds is None else f"obs-{stamp}.jsonl")
    n_obs = n_cycles = 0
    known = set()
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            for line in f:
                try:
                    known.add(json.loads(line)["id"])
                except Exception:
                    pass
    cand, last = {}, {}
    last_u = last_f = 0
    print("Relevé lancé ; Ctrl-C pour arrêter.", flush=True)
    while True:
        now = time.time()
        if (max_seconds and now - t_start > max_seconds) or (until and now >= until):
            break
        try:
            if now - last_u > 600:
                old_cand, cand = cand, {}
                a = datetime.fromtimestamp(now - 10 * 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                b = datetime.fromtimestamp(now + 36 * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                cur = None
                with open(meta_path, "a") as mf:
                    for _ in range(400):
                        url = f"{GAMMA}?closed=false&limit=100&volume_num_min=1000&end_date_min={a}&end_date_max={b}"
                        if cur:
                            url += "&after_cursor=" + urllib.parse.quote(cur)
                        j = http_json(url)
                        if not j:
                            break
                        for m in j.get("markets", []):
                            try:
                                o = json.loads(m["outcomes"])
                                tk = json.loads(m["clobTokenIds"])
                            except Exception:
                                continue
                            if len(o) != 2 or len(tk) != 2 or not m.get("enableOrderBook"):
                                continue
                            ev = (m.get("events") or [{}])[0]
                            meta = {"id": m["id"], "cid": m.get("conditionId"), "q": (m.get("question") or "")[:140], "sl": m.get("slug"),
                                    "ev": ev.get("slug", ""), "o": o, "tk": tk, "end": tms(m.get("endDate")), "gst": tms(m.get("gameStartTime")),
                                    "ft": m.get("feeType") or "", "smt": m.get("sportsMarketType") or "", "v": round(m.get("volumeNum") or 0)}
                            if m["id"] not in known:
                                mf.write(json.dumps(meta) + "\n")
                                known.add(m["id"])
                            ms = now * 1000
                            in_play = meta["gst"] and ms > meta["gst"] - 30 * 60000 and ms < meta["gst"] + 5 * 3600000
                            hot = bool(in_play) or meta["end"] - ms < 6 * 3600000
                            ba = float(m.get("bestAsk") or 0)
                            bb = m.get("bestBid")
                            if ba >= 0.85 or in_play:
                                cand[tk[0]] = (m["id"], 0, hot)
                            if (bb is not None and float(bb) <= 0.15) or in_play:
                                cand[tk[1]] = (m["id"], 1, hot)
                        cur = j.get("next_cursor")
                        if not cur or len(j.get("markets", [])) < 100:
                            break
                if not cand and old_cand:  # réseau coupé (veille) : on garde l'univers précédent
                    cand = old_cand
                last_u = time.time()
                print(f"{datetime.now(timezone.utc):%H:%M:%S} univers : {len(cand)} issues candidates", flush=True)
            full = now - last_f > 180
            toks = list(cand) if full else [t for t, v in cand.items() if v[2]]
            out = []
            ts = int(time.time() * 1000)
            for i in range(0, len(toks), 50):
                j = http_json(BOOKS, [{"token_id": t} for t in toks[i:i + 50]])
                if not j:
                    continue
                for bk in j:
                    c = cand.get(bk.get("asset_id"))
                    if not c:
                        continue
                    asks = sorted(((float(x["price"]), float(x["size"])) for x in bk.get("asks", [])), key=lambda x: x[0])
                    bids = sorted(((float(x["price"]), float(x["size"])) for x in bk.get("bids", [])), key=lambda x: -x[0])
                    a = asks[0][0] if asks else None
                    k = bk["asset_id"]
                    if a is None or a < 0.98 or a > 0.999:
                        if k in last:
                            out.append({"t": ts, "id": c[0], "s": c[1], "a": a, "gone": 1, "b": bids[0][0] if bids else 0, "h": 0 if full else 1})
                            del last[k]
                        continue
                    s995 = sum(s for p, s in asks if p <= 0.995)
                    s999 = sum(s for p, s in asks if p <= 0.999)
                    rec = {"t": ts, "id": c[0], "s": c[1], "a": a, "as": asks[0][1], "s995": round(s995), "s999": round(s999),
                           "b": bids[0][0] if bids else 0, "bs": bids[0][1] if bids else 0, "h": 0 if full else 1}
                    L = last.get(k)
                    if not full and L and L["a"] == a and abs(L["as"] - rec["as"]) < 1 and ts - L["t"] < 300000:
                        continue
                    last[k] = rec
                    out.append(rec)
            if out:
                with open(obs_path, "a") as f:
                    for r in out:
                        f.write(json.dumps(r) + "\n")
                n_obs += len(out)
            n_cycles += 1
            if full:
                last_f = time.time()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print("erreur :", e, file=sys.stderr, flush=True)
        time.sleep(30)
    etat = {"debut": datetime.fromtimestamp(t_start, timezone.utc).isoformat(), "fin": datetime.now(timezone.utc).isoformat(),
            "cycles": n_cycles, "observations": n_obs, "issues_candidates": len(cand), "marches_connus": len(known), "http": STAT}
    with open(os.path.join(d, "etat.jsonl"), "a") as f:
        f.write(json.dumps(etat, ensure_ascii=False) + "\n")
    print(json.dumps(etat, ensure_ascii=False))


def wilson(n, k, z=1.959964):
    if n == 0:
        return (0, 1)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def settle(d):
    meta = {}
    with open(os.path.join(d, "meta.jsonl")) as f:
        for line in f:
            m = json.loads(line)
            meta[m["id"]] = m
    obs = []
    for name in sorted(os.listdir(d)):
        if not name.startswith("obs"):
            continue
        opener = gzip.open if name.endswith(".gz") else open
        with opener(os.path.join(d, name), "rt") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not r.get("gone"):
                    obs.append(r)
    obs.sort(key=lambda r: r["t"])
    # résultats
    res = {}
    ids = sorted({r["id"] for r in obs})
    for i, mid in enumerate(ids):
        j = http_json(GAMMA_ONE + str(mid))
        if j and j.get("closed"):
            try:
                pay = [float(x) for x in json.loads(j["outcomePrices"])]
                res[mid] = pay
            except Exception:
                pass
        if i % 200 == 0:
            print(f"règlements lus : {i}/{len(ids)}", flush=True)
    for rule in RULES:
        taken = set()
        rows = []
        for r in obs:
            m = meta.get(r["id"])
            if not m or r["id"] not in res:
                continue
            cat, sub, fee, disc = category(m)
            if rule.get("cat") and cat != rule["cat"]:
                continue
            if rule.get("disc") and disc != rule["disc"]:
                continue
            if rule.get("sub") and sub != rule["sub"]:
                continue
            ref = m["gst"] if rule["ref"] == "start" else m["end"]
            if not ref:
                continue
            dt = (r["t"] - ref) / 1000
            if not (rule["t_min"] <= dt < rule["t_max"]):
                continue
            bid = r.get("b") or 0
            if bid < rule.get("b_min", 0) or r["a"] > rule["p_max"]:
                continue
            mid = (r["a"] + bid) / 2
            if not (rule["m_min"] - 1e-9 <= mid <= rule["m_max"] + 1e-9):
                continue
            key = (r["id"], r["s"])
            if key in taken:
                continue
            taken.add(key)
            rows.append((r, res[r["id"]][r["s"]], fee))
        print(f"\nRègle {rule['nom']} : {len(rows)} achats")
        for stake in (100, 500, 1000, 2500):
            n = L = 0
            pnl = cost = 0.0
            for r, pay, fee in rows:
                p = r["a"]
                cap = r["s995"] if p <= 0.995 else r["s999"]
                shares = min(stake / p, cap)
                if shares < 5:
                    continue
                f = fee * p * (1 - p) * shares
                n += 1
                L += pay < 1
                pnl += shares * (pay - p) - f
                cost += shares * p + f
            lo, hi = wilson(n, L)
            if n:
                print(f"  mise {stake:>5} $ : {n} achats, {L} pertes, réussite {100*(1-L/n):.2f} %, "
                      f"perte {100*lo:.2f}-{100*hi:.2f} %, rendement net {100*pnl/cost:.2f} %")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["collect", "settle"])
    ap.add_argument("--dir", default="releve")
    ap.add_argument("--max-seconds", type=float, default=None, help="arrêt après cette durée (un fichier obs par lancement)")
    ap.add_argument("--until", default=None, help="date de fin ISO en UTC, ex. 2026-12-06T21:00:00Z")
    a = ap.parse_args()
    if a.cmd == "collect":
        try:
            until = tms(a.until) / 1000 if a.until else None
            collect(a.dir, a.max_seconds, until)
        except KeyboardInterrupt:
            print("\nArrêté.")
    else:
        settle(a.dir)
