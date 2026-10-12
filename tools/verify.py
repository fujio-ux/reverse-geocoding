#!/usr/bin/env python3
"""c/rgeo.h（RGEO 形式の検索）の答えが、data/towns.csv の全点を総当たりした答えと一致するか確かめる。

  python3 tools/verify.py [件数]     （c/rgeo_cli を先に作る：cc -O2 -o c/rgeo_cli c/rgeo_cli.c -lm）

試す点：実在の代表点の近く（±0.05 度）を中心に、海の上・範囲の外も少し混ぜる。
距離が 1m 以内で同じなら一致とみなす（区画の中の位置を 16 ビットに丸めているため、最大 0.3m ほどずれる）。
"""
import csv
import math
import os
import random
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')


def dist(lat1, lon1, lat2, lon2):
    D = math.pi / 180
    x = (lon2 - lon1) * D * math.cos((lat1 + lat2) * 0.5 * D)
    y = (lat2 - lat1) * D
    return 6371000.0 * math.hypot(x, y)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    rows = list(csv.DictReader(open(os.path.join(ROOT, 'data', 'towns.csv'), encoding='utf-8')))
    pts = [(float(r['lat']), float(r['lon']), r) for r in rows]
    rnd = random.Random(20261012)
    qs = []
    for i in range(n):
        if i % 10 == 9:     # 範囲のあちこち（海の上を含む）
            qs.append((rnd.uniform(20, 46), rnd.uniform(122, 154)))
        else:
            la, lo, _ = rnd.choice(pts)
            qs.append((la + rnd.uniform(-0.05, 0.05), lo + rnd.uniform(-0.05, 0.05)))
    out = subprocess.run([os.path.join(ROOT, 'c', 'rgeo_cli'), os.path.join(ROOT, 'data', 'rgeo_all.bin'), '-'],
                         input=''.join(f'{a:.6f} {b:.6f}\n' for a, b in qs), capture_output=True, text=True, check=True).stdout.splitlines()
    assert len(out) == len(qs), (len(out), len(qs))
    bad = 0
    for (qa, qo), line in zip(qs, out):
        best = min(pts, key=lambda p: dist(qa, qo, p[0], p[1]))
        bd = dist(qa, qo, best[0], best[1])
        code, name, d = line.split(',')
        want = f"{best[2]['oaza_cho']}{best[2]['koaza']}{best[2]['chome']}"
        if abs(float(d) - bd) > 1.0:
            bad += 1
            print(f'違う：{qa:.6f},{qo:.6f} 検索={code} {name} {d}m 総当たり={best[2]["lg_code"]} {want} {bd:.1f}m')
    print(f'{len(qs)} 件中 {len(qs) - bad} 件一致')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
