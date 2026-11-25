#!/usr/bin/env python3
"""Lecture des relevés de pistes_live.py.

  python3 -I pistes_analyse.py <dossier pistes_polymarket> [mise en dollars, 1000 par défaut]
          [--piste=1|2] [--depuis=2026-11-24T08:24:00Z] [--jusque=2026-11-25T05:30:00Z]   (fenêtre de la piste 2, UTC)

Piste 1 : ce que paient les récompenses de liquidité par dollar posé (piste1), puis le solde
une fois déduit ce que les poseurs perdent sur les ordres servis (piste1_net, d'après pistes_markout.py).
  - capital déjà posé dans l'écart admis : d1 (côté acheteur) + d2 (côté vendeur, compté au prix du Non) ;
  - score de la concurrence D, borné faute de connaître les ordres de chaque intervenant :
      milieu entre 0,10 et 0,90 : (q1 + q2) / 6 <= D <= (q1 + q2) / 2   (règle publiée, c = 3)
      milieu hors de cette zone  : D <= min(q1, q2), ordres obligatoirement des deux côtés ;
  - un ordre de X dollars des deux côtés à mi-écart a un score de 0,25 X et reçoit taux x 0,25 X / (D + 0,25 X).
Piste 2 : paniers d'issues qui coûtent moins que ce qu'ils paient, frais compris, confirmés à la relecture.
"""
import bisect
import glob
import gzip
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone


def lines(pattern):
    for p in sorted(glob.glob(pattern)):
        f = gzip.open(p, "rt") if p.endswith(".gz") else open(p)
        with f:
            for line in f:
                try:
                    yield json.loads(line)
                except Exception:
                    pass


def hm(t):
    return datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%d/%m %H:%M")


def pct(x):
    return f"{100 * x:.3f} %"


def quant(xs, ws, q):
    z = sorted(zip(xs, ws))
    tot, acc = sum(ws), 0.0
    for x, w in z:
        acc += w
        if acc >= q * tot:
            return x
    return z[-1][0] if z else float("nan")


def piste1(d, X):
    snaps = defaultdict(list)
    for r in lines(os.path.join(d, "rec-*.jsonl*")):
        snaps[r["t"]].append(r)
    if not snaps:
        print("piste 1 : aucun relevé")
        return
    print(f"=== Piste 1, récompenses : {len(snaps)} relevés, du {hm(min(snaps))} au {hm(max(snaps))} UTC")
    print("relevé       marchés  récomp./jour   dont marchés avec des ordres dans l'écart admis   capital posé   récomp./capital/jour")
    for t in sorted(snaps):
        rows = snaps[t]
        two = [r for r in rows if r.get("d1", 0) + r.get("d2", 0) > 0]
        cap = sum(r["d1"] + r["d2"] for r in two)
        print(f"{hm(t)}   {len(rows):6d}  {sum(r['r'] for r in rows):11.0f}   {len(two):6d} marchés, {sum(r['r'] for r in two):9.0f} $/jour   {cap:12.0f}   {pct(sum(r['r'] for r in two) / cap)}")
    rows = snaps[max(snaps)]
    two = [r for r in rows if "mid" in r and r["d1"] + r["d2"] > 0]
    print(f"\ndernier relevé : {len(rows)} marchés récompensés, {len(two)} avec des ordres dans l'écart admis ; "
          f"récompenses des marchés où personne n'est dans l'écart : {sum(r['r'] for r in rows) - sum(r['r'] for r in two):.0f} $/jour")
    print("\npar zone de prix (milieu) : marchés, récomp./jour, capital posé, récomp./capital/jour")
    for name, f in (("0,10-0,90", lambda m: 0.10 <= m <= 0.90), ("< 0,10 ou > 0,90", lambda m: m < 0.10 or m > 0.90),
                    (">= 0,97 ou <= 0,03", lambda m: m >= 0.97 or m <= 0.03)):
        z = [r for r in two if f(r["mid"])]
        cap = sum(r["d1"] + r["d2"] for r in z)
        if z:
            print(f"  {name:20s} {len(z):6d} {sum(r['r'] for r in z):10.0f} {cap:13.0f}   {pct(sum(r['r'] for r in z) / cap)}")
    print("\npar taille de récompense : marchés, récomp./jour, capital posé, récomp./capital/jour")
    for lo, hi in ((0, 5), (5, 20), (20, 100), (100, 500), (500, 1e9)):
        z = [r for r in two if lo <= r["r"] < hi]
        cap = sum(r["d1"] + r["d2"] for r in z)
        if z:
            print(f"  {lo:>4.0f} à {hi if hi < 1e9 else float('inf'):>5.0f} $  {len(z):6d} {sum(r['r'] for r in z):10.0f} {cap:13.0f}   {pct(sum(r['r'] for r in z) / cap)}")
    # ordre fictif de X dollars des deux côtés à mi-écart
    out = []
    for r in two:
        s = 0.25 * X
        mid_zone = 0.10 <= r["mid"] <= 0.90
        d_hi = (r["q1"] + r["q2"]) / 2 if mid_zone else min(r["q1"], r["q2"])
        d_lo = (r["q1"] + r["q2"]) / 6 if mid_zone else None
        y_lo = r["r"] * s / (d_hi + s) / X                      # rendement prudent (concurrence au plus haut)
        y_hi = r["r"] * s / (d_lo + s) / X if d_lo is not None else None
        out.append((y_lo, y_hi, r, mid_zone))
    ys = [o[0] for o in out]
    ws = [1.0] * len(out)
    print(f"\nordre fictif de {X:.0f} $ des deux côtés à mi-écart, rendement brut par jour, hypothèse prudente :")
    print("  par marché : médiane", pct(quant(ys, ws, 0.5)), " 90e centile", pct(quant(ys, ws, 0.9)), " 99e centile", pct(quant(ys, ws, 0.99)))
    out.sort(key=lambda o: -o[0])
    meta = {}
    mp = os.path.join(d, "recompenses_meta.jsonl")
    if os.path.exists(mp):
        for m in lines(mp):
            meta[m["c"]] = m
    print("\n  20 premiers marchés (rendement prudent / optimiste par jour, récompense, capital posé, milieu, écart admis, question)")
    for y_lo, y_hi, r, mz in out[:20]:
        q = (meta.get(r["c"], {}).get("q") or "")[:70]
        print(f"   {pct(y_lo):>9s} / {pct(y_hi) if y_hi is not None else '   n.d. ':>9s}  {r['r']:7.0f} $  {r['d1'] + r['d2']:10.0f} $  {r['mid']:.3f}  {r['v']:.1f}c  {q}")
    for n in (10, 50):
        top = out[:n]
        print(f"  {n} premiers marchés, {X:.0f} $ chacun ({n * X:.0f} $) : {sum(o[0] for o in top) * X:.0f} $/jour prudent, soit {pct(sum(o[0] for o in top) / n)} par jour")
    # stabilité : rang des mêmes marchés entre le premier et le dernier relevé
    if len(snaps) > 1:
        first = {r["c"]: r for r in snaps[min(snaps)] if "mid" in r}
        ch = [(r["d1"] + r["d2"]) / (first[r["c"]]["d1"] + first[r["c"]]["d2"]) for _, _, r, _ in out[:50]
              if r["c"] in first and first[r["c"]]["d1"] + first[r["c"]]["d2"] > 0]
        if ch:
            ch.sort()
            print(f"  capital posé sur ces 50 marchés, dernier relevé / premier relevé : médiane x{ch[len(ch) // 2]:.2f}, min x{ch[0]:.2f}, max x{ch[-1]:.2f}")


def piste1_net(d, B=2000):
    """Récompenses + résultat des poseurs sur les ordres servis (pistes_markout.py), par jour."""
    import random
    days = []
    for p in sorted(glob.glob(os.path.join(d, "mko-*.json"))):
        j = json.load(open(p))
        ms = [m for m in j["marches"] if "erreur" not in m]
        err = Counter(m.get("erreur") for m in j["marches"] if "erreur" in m)
        print(f"\n=== Piste 1, solde des poseurs d'ordres : {os.path.basename(p)}, transactions des {j['fenetre_h']:.0f} h avant le {hm(j['t'] * 1000)} UTC")
        print(f"marchés traités {j['faits']} sur {j['prevus']} (erreurs : {dict(err) or 'aucune'}), tronqués {sum(1 for m in ms if m.get('tronque'))}, sans transaction {sum(1 for m in ms if not m['n'])}")
        st = j["strates"]
        by = defaultdict(list)
        for m in ms:
            f = 24.0 / m["couvert_h"] if m.get("couvert_h") else 24.0 / max(1.0, j["fenetre_h"] - 1)
            by[m["strate"]].append({k: m.get(k, 0.0) * f for k in ("vol", "mk0", "mk5", "mk60", "mkfin")} | {"r": m["r"], "cap": m["cap"], "mid": m.get("mid"), "n": m["n"]})
        w = {k: st[k]["marches"] / len(v) for k, v in by.items() if v}
        rew = sum(st[k]["recompenses_par_jour"] for k in st)

        def total(sample, key, f=lambda x: True):
            return sum(w[k] * sum(x[key] for x in v if f(x)) for k, v in sample.items())
        # récompenses et capital posé : valeurs exactes du relevé des récompenses utilisé pour le tirage
        snap = [r for r in lines(os.path.join(d, "rec-*.jsonl*")) if r["t"] == j["releve_recompenses"]]
        cap = sum(r.get("d1", 0) + r.get("d2", 0) for r in snap)
        rew_all = rew
        # récompenses retenues : celles des marchés où des ordres sont dans l'écart admis au moment du relevé
        # (ailleurs personne ne les touche à cet instant) ; le total est donné comme borne haute
        rew = sum(r["r"] for r in snap if r.get("d1", 0) + r.get("d2", 0) > 0)
        print(f"récompenses : {rew:.0f} $/jour sur les marchés où des ordres sont dans l'écart admis ({rew_all:.0f} $/jour au total) ; "
              f"capital posé dans l'écart admis (exact, relevé de {hm(j['releve_recompenses'])}) : {cap:.0f} $ ; "
              f"volume servi estimé : {total(by, 'vol'):.0f} $/jour ; transactions lues : {sum(m['n'] for m in ms)}")
        print("strate   marchés  tirés   récomp./jour   résultat sur ordres servis, $/jour : à l'exécution / +5 min / +60 min / fin de fenêtre")
        for k in sorted(by):
            v = by[k]
            print(f"  {k}     {st[k]['marches']:6d}  {len(v):5d}   {st[k]['recompenses_par_jour']:10.0f}    "
                  + " / ".join(f"{w[k] * sum(x[key] for x in v):10.0f}" for key in ("mk0", "mk5", "mk60", "mkfin")))
        rnd = random.Random(11)
        for key, label in (("mk5", "+5 min"), ("mk60", "+60 min"), ("mkfin", "fin de fenêtre")):
            est = total(by, key)
            bs = []
            for _ in range(B):
                bs.append(sum(w[k] * sum(v[rnd.randrange(len(v))][key] for _ in v) for k, v in by.items()))
            bs.sort()
            lo, hi = bs[int(B * .025)], bs[int(B * .975) - 1]
            print(f"horizon {label:14s}: résultat sur ordres servis {est:10.0f} $/jour (IC 95 % {lo:.0f} / {hi:.0f}) ; "
                  f"avec les récompenses {est + rew:10.0f} $/jour (IC {lo + rew:.0f} / {hi + rew:.0f}), soit {pct((est + rew) / cap)} du capital posé par jour (IC {pct((lo + rew) / cap)} / {pct((hi + rew) / cap)}) ; "
                  f"avec toutes les récompenses : {est + rew_all:.0f} $/jour, {pct((est + rew_all) / cap)}")
        days.append({"by": by, "w": w, "rew": rew, "cap": cap, "t": j["t"], "h": j["fenetre_h"]})
        print("par zone de prix : récompenses (exact) / capital posé (exact) / résultat sur ordres servis estimé à +5 min, +60 min, fin ($/jour) / solde à +60 min")
        for name, f in (("0,10-0,90", lambda m: m is not None and 0.10 <= m <= 0.90),
                        ("< 0,10 ou > 0,90", lambda m: m is not None and (m < 0.10 or m > 0.90)),
                        ("sans carnet des deux côtés", lambda m: m is None)):
            r_ = sum(r["r"] for r in snap if f(r.get("mid")) and (r.get("mid") is None or r.get("d1", 0) + r.get("d2", 0) > 0))
            c_ = sum(r.get("d1", 0) + r.get("d2", 0) for r in snap if f(r.get("mid")))
            g = lambda x: f(x["mid"])
            m5, m60, mf = total(by, "mk5", g), total(by, "mk60", g), total(by, "mkfin", g)
            bs = sorted(sum(w[k] * sum((lambda x: x["mk60"] if g(x) else 0.0)(v[rnd.randrange(len(v))]) for _ in v) for k, v in by.items()) for _ in range(B))
            lo, hi = bs[int(B * .025)], bs[int(B * .975) - 1]
            print(f"  {name:28s} {r_:9.0f} / {c_:11.0f} / {m5:9.0f} {m60:9.0f} {mf:9.0f}"
                  + (f" / {pct((r_ + m60) / c_)} par jour (IC {pct((r_ + lo) / c_)} / {pct((r_ + hi) / c_)})" if c_ > 0 else ""))
        top = sorted((x for v in by.values() for x in v), key=lambda x: x["mk60"])
        print("plus fortes pertes sur ordres servis (+60 min, $/jour, non pondéré) :", [round(x["mk60"]) for x in top[:5]],
              "; plus forts gains :", [round(x["mk60"]) for x in top[-5:]])
    if len(days) >= 2:
        # Moyenne des jours. Deux intervalles : jours indépendants (rééchantillonnage séparé de chaque jour) et jours
        # parfaitement liés (moyenne des bornes de chaque jour, le plus large possible). La réalité est entre les deux :
        # une partie des marchés est commune aux tirages, et les deux premières fenêtres se recouvrent.
        n = len(days)
        rew, cap = sum(x["rew"] for x in days) / n, sum(x["cap"] for x in days) / n
        print(f"\n=== Piste 1, moyenne de {n} jours (fenêtres : " + " ; ".join(f"{hm((x['t'] - x['h'] * 3600) * 1000)} -> {hm((x['t'] - 3600) * 1000)}" for x in days) + " UTC)")
        print(f"récompenses moyennes {rew:.0f} $/jour ; capital posé moyen {cap:.0f} $ ; soit {pct(rew / cap)} par jour avant pertes sur ordres servis")
        rnd = random.Random(12)
        for key, label in (("mk5", "+5 min"), ("mk60", "+60 min"), ("mkfin", "fin de fenêtre")):
            ests, los, his, boots = [], [], [], []
            for x in days:
                est = sum(x["w"][k] * sum(m[key] for m in v) for k, v in x["by"].items())
                bs = sorted(sum(x["w"][k] * sum(v[rnd.randrange(len(v))][key] for _ in v) for k, v in x["by"].items()) for _ in range(B))
                ests.append(est)
                los.append(bs[int(B * .025)])
                his.append(bs[int(B * .975) - 1])
                rnd.shuffle(bs)
                boots.append(bs)
            mean = sum(ests) / n
            ind = sorted(sum(b[i] for b in boots) / n for i in range(B))
            lo_i, hi_i = ind[int(B * .025)], ind[int(B * .975) - 1]
            lo_l, hi_l = sum(los) / n, sum(his) / n
            print(f"horizon {label:14s}: résultat sur ordres servis {mean:10.0f} $/jour ; avec les récompenses {mean + rew:10.0f} $/jour, soit {pct((mean + rew) / cap)} du capital posé par jour ; "
                  f"IC 95 % jours indépendants {pct((lo_i + rew) / cap)} / {pct((hi_i + rew) / cap)} ({lo_i + rew:.0f} / {hi_i + rew:.0f} $) ; "
                  f"jours liés {pct((lo_l + rew) / cap)} / {pct((hi_l + rew) / cap)} ({lo_l + rew:.0f} / {hi_l + rew:.0f} $)")


def _oui(recs, ev):
    """Paniers Oui nets positifs confirmés, séparés en gardés et écartés (issue déjà close, événement augmenté ou incomplet)."""
    y = [r for r in recs if r.get("c") == 1 and r.get("gY", 0) > 0]
    bad = [r for r in y if ev.get(r["e"], {}).get("clos") or ev.get(r["e"], {}).get("aug") or ev.get(r["e"], {}).get("inc")]
    ids = {id(r) for r in bad}
    return y, bad, [r for r in y if id(r) not in ids]


def _table_oui(good, ev, times, taux, limit=25):
    """Une ligne par événement : meilleur panier, fréquence, plus longue présence continue."""
    by = defaultdict(list)
    for r in good:
        by[r["e"]].append(r)
    rows = []
    for e, rs in by.items():
        m = ev.get(e, {})
        r = max(rs, key=lambda x: x["gY"])
        years = max((m.get("end", 0) - r["t"]) / 1000 / 86400 / 365.25, 1 / 365.25) if m.get("end") else None
        ann = (r["nY"] / r["sa"]) / years if years and r.get("nY") else None
        # balayages où le panier est vu : une ligne porte l'heure de lecture des carnets de son balayage, ou celle de la relecture qui suit
        seen = sorted({max(bisect.bisect_right(times, x["t"]) - 1, 0) for x in rs})
        run = best = 1
        for a, b in zip(seen, seen[1:]):
            run = run + 1 if b == a + 1 else 1
            best = max(best, run)
        gains = sorted(x["gY"] for x in rs)
        rows.append({"ann": ann, "e": e, "m": m, "r": r, "n": len(seen), "years": years, "suite": best, "med": gains[len(gains) // 2]})
    over = [x for x in rows if x["ann"] is not None and x["ann"] > taux]
    print(f"   événements distincts : {len(rows)} ; dont rendement annualisé jusqu'à la date de fin supérieur à {100 * taux:.2f} % : {len(over)}")
    print(f"   somme des meilleurs gains par événement (chaque panier pris une fois, au mieux) : {sum(x['r']['gY'] for x in rows):.2f} $ ; "
          f"dont au-dessus du taux sans risque : {sum(x['r']['gY'] for x in over):.2f} $")
    for x in sorted(rows, key=lambda x: -(x["ann"] or -1))[:limit]:
        r, m = x["r"], x["m"]
        if x["ann"] is None:
            print(f"   {m.get('ti', x['e'])[:54]} : date de fin inconnue | marge nette {100 * r['nY']:.2f} % | gain {r['gY']:.2f} $")
            continue
        print(f"   {m.get('ti', x['e'])[:54]:54s} issues {r['n']:2d} | coût {r['sa']:.3f} | marge nette {100 * r['nY']:.2f} % | taille {r['qY']:.0f} | "
              f"gain max {r['gY']:.2f} $ (médian {x['med']:.2f}) | fin dans {365.25 * x['years']:.0f} j | annualisé {100 * x['ann']:.1f} % | "
              f"vu {x['n']} fois sur {len(times)}, {x['suite']} de suite au plus")
    return rows


def piste2(d, taux=0.0434, t0=None, t1=None):
    """Paniers nets positifs confirmés. taux : rendement sans risque de comparaison (bon du Trésor américain à 3 mois).

    t0, t1 : fenêtre en millisecondes (UTC). Les lignes « u »: 1 et les résumés « cycle_large » viennent du tour
    d'horizon des événements hors univers principal ; ils sont comptés à part.
    """
    cyc, recs, wide_c, wide_r, unis = [], [], [], [], []
    for r in lines(os.path.join(d, "arb-*.jsonl*")):
        k = r.get("k")
        if k == "univers":
            unis.append(r)
        elif (t0 and r["t"] < t0) or (t1 and r["t"] > t1):
            continue
        elif k == "cycle":
            cyc.append(r)
        elif k == "cycle_large":
            wide_c.append(r)
        elif r.get("u"):
            wide_r.append(r)
        else:
            recs.append(r)
    if not cyc:
        print("\n=== Piste 2, arbitrage : aucun cycle relevé")
        return
    ev = {}
    mp = os.path.join(d, "evenements.jsonl")
    if os.path.exists(mp):
        for m in lines(mp):
            ev[m["id"]] = m
    cyc.sort(key=lambda c: c["t"])
    durs = sorted(c["dur"] for c in cyc)
    span = (cyc[-1]["t"] - cyc[0]["t"]) / 3600000
    print(f"\n=== Piste 2, arbitrage : {len(cyc)} balayages, du {hm(cyc[0]['t'])} au {hm(cyc[-1]['t'])} UTC ({span:.1f} h) ; "
          f"{cyc[-1]['ev']} événements, {cyc[-1]['tok']} issues au dernier ; durée médiane d'un balayage {durs[len(durs) // 2]} s")
    sizes = Counter(c["ev"] for c in cyc)
    print("taille de l'univers suivi (événements : balayages) : " + ", ".join(f"{k} : {v}" for k, v in sorted(sizes.items(), key=lambda x: -x[1])[:8]))
    if unis:
        U = set()
        for u in unis:
            U |= set(u["ids"])
        print(f"listes d'univers notées : {len(unis)} (de {hm(unis[0]['t'])} à {hm(unis[-1]['t'])}) ; événements distincts : {len(U)} ; "
              f"variantes : {dict(Counter(str(u.get('variante')) for u in unis))} ; partielles : {sum(1 for u in unis if u.get('partiel'))}")
    cand, disp = sum(c["candidats"] for c in cyc), sum(c["disparus"] for c in cyc)
    print(f"écarts positifs avant frais à la première lecture : {cand} ; disparus à la relecture immédiate : {disp}")
    y, bad, good = _oui(recs, ev)
    print(f"panier Oui net positif (frais compris, confirmé) : {len(y)} occurrences ; écartées {len(bad)} "
          f"(issue déjà close : {sum(1 for r in bad if ev.get(r['e'], {}).get('clos'))}, événement augmenté ou incomplet : {sum(1 for r in bad if not ev.get(r['e'], {}).get('clos'))}) ; gardées {len(good)}")
    times = [c["t"] for c in cyc]
    _table_oui(good, ev, times, taux)
    # par balayage : nombre de paniers gardés et gain total disponible à cet instant
    per = defaultdict(lambda: [0, 0.0])
    for r in good:
        i = max(bisect.bisect_right(times, r["t"]) - 1, 0)
        per[i][0] += 1
        per[i][1] += r["gY"]
    gs = sorted(per.get(i, [0, 0.0])[1] for i in range(len(cyc)))
    ns = sorted(per.get(i, [0, 0.0])[0] for i in range(len(cyc)))
    print(f"   par balayage : paniers Oui gardés, médiane {ns[len(ns) // 2]}, maximum {ns[-1]} ; gain total disponible, médiane {gs[len(gs) // 2]:.2f} $, maximum {gs[-1]:.2f} $")
    conf = [r for r in recs if r.get("c") == 1]
    n_ = [r for r in conf if r.get("sb", 0) > 1]
    npos = [r for r in n_ if r.get("gN", 0) > 0]
    print(f"panier Non : somme des acheteurs Oui au-dessus de 1 dans {len(n_)} occurrences ({len({r['e'] for r in n_})} événements) ; net positif après frais : {len(npos)} "
          f"({len({r['e'] for r in npos})} événements)")
    byn = defaultdict(list)
    for r in npos:
        byn[r["e"]].append(r)
    for e, rs in sorted(byn.items(), key=lambda x: -max(r["gN"] for r in x[1]))[:10]:
        r = max(rs, key=lambda x: x["gN"])
        print(f"   {ev.get(e, {}).get('ti', e)[:54]:54s} somme {r['sb']:.3f} | marge nette {100 * r['nN']:.2f} % | taille {r['qN']:.0f} | gain max {r['gN']:.2f} $ | {len(rs)} occurrences")
    print(f"   somme des meilleurs gains par événement, panier Non : {sum(max(r['gN'] for r in rs) for rs in byn.values()):.2f} $")
    if n_:
        best = max(n_, key=lambda x: x.get("nN") or -9)
        print(f"   meilleure marge nette d'un panier Non : {100 * best['nN']:.2f} % ({ev.get(best['e'], {}).get('ti', best['e'])[:50]})")
    # tour d'horizon des événements hors univers principal
    if wide_c:
        print(f"\n--- Tour d'horizon hors univers principal (jamais échangés ou moins de 1 000 $ de volume) : {len(wide_c)} passages")
        for c in wide_c:
            if not c.get("ev"):
                print(f"   {hm(c['t'])} : liste vide ({c.get('erreur', '')[:90]})")
                continue
            print(f"   {hm(c['t'])} : {c['ev']} événements balayés sur {c.get('ev_total')} listés ({c['tok']} issues sur {c.get('tok_total')}), liste "
                  f"{'partielle' if c.get('partiel') else 'complète'} ({c.get('evenements_lus')} événements lus) ; paniers Oui complets {c['complets']} ; "
                  f"écarts positifs {c['candidats']}, disparus {c['disparus']} ; nets positifs Oui {c['y_net']} ({c['gy']:.2f} $), Non {c['n_net']} ({c['gn']:.2f} $) ; {c['dur']} s")
        y, bad, good = _oui(wide_r, ev)
        print(f"   panier Oui net positif : {len(y)} occurrences ; écartées {len(bad)} ; gardées {len(good)}")
        if good:
            _table_oui(good, ev, [c["t"] for c in wide_c], taux, limit=15)
        wn = [r for r in wide_r if r.get("c") == 1 and r.get("gN", 0) > 0]
        byw = defaultdict(list)
        for r in wn:
            byw[r["e"]].append(r)
        print(f"   panier Non net positif : {len(wn)} occurrences, {len(byw)} événements ; somme des meilleurs gains : {sum(max(r['gN'] for r in rs) for rs in byw.values()):.2f} $")
        for e, rs in sorted(byw.items(), key=lambda x: -max(r["gN"] for r in x[1]))[:10]:
            r = max(rs, key=lambda x: x["gN"])
            print(f"      {ev.get(e, {}).get('ti', e)[:54]:54s} somme {r['sb']:.3f} | marge nette {100 * r['nN']:.2f} % | taille {r['qN']:.0f} | gain {r['gN']:.2f} $")


def ms(x):
    return int(datetime.strptime(x, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    opts = dict(x[2:].split("=", 1) for x in sys.argv[1:] if x.startswith("--") and "=" in x)
    d = args[0]
    X = float(args[1]) if len(args) > 1 else 1000.0
    last = None
    for line in lines(os.path.join(d, "etat.jsonl")):
        last = line
        u = (line.get("info") or {}).get("univers") or {}
        print(f"tranche {line['debut']} -> {line['fin']} : {line['cycles']} cycles, {line['lignes_rec']} lignes récompenses, "
              f"{line['evenements']} événements (liste : {u.get('variante', '?')}, partielle : {u.get('partiel')}), http {line['http']['ok']} ok / {line['http']['echecs']} échecs")
    fr = (((last or {}).get("info") or {}).get("univers") or {}).get("frais")
    if fr:
        print(f"frais des issues de l'univers principal, dernière liste : lus sur le marché {fr.get('marche', 0)}, sans frais {fr.get('sans', 0)}, "
              f"déduits de la catégorie {fr.get('categorie', 0)} ; taux du marché différent de celui de la catégorie : {fr.get('ecart', 0)} {fr.get('ecarts', '')} ; "
              f"exposant différent de 1 : {fr.get('exposant', 0)}")
    if "piste" not in opts or opts["piste"] == "1":
        piste1(d, X)
        piste1_net(d)
    if "piste" not in opts or opts["piste"] == "2":
        piste2(d, t0=ms(opts["depuis"]) if "depuis" in opts else None, t1=ms(opts["jusque"]) if "jusque" in opts else None)
