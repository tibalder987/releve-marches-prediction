#!/usr/bin/env python3
"""Lecture des relevés de pistes_live.py.

  python3 -I pistes_analyse.py <dossier pistes_polymarket> [mise en dollars, 1000 par défaut]

Piste 1 : ce que paient les récompenses de liquidité par dollar posé (piste1), puis le solde
une fois déduit ce que les poseurs perdent sur les ordres servis (piste1_net, d'après pistes_markout.py).
  - capital déjà posé dans l'écart admis : d1 (côté acheteur) + d2 (côté vendeur, compté au prix du Non) ;
  - score de la concurrence D, borné faute de connaître les ordres de chaque intervenant :
      milieu entre 0,10 et 0,90 : (q1 + q2) / 6 <= D <= (q1 + q2) / 2   (règle publiée, c = 3)
      milieu hors de cette zone  : D <= min(q1, q2), ordres obligatoirement des deux côtés ;
  - un ordre de X dollars des deux côtés à mi-écart a un score de 0,25 X et reçoit taux x 0,25 X / (D + 0,25 X).
Piste 2 : paniers d'issues qui coûtent moins que ce qu'ils paient, frais compris, confirmés à la relecture.
"""
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


def piste2(d, taux=0.0434):
    """Paniers nets positifs confirmés. taux : rendement sans risque de comparaison (bon du Trésor américain à 3 mois)."""
    cyc, recs = [], []
    for r in lines(os.path.join(d, "arb-*.jsonl*")):
        (cyc if r.get("k") == "cycle" else recs).append(r)
    if not cyc:
        print("\n=== Piste 2, arbitrage : aucun cycle relevé")
        return
    ev = {}
    mp = os.path.join(d, "evenements.jsonl")
    if os.path.exists(mp):
        for m in lines(mp):
            ev[m["id"]] = m
    durs = sorted(c["dur"] for c in cyc)
    print(f"\n=== Piste 2, arbitrage : {len(cyc)} balayages, du {hm(cyc[0]['t'])} au {hm(cyc[-1]['t'])} UTC ; "
          f"{cyc[-1]['ev']} événements, {cyc[-1]['tok']} issues ; durée médiane d'un balayage {durs[len(durs) // 2]} s")
    cand, disp = sum(c["candidats"] for c in cyc), sum(c["disparus"] for c in cyc)
    print(f"écarts positifs avant frais à la première lecture : {cand} ; disparus à la relecture immédiate : {disp}")
    conf = [r for r in recs if r.get("c") == 1]
    # panier Oui : seulement les événements dont toutes les issues sont négociables, sans issue déjà close,
    # sans issues ajoutables plus tard (« augmenté »)
    y = [r for r in conf if r.get("gY", 0) > 0]
    bad = [r for r in y if ev.get(r["e"], {}).get("clos") or ev.get(r["e"], {}).get("aug") or ev.get(r["e"], {}).get("inc")]
    good = [r for r in y if r not in bad]
    print(f"panier Oui net positif (frais compris, confirmé) : {len(y)} occurrences ; écartées {len(bad)} "
          f"(issue déjà close : {sum(1 for r in bad if ev.get(r['e'], {}).get('clos'))}, événement augmenté ou incomplet : {sum(1 for r in bad if not ev.get(r['e'], {}).get('clos'))}) ; gardées {len(good)}")
    by = defaultdict(list)
    for r in good:
        by[r["e"]].append(r)
    rows = []
    for e, rs in by.items():
        m = ev.get(e, {})
        r = max(rs, key=lambda x: x["gY"])
        years = max((m.get("end", 0) - r["t"]) / 1000 / 86400 / 365.25, 1 / 365.25) if m.get("end") else None
        cost = r["sa"]
        ann = (r["nY"] / cost) / years if years and r.get("nY") else None
        rows.append((ann, e, m, r, len(rs), years))
    over = [x for x in rows if x[0] is not None and x[0] > taux]
    print(f"   événements distincts : {len(rows)} ; dont rendement annualisé jusqu'à la date de fin supérieur à {100 * taux:.2f} % : {len(over)}")
    for ann, e, m, r, n, years in sorted(rows, key=lambda x: -(x[0] or -1)):
        print(f"   {m.get('ti', e)[:58]:58s} issues {r['n']:2d} | coût {r['sa']:.3f} | marge nette {100 * r['nY']:.2f} % | taille {r['qY']:.0f} paniers | gain {r['gY']:.2f} $ | "
              f"fin dans {365.25 * years:.0f} j | annualisé {100 * ann:.1f} % | vu {n} fois sur {len(cyc)}" if ann is not None else f"   {m.get('ti', e)[:58]} : date de fin inconnue")
    n_ = [r for r in conf if r.get("sb", 0) > 1]
    npos = [r for r in n_ if r.get("gN", 0) > 0]
    print(f"panier Non : somme des acheteurs Oui au-dessus de 1 dans {len(n_)} occurrences ({len({r['e'] for r in n_})} événements) ; net positif après frais : {len(npos)}")
    for r in sorted(npos, key=lambda x: -x["gN"])[:10]:
        m = ev.get(r["e"], {})
        print(f"   {m.get('ti', r['e'])[:58]:58s} somme {r['sb']:.3f} | marge nette {100 * r['nN']:.2f} % | taille {r['qN']:.0f} | gain {r['gN']:.2f} $")
    if n_:
        best = max(n_, key=lambda x: x.get("nN") or -9)
        print(f"   meilleure marge nette d'un panier Non : {100 * best['nN']:.2f} % ({ev.get(best['e'], {}).get('ti', best['e'])[:50]})")


if __name__ == "__main__":
    d = sys.argv[1]
    X = float(sys.argv[2]) if len(sys.argv) > 2 else 1000.0
    for line in lines(os.path.join(d, "etat.jsonl")):
        u = (line.get("info") or {}).get("univers") or {}
        print(f"tranche {line['debut']} -> {line['fin']} : {line['cycles']} cycles, {line['lignes_rec']} lignes récompenses, "
              f"{line['evenements']} événements (liste : {u.get('variante', '?')}, partielle : {u.get('partiel')}), http {line['http']['ok']} ok / {line['http']['echecs']} échecs")
    piste1(d, X)
    piste1_net(d)
    piste2(d)
