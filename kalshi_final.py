# Passage complet Kalshi : chiffres toutes strates de volume confondues, cellules, controle contre kalshi/summary.json,
# et decompte des marches sans chandelier a partir de l historique de kalshi/status.json.
# Usage : python3 -I kalshi_check.py <dossier kalshi>          (ecrit /tmp/kalshi_rows.json)
#         python3 -I kalshi_final.py <dossier kalshi> [clone complet du depot, pour le decompte des reponses 404]
#         clone complet sans les fichiers : git clone --filter=blob:none --no-checkout <depot> <dossier>
import json, math, random, collections, sys
D = sys.argv[1]
rows = json.load(open('/tmp/kalshi_rows.json'))
def fee(p): return math.ceil(0.07 * 100 * p * (1 - p) * 100 - 1e-9) / 100 / 100
def wilson(n, k, z=1.959964):
    if n == 0: return (0, 1)
    p = k / n; d = 1 + z*z/n; c = p + z*z/(2*n); h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return ((c-h)/d, (c+h)/d)
def stats(rs, B=2000, seed=7, nofee=False):
    n = len(rs); L = sum(1 for r in rs if not r['win']); pnl = cost = 0.0; cl = {}; days = set()
    for r in rs:
        f = 0.0 if nofee else fee(r['ask']); pl = r['win'] - r['ask'] - f; c = r['ask'] + f
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
    return dict(n=n, L=L, tx=100*L/n, lo=100*lo, hi=100*hi, be=100-100*cost/n, rdt=100*pnl/cost, rlo=100*bs[int(B*0.025)], rhi=100*bs[int(B*0.975)-1], jours=len(days), ev=len(arr))
def ok(s): return s['hi'] < s['be'] and s['rlo'] > 0 and s['jours'] >= 30
def line(label, rs, **kw):
    if not rs: print(f'{label:44s} n=0'); return None
    s = stats(rs, **kw)
    print(f"{label:44s} n={s['n']:6d} pertes={s['L']:5d} tx={s['tx']:5.2f}% IC[{s['lo']:5.2f};{s['hi']:5.2f}] équilibre={s['be']:5.2f}% rdt={s['rdt']:+6.2f}% IC[{s['rlo']:+6.2f};{s['rhi']:+6.2f}] j={s['jours']:3d} ev={s['ev']:5d} {'OK' if ok(s) else ''}")
    return s
B = {'b0': '0,98-0,989', 'b1': '0,99-0,995', 'b2': '0,996-0,999'}
print('lignes', len(rows), '| marchés avec achat', len({r['ticker'] for r in rows}), '| événements', len({r['ev'] for r in rows}))
print('=== toutes strates de volume, par tranche')
for band in ('b1', 'b0', 'b2'):
    for per in ('disc', 'val'):
        line(f'{per} {B[band]}', [r for r in rows if r['per'] == per and r['band'] == band])
print('=== b1 sans aucun frais')
for per in ('disc', 'val'):
    line(f'{per} b1 sans frais', [r for r in rows if r['per'] == per and r['band'] == 'b1'], nofee=True)
print('=== b1 par strate de volume')
for st in ('tirage', 'haut'):
    for per in ('disc', 'val'):
        line(f'{per} b1 volume {"< 2,34 M" if st == "tirage" else ">= 2,34 M"}', [r for r in rows if r['per'] == per and r['band'] == 'b1' and r['st'] == st])
print('=== b1 par délai avant clôture (toutes strates)')
for w in ('<=1h', '1-6h', '6-24h', '24-72h', '>72h'):
    for per in ('disc', 'val'):
        line(f'{per} b1 {w}', [r for r in rows if r['per'] == per and r['band'] == 'b1' and r['w'] == w])
print('=== b1 par catégorie (toutes strates)')
cc = collections.Counter(r['cat'] for r in rows if r['band'] == 'b1')
for c, n in cc.most_common():
    for per in ('disc', 'val'):
        line(f'{per} b1 {c}', [r for r in rows if r['per'] == per and r['band'] == 'b1' and r['cat'] == c])
print('=== sensibilité hors règle : acheteur >= 0,98 (b1, toutes strates) ; puis <=1h')
for per in ('disc', 'val'):
    line(f'{per} b1 acheteur>=0,98', [r for r in rows if r['per'] == per and r['band'] == 'b1' and r['bid'] >= 0.98])
for per in ('disc', 'val'):
    line(f'{per} b1 acheteur>=0,985', [r for r in rows if r['per'] == per and r['band'] == 'b1' and r['bid'] >= 0.985])
for per in ('disc', 'val'):
    line(f'{per} b1 <=1h acheteur>=0,98', [r for r in rows if r['per'] == per and r['band'] == 'b1' and r['bid'] >= 0.98 and r['h'] <= 1])
# cellules : période x catégorie x délai x tranche, comme summary.json
print('=== cellules (période | catégorie | délai | tranche)')
cells = collections.defaultdict(list)
for r in rows:
    cells[(r['per'], r['cat'], r['w'], r['band'])].append(r)
summ = json.load(open(D + '/summary.json'))['cellules']
wmap = {'<=1h': '<=1h', '1-6h': '1-6h', '6-24h': '6-24h', '24-72h': '24-72h', '>72h': '>72h'}
print('cellules recalculées', len(cells), '| cellules de summary.json', len(summ))
keys_s = set(summ)
mism = 0; miss = 0
for k, rs in cells.items():
    ks = '|'.join(k)
    if ks not in summ:
        miss += 1; continue
    n = len(rs); L = sum(1 for r in rs if not r['win'])
    pnl = sum(r['win'] - r['ask'] - fee(r['ask']) for r in rs); cost = sum(r['ask'] + fee(r['ask']) for r in rs)
    s = summ[ks]
    if s['n'] != n or s['pertes'] != L or abs(s['rdt'] - 100 * pnl / cost) > 0.002: mism += 1
print('cellules absentes de summary.json :', miss, '| exemples de clés summary :', list(summ)[:3], '| écarts (n, pertes ou rendement) :', mism)
res = {}
for k, rs in cells.items():
    if len(rs) >= 30: res[k] = stats(rs, B=1000)
for per in ('disc', 'val'):
    z = {k: s for k, s in res.items() if k[0] == per}
    print(per, ': cellules d\'au moins 30 achats', len(z), '| passent le critère', sum(1 for s in z.values() if ok(s)), '| rendement positif', sum(1 for s in z.values() if s['rdt'] > 0),
          '| sans aucune perte', sum(1 for s in z.values() if s['L'] == 0), '| max achats d\'une cellule sans perte', max([s['n'] for s in z.values() if s['L'] == 0] or [0]))
    pos = sorted(((s['rdt'], k, s) for k, s in z.items() if s['rdt'] > 0), reverse=True)
    for rdt, k, s in pos[:12]:
        print(f"   {'|'.join(k[1:]):48s} n={s['n']:4d} pertes={s['L']:2d} IC perte[{s['lo']:.2f};{s['hi']:.2f}] équilibre={s['be']:.2f}% rdt={s['rdt']:+.2f}% IC[{s['rlo']:+.2f};{s['rhi']:+.2f}] j={s['jours']}")
# cellules positives en découverte : que donnent-elles en validation ?
print('=== cellules à rendement positif en découverte (>= 30 achats) : même cellule en validation')
for k, s in sorted(res.items()):
    if k[0] == 'disc' and s['rdt'] > 0:
        v = res.get(('val',) + k[1:]); vv = cells.get(('val',) + k[1:], [])
        print(f"   {'|'.join(k[1:]):48s} disc n={s['n']:4d} L={s['L']:2d} rdt={s['rdt']:+.2f}% [{s['rlo']:+.2f};{s['rhi']:+.2f}] | val " + (f"n={v['n']:4d} L={v['L']:2d} rdt={v['rdt']:+.2f}% [{v['rlo']:+.2f};{v['rhi']:+.2f}]" if v else f"n={len(vv)} (moins de 30)"))


def sans_chandelier(repo):
    """Somme des reponses 404 (marche sans chandelier) dans les versions successives de kalshi/status.json."""
    import subprocess
    git = lambda *a: subprocess.run(['git', '-C', repo] + list(a), capture_output=True, text=True).stdout
    seen, states = set(), []
    for sha in git('log', '--format=%H', '--', 'kalshi/status.json').split():
        try:
            j = json.loads(git('show', sha + ':kalshi/status.json'))
        except Exception:
            continue
        if j.get('maj') in seen or j.get('traites') is None:
            continue
        seen.add(j.get('maj'))
        states.append(j)
    states.sort(key=lambda j: j['maj'])
    prev = n404 = 0
    incs = []
    for j in states:
        incs.append(j['traites'] - prev)
        prev = j['traites']
        n404 += (j.get('http') or {}).get('404', 0)
    print('=== marches sans chandelier (reponses 404)')
    print('etats lus', len(states), '| marches traites', prev, '| plus grand increment', max(incs), '(un increment double signalerait un etat manquant)')
    print('reponses 404 cumulees', n404, f'soit {100 * n404 / prev:.2f} % des marches traites')


if len(sys.argv) > 2:
    sans_chandelier(sys.argv[2])
