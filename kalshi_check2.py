# Controle du tirage (taux par mois de cloture, reponderation) et sensibilite a l ecart acheteur-vendeur.
# Usage : python3 -I kalshi_check.py <dossier kalshi> puis python3 -I kalshi_check2.py <dossier kalshi>
import json, sys, collections, datetime, math, random
D = sys.argv[1]
THR = 2340280
SPLIT = int(datetime.datetime(2026, 8, 20, tzinfo=datetime.timezone.utc).timestamp())
def fee(p): return math.ceil(0.07 * 100 * p * (1 - p) * 100 - 1e-9) / 100 / 100
def wilson(n, k, z=1.959964):
    p = k / n; d = 1 + z*z/n; c = p + z*z/(2*n); h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return ((c-h)/d, (c+h)/d)
done = set(l.strip() for l in open(D + '/done.txt') if l.strip())
pop = {}
for line in open(D + '/markets.jsonl'):
    try: r = json.loads(line)
    except Exception: continue
    pop[r[0]] = r
print('population retenue', len(pop), 'traites', len(done), 'traites dans population', len(done & set(pop)))
mon = lambda ts: datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime('%Y-%m')
N = collections.Counter(); n = collections.Counter(); NH = collections.Counter(); nH = collections.Counter()
src = collections.Counter()
for t, r in pop.items():
    m = mon(r[2]); hi = r[4] >= THR
    (NH if hi else N)[m] += 1
    if t in done: (nH if hi else n)[m] += 1
    src[(r[6] if len(r) > 6 else '?', 'disc' if r[2] >= SPLIT else 'val')] += 1
print('sources', dict(src))
print('mois   N_tirage  traites  taux |  N_haut  traites')
for m in sorted(set(N) | set(NH)):
    print(m, f'{N[m]:8d} {n[m]:8d} {100*n[m]/max(1,N[m]):6.2f}% | {NH[m]:6d} {nH[m]:6d}')
for per, f in (('disc', lambda r: r[2] >= SPLIT), ('val', lambda r: r[2] < SPLIT)):
    Np = sum(1 for r in pop.values() if f(r) and r[4] < THR); npd = sum(1 for t, r in pop.items() if f(r) and r[4] < THR and t in done)
    Nh = sum(1 for r in pop.values() if f(r) and r[4] >= THR); nh = sum(1 for t, r in pop.items() if f(r) and r[4] >= THR and t in done)
    print(per, 'tirage', Np, 'traites', npd, f'{100*npd/Np:.2f}%', '| haut volume', Nh, 'traites', nh)
rows = json.load(open('/tmp/kalshi_rows.json'))
# post-stratification par mois de cloture (strate tirage, b1)
for per in ('disc', 'val'):
    rs = [r for r in rows if r['per'] == per and r['band'] == 'b1' and r['st'] == 'tirage']
    by = collections.defaultdict(lambda: [0.0, 0.0, 0, 0])
    for r in rs:
        m = mon(r['close']); f_ = fee(r['ask'])
        by[m][0] += r['win'] - r['ask'] - f_; by[m][1] += r['ask'] + f_; by[m][2] += 1; by[m][3] += 1 - r['win']
    num = den = 0.0
    print(per, 'par mois de cloture (b1, tirage): mois n pertes rdt')
    for m in sorted(by):
        w = N[m] / max(1, n[m]); num += w * by[m][0]; den += w * by[m][1]
        print('  ', m, by[m][2], by[m][3], f'{100*by[m][0]/by[m][1]:+.2f}%', f'poids {w:.1f}')
    print(per, f'rdt brut {100*sum(v[0] for v in by.values())/sum(v[1] for v in by.values()):+.3f}%  rdt repondere par mois {100*num/den:+.3f}%')
# sensibilite : ecart acheteur-vendeur
def stats(rs, B=2000, seed=11):
    nn = len(rs); L = sum(1 for r in rs if not r['win']); pnl = cost = 0.0; cl = {}
    for r in rs:
        f_ = fee(r['ask']); pl = r['win'] - r['ask'] - f_; c = r['ask'] + f_; pnl += pl; cost += c
        a = cl.setdefault(r['ev'], [0.0, 0.0]); a[0] += pl; a[1] += c
    arr = list(cl.values()); rnd = random.Random(seed); bs = []
    for _ in range(B):
        sp = sc = 0.0
        for _ in range(len(arr)):
            a = arr[rnd.randrange(len(arr))]; sp += a[0]; sc += a[1]
        bs.append(sp / sc)
    bs.sort(); lo, hi = wilson(nn, L)
    return f"n={nn:5d} pertes={L:3d} tx={100*L/nn:5.2f}% IC[{100*lo:5.2f};{100*hi:5.2f}] seuil={100-100*cost/nn:5.3f}% rdt={100*pnl/cost:+6.2f}% IC[{100*bs[int(B*.025)]:+6.2f};{100*bs[int(B*.975)-1]:+6.2f}]"
print('--- sensibilite a l ecart (b1, tirage) : acheteur du meme cote >= seuil')
for thr in (0.95, 0.97, 0.98, 0.985):
    for per in ('disc', 'val'):
        rs = [r for r in rows if r['per'] == per and r['band'] == 'b1' and r['st'] == 'tirage' and r['bid'] >= thr]
        print(f'bid>={thr} {per}', stats(rs) if rs else 'n=0')
print('--- <=1h et ecart serre (b1, tirage)')
for thr in (0.95, 0.98):
    for per in ('disc', 'val'):
        rs = [r for r in rows if r['per'] == per and r['band'] == 'b1' and r['st'] == 'tirage' and r['bid'] >= thr and r['h'] <= 1]
        print(f'<=1h bid>={thr} {per}', stats(rs) if rs else 'n=0')
print('--- cote (b1, tirage)')
for side in ('yes', 'no'):
    for per in ('disc', 'val'):
        rs = [r for r in rows if r['per'] == per and r['band'] == 'b1' and r['st'] == 'tirage' and r['side'] == side]
        print(side, per, stats(rs) if rs else 'n=0')
