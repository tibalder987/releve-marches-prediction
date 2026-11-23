# Recalcul independant des resultats Kalshi a partir de kalshi/rows.csv.
# Usage : python3 -I kalshi_check.py <dossier kalshi>   (ecrit /tmp/kalshi_rows.json, lu par kalshi_check2.py)
import csv, json, math, random, sys, collections, datetime
D = sys.argv[1]
SPLIT = int(datetime.datetime(2026, 8, 20, tzinfo=datetime.timezone.utc).timestamp())
T0 = int(datetime.datetime(2026, 5, 20, tzinfo=datetime.timezone.utc).timestamp())
T1 = int(datetime.datetime(2026, 11, 19, tzinfo=datetime.timezone.utc).timestamp())
THR = 2340280
def fee(p): return math.ceil(0.07 * 100 * p * (1 - p) * 100 - 1e-9) / 100 / 100
def wilson(n, k, z=1.959964):
    if n == 0: return (0, 1)
    p = k / n; d = 1 + z*z/n; c = p + z*z/(2*n); h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return ((c-h)/d, (c+h)/d)
rows = []
seen = collections.Counter()
for r in csv.reader(open(D + '/rows.csv')):
    if len(r) < 12 or r[0] == 'ticker': continue
    x = dict(ticker=r[0], ev=r[1], series=r[2], side=r[3], band=r[4], ask=float(r[5]), bid=float(r[6]), t=int(r[7]), close=int(r[8]), h=float(r[9]), win=int(r[10]), vol=int(r[11]))
    seen[(x['ticker'], x['side'], x['band'])] += 1
    rows.append(x)
print('lignes', len(rows), 'doublons (ticker,cote,bande)', sum(1 for v in seen.values() if v > 1), 'marches avec achat', len({r['ticker'] for r in rows}))
print('close hors periode', sum(1 for r in rows if not (T0 <= r['close'] < T1)), 'bid<0.95', sum(1 for r in rows if r['bid'] < 0.95), 'h<0', sum(1 for r in rows if r['h'] < 0))
def window(h): return '>72h' if h > 72 else '24-72h' if h > 24 else '6-24h' if h > 6 else '1-6h' if h > 1 else '<=1h'
def stats(rs, B=2000, seed=7):
    n = len(rs); L = sum(1 for r in rs if not r['win'])
    pnl = cost = 0.0; cl = {}; days = set()
    for r in rs:
        f = fee(r['ask']); pl = r['win'] - r['ask'] - f; c = r['ask'] + f
        pnl += pl; cost += c
        a = cl.setdefault(r['ev'], [0.0, 0.0]); a[0] += pl; a[1] += c
        days.add(r['t'] // 86400)
    arr = list(cl.values()); rnd = random.Random(seed); bs = []
    for _ in range(B):
        sp = sc = 0.0
        for _ in range(len(arr)):
            a = arr[rnd.randrange(len(arr))]; sp += a[0]; sc += a[1]
        bs.append(sp / sc)
    bs.sort(); lo, hi = wilson(n, L)
    return dict(n=n, L=L, lo=100*lo, hi=100*hi, seuil=100*cost/n, rdt=100*pnl/cost, rlo=100*bs[int(B*0.025)], rhi=100*bs[int(B*0.975)-1], jours=len(days), ev=len(arr))
def show(label, rs):
    if not rs: print(f'{label:46s} n=0'); return None
    s = stats(rs)
    ok = (s['hi'] < 100 - s['seuil']) and s['rlo'] > 0 and s['jours'] >= 30
    print(f"{label:46s} n={s['n']:5d} pertes={s['L']:4d} tx={100*s['L']/s['n']:5.2f}% IC[{s['lo']:5.2f};{s['hi']:5.2f}] seuil_perte={100-s['seuil']:5.3f}% rdt={s['rdt']:+6.2f}% IC[{s['rlo']:+6.2f};{s['rhi']:+6.2f}] j={s['jours']:3d} ev={s['ev']:5d} {'OK' if ok else ''}")
    return s
cats = {}
try:
    sj = json.load(open(D + '/series.json'))
    cats = sj if isinstance(sj, dict) else {}
except Exception as e:
    print('series.json', e)
for r in rows:
    r['per'] = 'disc' if r['close'] >= SPLIT else 'val'
    r['cat'] = cats.get(r['series'], '?')
    r['w'] = window(r['h'])
    r['st'] = 'haut' if r['vol'] >= THR else 'tirage'
for per in ('disc', 'val'):
    print('=====', per)
    for band in ('b0', 'b1', 'b2'):
        show(f'{per} tirage {band}', [r for r in rows if r['per'] == per and r['band'] == band and r['st'] == 'tirage'])
        show(f'{per} haut-volume {band}', [r for r in rows if r['per'] == per and r['band'] == band and r['st'] == 'haut'])
    print('-- fenetres, b1, tirage')
    for w in ('<=1h', '1-6h', '6-24h', '24-72h', '>72h'):
        show(f'{per} tirage b1 {w}', [r for r in rows if r['per'] == per and r['band'] == 'b1' and r['st'] == 'tirage' and r['w'] == w])
    print('-- categories, b1, tirage')
    cc = collections.Counter(r['cat'] for r in rows if r['per'] == per and r['band'] == 'b1' and r['st'] == 'tirage')
    for c, n in cc.most_common():
        show(f'{per} tirage b1 {c}', [r for r in rows if r['per'] == per and r['band'] == 'b1' and r['st'] == 'tirage' and r['cat'] == c])
json.dump(rows, open('/tmp/kalshi_rows.json', 'w'))
