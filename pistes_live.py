#!/usr/bin/env python3
"""Relevé en lecture seule de deux pistes sur Polymarket. Aucun compte, aucun ordre.

Piste 1, récompenses de liquidité
  Toutes les 3 h : liste des marchés récompensés (GET clob /rewards/markets/multi,
  sans authentification) puis carnet de chacun. Pour chaque marché on note le montant
  journalier, l'écart maximal admis autour du milieu, et la liquidité déjà posée dans cet
  écart, pondérée par la formule de score publiée : ((v - s) / v)^2 x taille.

Piste 2, arbitrage de cohérence
  Chaque minute : carnets de toutes les issues des événements « negative risk » (une seule
  issue peut gagner). On note les événements où
    - acheter toutes les issues Oui coûte moins de 1 (panier Oui, payé 1 à la résolution),
    - la somme des meilleurs acheteurs Oui dépasse 1 (panier Non : k issues Non achetées
      paient au moins k - 1),
  frais de preneur compris, avec la taille exécutable en descendant les carnets.

Bibliothèque standard uniquement. Les adresses peuvent être remplacées par les variables
PISTES_GAMMA et PISTES_CLOB (essais locaux).
"""
import argparse
import gzip
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from polymarket_live import category, tms  # même classement des frais que le relevé en direct

GAMMA = os.environ.get("PISTES_GAMMA", "https://gamma-api.polymarket.com")
CLOB = os.environ.get("PISTES_CLOB", "https://clob.polymarket.com")
EVENTS = GAMMA + "/events/keyset"
BOOKS = CLOB + "/books"
REWARDS = CLOB + "/rewards/markets/multi"
UA = {"User-Agent": "releve-lecture-seule/1.0", "Content-Type": "application/json", "Accept": "application/json"}

STAT = {"ok": 0, "echecs": 0, "429": 0, "derniere_erreur": ""}
GAP = [0.25]          # délai minimal entre deux requêtes, tous fils confondus (4 par seconde au plus)
NEXT = [0.0]
LOCK = threading.Lock()


def http_json(url, data=None, tries=4):
    for i in range(tries):
        with LOCK:
            now = time.time()
            wait = NEXT[0] - now
            NEXT[0] = max(now, NEXT[0]) + GAP[0]
        if wait > 0:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=UA,
                                         method="POST" if data is not None else "GET")
            with urllib.request.urlopen(req, timeout=30) as r:
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
            if i + 1 < tries:
                time.sleep(1.5 * (i + 1))
        except Exception as e:
            STAT["derniere_erreur"] = (type(e).__name__ + " " + str(e))[:160] + " @ " + url[:120]
            if i + 1 < tries:
                time.sleep(1.5 * (i + 1))
    STAT["echecs"] += 1
    return None


def num(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return d


# ---------------------------------------------------------------- univers (piste 2)
# Façons de lister les événements ouverts, essayées dans l'ordre ; la première qui répond est gardée.
# (nom, adresse, clé de la liste dans la réponse, pagination)
LISTINGS = [
    ("evenements_curseur_volume", GAMMA + "/events/keyset?closed=false&limit=100&volume_min=1000", "events", "after_cursor"),
    ("evenements_curseur", GAMMA + "/events/keyset?closed=false&limit=100", "events", "after_cursor"),
    ("evenements_curseur_20", GAMMA + "/events/keyset?closed=false&limit=20", "events", "after_cursor"),
    ("evenements_decalage", GAMMA + "/events?closed=false&limit=100", None, "offset"),
    ("marches_curseur", GAMMA + "/markets/keyset?closed=false&limit=100", "markets", "after_cursor"),
]


def leg_of(m, ev_slug):
    """Issue négociable d'un événement, ou None si elle ne l'est pas."""
    try:
        tk = json.loads(m.get("clobTokenIds") or "[]")
    except Exception:
        tk = []
    if not (len(tk) == 2 and m.get("enableOrderBook") and m.get("acceptingOrders") is not False and m.get("active") is not False):
        return None
    meta = {"ft": m.get("feeType") or "", "q": m.get("question") or "", "gst": tms(m.get("gameStartTime")),
            "smt": m.get("sportsMarketType") or "", "sl": m.get("slug") or "", "ev": ev_slug or ""}
    cat, _, fee, _ = category(meta)
    if m.get("feesEnabled") is False:
        fee = 0.0
    return {"id": m.get("id"), "tk": tk[0], "fee": fee, "cat": cat, "ft": meta["ft"],
            "o": bool(m.get("negRiskOther")), "g": (m.get("groupItemTitle") or "")[:40]}


def list_universe(samples, deadline, skip):
    """Événements ouverts « negative risk » d'au moins 1 000 $ de volume, avec leurs issues non closes.

    skip : variantes déjà essayées sans succès (refusées, ou sans aucun événement utile), à ne pas refaire.
    """
    essais = []
    for name, base, key, paging in LISTINGS:
        if name in skip:
            continue
        evs, groups, cur, off, pages, n_items, partial, started = {}, {}, None, 0, 0, 0, False, False
        while pages < 1500:
            if time.time() > deadline:
                partial = True
                break
            url = base + ("&after_cursor=" + urllib.parse.quote(cur) if cur else "") + (f"&offset={off}" if paging == "offset" and off else "")
            j = http_json(url, tries=4 if started else 1)
            items = (j.get(key) if isinstance(j, dict) else None) if key else (j if isinstance(j, list) else None)
            if items is None:
                partial = True
                break
            started = True
            pages += 1
            for it in items:
                n_items += 1
                if key == "markets":
                    # repli : les marchés un par un, regroupés par événement
                    if not it.get("negRisk") or it.get("closed"):
                        continue
                    e0 = (it.get("events") or [{}])[0]
                    gid = str(it.get("negRiskMarketID") or e0.get("id") or "")
                    if not gid:
                        continue
                    if "evenement_marche" not in samples:
                        samples["evenement_marche"] = it
                    g = groups.setdefault(gid, {"id": gid, "sl": e0.get("slug") or "", "ti": (e0.get("title") or "")[:100],
                                                "end": tms(e0.get("endDate") or it.get("endDate")), "aug": e0.get("negRiskAugmented"),
                                                "inc": False, "clos": 0, "legs": []})
                    leg = leg_of(it, g["sl"])
                    if leg is None:
                        g["inc"] = True
                    else:
                        g["legs"].append(leg)
                    continue
                e = it
                if "premier_evenement_cles" not in samples:
                    samples["premier_evenement_cles"] = sorted(e.keys())
                if not e.get("negRisk"):
                    continue
                if e.get("volume") is not None and num(e.get("volume")) < 1000:
                    continue
                if "evenement" not in samples:
                    samples["evenement"] = {k: v for k, v in e.items() if k != "markets"}
                    samples["evenement_marche"] = (e.get("markets") or [{}])[0]
                legs, n_closed, inc = [], 0, False
                for m in e.get("markets") or []:
                    if m.get("closed"):
                        n_closed += 1
                        continue
                    leg = leg_of(m, e.get("slug"))
                    if leg is None:
                        inc = True      # une issue non négociable : le panier Oui ne peut pas être complet
                    else:
                        legs.append(leg)
                if len(legs) >= 2:
                    evs[str(e.get("id"))] = {"id": str(e.get("id")), "sl": e.get("slug") or "", "ti": (e.get("title") or "")[:100],
                                             "end": tms(e.get("endDate")), "aug": e.get("negRiskAugmented"), "inc": inc,
                                             "clos": n_closed, "legs": legs}
            if paging == "offset":
                off += len(items)
                if len(items) < 100:
                    break
            else:
                cur = j.get("next_cursor")
                if not cur:
                    break
        if key == "markets":
            for g in groups.values():
                if partial:
                    g["inc"] = True     # liste incomplète : on ne peut pas affirmer qu'un panier Oui est complet
                if len(g["legs"]) >= 2:
                    evs[g["id"]] = g
        essais.append({"variante": name, "pages": pages, "lus": n_items, "negrisk": len(evs), "partiel": partial,
                       "erreur": "" if started else STAT["derniere_erreur"]})
        if evs:
            return evs, {"variante": name, "pages": pages, "evenements_lus": n_items, "negrisk": len(evs), "partiel": partial, "essais": essais}
        if not started or n_items >= 300:
            skip.add(name)
    return {}, {"variante": None, "pages": 0, "evenements_lus": 0, "negrisk": 0, "partiel": True, "essais": essais}


def fetch_books(tokens=None, groups=None):
    """Carnets par paquets de 50. Avec groups, un événement n'est pas réparti sur deux requêtes
    (sauf s'il a plus de 50 issues) : ses carnets sont lus au même instant."""
    out = {}

    def one(chunk):
        return http_json(BOOKS, [{"token_id": t} for t in chunk]) or []
    if groups is None:
        chunks = [tokens[i:i + 50] for i in range(0, len(tokens), 50)]
    else:
        chunks, cur = [], []
        for g in groups:
            if len(g) > 50:
                if cur:
                    chunks.append(cur)
                    cur = []
                chunks += [g[i:i + 50] for i in range(0, len(g), 50)]
            elif len(cur) + len(g) > 50:
                chunks.append(cur)
                cur = list(g)
            else:
                cur += g
        if cur:
            chunks.append(cur)
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(one, chunks):
            for bk in res if isinstance(res, list) else []:
                try:
                    asks = sorted(((float(x["price"]), float(x["size"])) for x in bk.get("asks") or []), key=lambda x: x[0])
                    bids = sorted(((float(x["price"]), float(x["size"])) for x in bk.get("bids") or []), key=lambda x: -x[0])
                    out[bk.get("asset_id")] = (asks, bids)
                except Exception:
                    continue
    return out


def basket(ladders, fees, mode):
    """Achète la même quantité de chaque jambe en descendant les carnets.

    ladders : par jambe, liste (prix, taille) du moins cher au plus cher.
    mode "Y" : panier Oui, payé 1 ; on s'arrête dès qu'une jambe est épuisée.
    mode "N" : panier Non, k jambes payées k - 1 ; une jambe épuisée est retirée du panier.
    Renvoie (marge nette de la première unité, quantité, gain net total).
    """
    legs = [{"l": l, "i": 0, "r": l[0][1], "f": f} for l, f in zip(ladders, fees) if l]
    if mode == "Y" and len(legs) != len(ladders):
        return None, 0.0, 0.0
    edge0, q, gain = None, 0.0, 0.0
    for _ in range(100000):
        if len(legs) < (1 if mode == "Y" else 2):
            break
        cost = sum(g["l"][g["i"]][0] * (1 + g["f"] * (1 - g["l"][g["i"]][0])) for g in legs)
        edge = (1.0 if mode == "Y" else len(legs) - 1.0) - cost
        if edge0 is None:
            edge0 = edge
        if edge <= 1e-9:
            break
        step = min(g["r"] for g in legs)
        q += step
        gain += step * edge
        stop = False
        for g in legs:
            g["r"] -= step
        for g in list(legs):
            if g["r"] <= 1e-9:
                g["i"] += 1
                if g["i"] >= len(g["l"]):
                    if mode == "Y":
                        stop = True
                    else:
                        legs.remove(g)
                else:
                    g["r"] = g["l"][g["i"]][1]
        if stop:
            break
    return edge0, q, gain


def evaluate(e, books, ts):
    """Renvoie (ligne ou None, panier Oui complet ?, écart positif avant frais ?, gain net Oui, gain net Non)."""
    bk = [books.get(g["tk"]) for g in e["legs"]]
    if any(b is None for b in bk):
        return None, False, False, 0.0, 0.0
    fees = [g["fee"] for g in e["legs"]]
    asks = [b[0] for b in bk]
    bids = [b[1] for b in bk]
    complete = (not e["inc"]) and all(asks)
    sa = sum(a[0][0] for a in asks) if complete else None
    sb = sum(b[0][0] for b in bids if b)
    ey = (1 - sa) if sa is not None else None
    en = sb - 1
    if not ((ey is not None and ey > -0.005) or en > -0.005):
        return None, complete, False, 0.0, 0.0
    rec = {"t": ts, "e": e["id"], "n": len(bk), "sa": None if sa is None else round(sa, 4), "sb": round(sb, 4)}
    gy = gn = 0.0
    if ey is not None and ey > 0:
        e0, q, gy = basket(asks, fees, "Y")
        rec.update(nY=None if e0 is None else round(e0, 5), qY=round(q, 1), gY=round(gy, 3))
    if en > 0:
        e0, q, gn = basket([[(1 - p, s) for p, s in b] for b in bids], fees, "N")
        rec.update(nN=None if e0 is None else round(e0, 5), qN=round(q, 1), gN=round(gn, 3))
    pos = (ey is not None and ey > 0) or en > 0
    if pos:
        rec["legs"] = [[a[0][0] if a else None, round(a[0][1], 1) if a else 0, b[0][0] if b else None, round(b[0][1], 1) if b else 0]
                       for a, b in zip(asks, bids)]
    return rec, complete, pos, gy, gn


def sweep(evs, last, out):
    t0 = time.time()
    books = fetch_books(groups=[[g["tk"] for g in e["legs"]] for e in evs.values()])
    ts = int(time.time() * 1000)
    c = {"t": ts, "k": "cycle", "ev": len(evs), "tok": sum(len(e["legs"]) for e in evs.values()), "lus": len(books), "complets": 0,
         "candidats": 0, "disparus": 0, "y_brut": 0, "y_net": 0, "n_brut": 0, "n_net": 0, "gy": 0.0, "gn": 0.0}
    for e in evs.values():
        rec, complete, pos, gy, gn = evaluate(e, books, ts)
        c["complets"] += 1 if complete else 0
        if rec is None:
            last.pop(e["id"], None)
            continue
        if pos:
            # écart positif : on relit tout de suite les carnets de cet événement et on ne garde que ce qui tient
            c["candidats"] += 1
            first = (rec["sa"], rec["sb"])
            b2 = fetch_books(groups=[[g["tk"] for g in e["legs"]]])
            rec2, _, pos2, gy, gn = evaluate(e, b2, int(time.time() * 1000))
            if rec2 is None or not pos2:
                c["disparus"] += 1
                out.append({"t": ts, "e": e["id"], "c": 0, "sa": first[0], "sb": first[1]})
                last.pop(e["id"], None)
                continue
            rec = rec2
            rec["c"] = 1
            if rec.get("sa") is not None and rec["sa"] < 1:
                c["y_brut"] += 1
            if rec["sb"] > 1:
                c["n_brut"] += 1
            if gy > 0:
                c["y_net"] += 1
                c["gy"] += gy
            if gn > 0:
                c["n_net"] += 1
                c["gn"] += gn
        else:
            L = last.get(e["id"])
            if L and L[0] == (rec["sa"], rec["sb"]) and ts - L[1] < 300000:
                continue        # quasi-arbitrage inchangé : une ligne toutes les 5 min suffit
        last[e["id"]] = ((rec["sa"], rec["sb"]), ts)
        out.append(rec)
    c["dur"] = round(time.time() - t0, 1)
    c["gy"], c["gn"] = round(c["gy"], 2), round(c["gn"], 2)
    out.append(c)
    return c


# ---------------------------------------------------------------- récompenses (piste 1)
def rewards_snapshot(samples, known, meta_f):
    items, cur, pages = [], None, 0
    while pages < 40:
        url = f"{REWARDS}?order_by=rate_per_day&position=DESC&page_size=500" + ("&next_cursor=" + urllib.parse.quote(cur) if cur else "")
        j = http_json(url)
        if not j:
            break
        pages += 1
        data = j.get("data") or []
        if data and "recompense" not in samples:
            samples["recompense"] = data[0]
            samples["recompense_enveloppe"] = {k: v for k, v in j.items() if k != "data"}
        page_rate = 0.0
        for it in data:
            rc = it.get("rewards_config") or []
            rate = sum(num(x.get("rate_per_day")) for x in rc)
            page_rate = max(page_rate, rate)
            if rate <= 0:
                continue
            toks = it.get("tokens") or []
            if not toks or not toks[0].get("token_id"):
                continue
            items.append((it, rate, rc))
        cur = j.get("next_cursor")
        if not cur or cur == "LTE=" or not data or page_rate <= 0:
            break
    books = fetch_books([it["tokens"][0]["token_id"] for it, _, _ in items])
    ts = int(time.time() * 1000)
    rows = []
    for it, rate, rc in items:
        cid = it.get("condition_id")
        if cid not in known:
            known.add(cid)
            meta_f.write(json.dumps({"c": cid, "q": (it.get("question") or "")[:140], "sl": it.get("market_slug"), "ev": it.get("event_slug"),
                                     "eid": it.get("event_id"), "end": it.get("end_date"), "tk": it["tokens"][0]["token_id"],
                                     "o": it["tokens"][0].get("outcome")}, ensure_ascii=False) + "\n")
        bk = books.get(it["tokens"][0]["token_id"])
        raw_v = num(it.get("rewards_max_spread"))
        v = raw_v / 100 if raw_v >= 0.2 else raw_v      # publié en centimes ; gardé tel quel dans "v"
        row = {"t": ts, "c": cid, "r": round(rate, 4), "v": raw_v, "ms": num(it.get("rewards_min_size")),
               "comp": it.get("market_competitiveness"), "vol": it.get("volume_24hr")}
        if len(rc) > 1:
            row["rc"] = [[num(x.get("rate_per_day")), x.get("start_date"), x.get("end_date")] for x in rc]
        if bk and bk[0] and bk[1] and v > 0:
            asks, bids = bk
            mid = (asks[0][0] + bids[0][0]) / 2
            q1 = q2 = z1 = z2 = d1 = d2 = 0.0
            for p, s in bids:
                d = mid - p
                if d > v:
                    break
                q1 += ((v - d) / v) ** 2 * s
                z1 += s
                d1 += s * p
            for p, s in asks:
                d = p - mid
                if d > v:
                    break
                q2 += ((v - d) / v) ** 2 * s
                z2 += s
                d2 += s * (1 - p)
            row.update(mid=round(mid, 4), a=asks[0][0], b=bids[0][0], q1=round(q1, 1), q2=round(q2, 1), z1=round(z1, 1), z2=round(z2, 1),
                       d1=round(d1, 1), d2=round(d2, 1))
        rows.append(row)
    return rows, {"pages": pages, "marches_recompenses": len(items), "carnets": len(books),
                  "total_par_jour": round(sum(r for _, r, _ in items), 2)}


# ---------------------------------------------------------------- boucle
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="pistes")
    ap.add_argument("--max-seconds", type=float, default=1080)
    ap.add_argument("--cycle", type=float, default=60)
    ap.add_argument("--univers-min", type=float, default=45)
    ap.add_argument("--rec-min", type=float, default=15)
    ap.add_argument("--cache", default="/tmp/pistes_univers.json")
    a = ap.parse_args()
    t_start = time.time()
    left = lambda: a.max_seconds - (time.time() - t_start)
    os.makedirs(a.dir, exist_ok=True)
    if os.path.exists(os.path.join(a.dir, "ARRET")):
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    arb_path = os.path.join(a.dir, f"arb-{stamp}.jsonl")
    rec_path = os.path.join(a.dir, f"rec-{stamp}.jsonl")
    samples, info = {}, {}
    # univers : repris du cache s'il est récent (les tranches du même travail se suivent)
    evs, uni_t = {}, 0
    try:
        c = json.load(open(a.cache))
        if time.time() - c["t"] < a.univers_min * 60:
            evs, uni_t = c["evs"], c["t"]
    except Exception:
        pass
    skip_path = a.cache + ".skip"
    try:
        skip = set(json.load(open(skip_path)))
    except Exception:
        skip = set()

    def listing(seconds):
        out = list_universe(samples, time.time() + seconds, skip)
        try:
            json.dump(sorted(skip), open(skip_path, "w"))
        except Exception:
            pass
        return out
    if not evs:
        evs, info["univers"] = listing(min(420, max(60, left() - 300)))
        uni_t = time.time()
        if evs:
            try:
                json.dump({"t": uni_t, "evs": evs}, open(a.cache, "w"))
            except Exception:
                pass
    ev_meta = os.path.join(a.dir, "evenements.jsonl")
    known_ev = set()
    if os.path.exists(ev_meta):
        for line in open(ev_meta):
            try:
                known_ev.add(json.loads(line)["id"])
            except Exception:
                pass

    def note_events():
        with open(ev_meta, "a") as f:
            for e in evs.values():
                if e["id"] not in known_ev:
                    known_ev.add(e["id"])
                    f.write(json.dumps({"id": e["id"], "sl": e["sl"], "ti": e["ti"], "end": e["end"], "aug": e["aug"], "inc": e["inc"],
                                        "clos": e["clos"], "n": len(e["legs"]), "frais": sorted({g["fee"] for g in e["legs"]}),
                                        "cat": sorted({g["cat"] for g in e["legs"]}), "autre": any(g["o"] for g in e["legs"])},
                                       ensure_ascii=False) + "\n")
    note_events()
    rec_meta = os.path.join(a.dir, "recompenses_meta.jsonl")
    known_rec = set()
    if os.path.exists(rec_meta):
        for line in open(rec_meta):
            try:
                known_rec.add(json.loads(line)["c"])
            except Exception:
                pass
    # Un relevé des récompenses pèse 0,6 Mo compressé et la concurrence bouge lentement :
    # un toutes les 3 h au plus, l'heure du dernier étant gardée d'une tranche à l'autre.
    rec_every = max(a.rec_min, 180) * 60
    rec_stamp = a.cache + ".rec"
    try:
        last_rec = float(open(rec_stamp).read())
    except Exception:
        last_rec = 0.0
    last, n_cycles, n_lines, n_rec = {}, 0, 0, 0
    tot = {"candidats": 0, "disparus": 0, "y_brut": 0, "y_net": 0, "n_brut": 0, "n_net": 0}
    while left() > 45:
        c0 = time.time()
        try:
            if c0 - last_rec > rec_every and left() > 150:
                with open(rec_meta, "a") as mf:
                    rows, info["recompenses"] = rewards_snapshot(samples, known_rec, mf)
                with open(rec_path, "a") as f:
                    for r in rows:
                        f.write(json.dumps(r) + "\n")
                n_rec += len(rows)
                last_rec = time.time()
                try:
                    open(rec_stamp, "w").write(str(last_rec))
                except Exception:
                    pass
            if evs:
                out = []
                c = sweep(evs, last, out)
                with open(arb_path, "a") as f:
                    for r in out:
                        f.write(json.dumps(r) + "\n")
                n_lines += len(out)
                n_cycles += 1
                for k in tot:
                    tot[k] += c[k]
                info["dernier_cycle"] = c
            if time.time() - uni_t > (600 if not evs else a.univers_min * 60) and left() > 400:
                new, info["univers"] = listing(min(300, left() - 100))
                uni_t = time.time()
                if new and (not evs or not info["univers"]["partiel"]):
                    evs = new
                    note_events()
                    try:
                        json.dump({"t": uni_t, "evs": evs}, open(a.cache, "w"))
                    except Exception:
                        pass
        except Exception as e:
            STAT["derniere_erreur"] = "boucle " + (type(e).__name__ + " " + str(e))[:160]
        time.sleep(max(1.0, min(a.cycle - (time.time() - c0), left() - 40)))
    for p in (arb_path, rec_path):
        if os.path.exists(p):
            with open(p, "rb") as fi, gzip.open(p + ".gz", "wb") as fo:
                shutil.copyfileobj(fi, fo)
            os.remove(p)
    ex_path = os.path.join(a.dir, "exemples.json")
    if samples:
        old = {}
        try:
            old = json.load(open(ex_path))
        except Exception:
            pass
        if any(k not in old for k in samples):
            old.update(samples)
            json.dump(old, open(ex_path, "w"), ensure_ascii=False, indent=1)
    etat = {"debut": datetime.fromtimestamp(t_start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fin": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "cycles": n_cycles, "lignes_arb": n_lines,
            "lignes_rec": n_rec, "evenements": len(evs), "issues": sum(len(e["legs"]) for e in evs.values()),
            "cumul": tot, "info": info, "delai": round(GAP[0], 3), "http": STAT}
    with open(os.path.join(a.dir, "etat.jsonl"), "a") as f:
        f.write(json.dumps(etat, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
