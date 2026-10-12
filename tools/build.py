#!/usr/bin/env python3
"""アドレス・ベース・レジストリの町字（大字・町／丁目／小字）の代表点から、逆ジオコーディング用の軽いデータを作る。

入力（tools/fetch.sh で取ってくる）
  raw/mt_town_all.csv            町字マスター（名前）
  raw/pos/mt_town_pos_prefXX.csv 町字マスター位置参照拡張（代表点の緯度経度）

出力
  data/towns.csv          代表点のある町字の一覧（UTF-8、パソコンで見る・確かめる用）
  data/rgeo_all.bin       全国分（ESP32 用の形式「RGEO」。形式は README）
  data/pref/rgeo_XX.bin   都道府県ごと（XX は都道府県コード 01〜47）
  data/stats.json         件数・大きさ・作った日
"""
import csv
import glob
import json
import os
import struct
import sys
import zlib
from collections import OrderedDict
from datetime import date

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
RAW = os.path.join(ROOT, 'raw')
OUT = os.path.join(ROOT, 'data')

CELL_E6 = 250000          # 区画の大きさ（0.25 度。緯度方向で約 28km）
HEADER_SIZE = 68
POINT_SIZE = 10
CITY_SIZE = 12
VERSION = 1


def load_points():
    """代表点のある町字を返す。[(行の辞書, 緯度×1e6, 経度×1e6)]"""
    town = {}
    with open(os.path.join(RAW, 'mt_town_all.csv'), encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            if r['ablt_date']:      # 廃止済み
                continue
            town[(r['lg_code'], r['machiaza_id'])] = r
    pts = []
    skipped = 0
    for fn in sorted(glob.glob(os.path.join(RAW, 'pos', 'mt_town_pos_pref*.csv'))):
        with open(fn, encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                if not r['rep_lat'] or not r['rep_lon']:
                    continue
                t = town.get((r['lg_code'], r['machiaza_id']))
                if t is None:       # 名前の表に無い（名前が分からないので使えない）
                    skipped += 1
                    continue
                pts.append((t, round(float(r['rep_lat']) * 1e6), round(float(r['rep_lon']) * 1e6)))
    # 並びを固定（市区町村コード → 町字 ID）
    pts.sort(key=lambda p: (p[0]['lg_code'], p[0]['machiaza_id']))
    return pts, skipped


def city_name(t):
    """郡・市・区をつないだ名前（例：札幌市中央区、西多摩郡奥多摩町）"""
    return t['county'] + t['city'] + t['ward']


def town_name(t):
    """大字・町と小字をつないだ名前。丁目は別に持つ"""
    return t['oaza_cho'] + t['koaza']


class Strings:
    """UTF-8 の文字列を NUL 終わりで並べ、同じ文字列は 1 回だけ持つ"""

    def __init__(self):
        self.blob = bytearray(b'\0')    # 0 番地は空文字列
        self.index = {'': 0}

    def add(self, s):
        if s not in self.index:
            self.index[s] = len(self.blob)
            self.blob += s.encode('utf-8') + b'\0'
        return self.index[s]


def build_bin(pts, chomes, data_date):
    """RGEO 形式のバイト列を作る"""
    st = Strings()
    # 丁目の表（全国で共通。0 番は丁目なし）
    chome_off = [st.add(c) for c in chomes]
    chome_idx = {c: i for i, c in enumerate(chomes)}

    cities = OrderedDict()
    for t, _, _ in pts:
        if t['lg_code'] not in cities:
            cities[t['lg_code']] = (st.add(t['pref']), st.add(city_name(t)))
    city_idx = {k: i for i, k in enumerate(cities)}
    if len(cities) > 0xFFFF:
        raise SystemExit('市区町村が多すぎる')

    # 区画の範囲（点を全部含む。端は区画の大きさにそろえる）
    lat0 = min(p[1] for p in pts) // CELL_E6 * CELL_E6
    lon0 = min(p[2] for p in pts) // CELL_E6 * CELL_E6
    rows = (max(p[1] for p in pts) - lat0) // CELL_E6 + 1
    cols = (max(p[2] for p in pts) - lon0) // CELL_E6 + 1

    cells = [[] for _ in range(rows * cols)]
    for p in pts:
        r = (p[1] - lat0) // CELL_E6
        c = (p[2] - lon0) // CELL_E6
        cells[r * cols + c].append(p)

    starts = []
    recs = bytearray()
    n = 0
    for ci, cell in enumerate(cells):
        starts.append(n)
        r, c = divmod(ci, cols)
        clat, clon = lat0 + r * CELL_E6, lon0 + c * CELL_E6
        for t, lat, lon in cell:
            dlat = min(65535, round((lat - clat) * 65535 / CELL_E6))
            dlon = min(65535, round((lon - clon) * 65535 / CELL_E6))
            name = st.add(town_name(t))
            recs += struct.pack('<HHH', dlat, dlon, city_idx[t['lg_code']])
            recs += name.to_bytes(3, 'little')
            recs += bytes([chome_idx[t['chome']]])
            n += 1
    starts.append(n)
    if len(st.blob) >= 1 << 24:
        raise SystemExit('文字列が 16MB を超えた')

    cell_bytes = struct.pack(f'<{len(starts)}I', *starts)
    city_bytes = b''.join(struct.pack('<III', int(k), po, co) for k, (po, co) in cities.items())
    chome_bytes = struct.pack(f'<{len(chome_off)}I', *chome_off)

    off_cells = HEADER_SIZE
    off_points = off_cells + len(cell_bytes)
    off_cities = off_points + len(recs)
    off_chome = off_cities + len(city_bytes)
    off_strings = off_chome + len(chome_bytes)
    body = cell_bytes + bytes(recs) + city_bytes + chome_bytes + bytes(st.blob)
    header = struct.pack('<4sHHIIiiIHHIIIIIIHHI',
                         b'RGEO', VERSION, HEADER_SIZE,
                         n, len(cities),
                         lat0, lon0, CELL_E6, rows, cols,
                         off_cells, off_points, off_cities, off_chome, off_strings, len(st.blob),
                         len(chomes), 0,
                         data_date)
    header += struct.pack('<I', zlib.crc32(body))
    assert len(header) == HEADER_SIZE, len(header)
    return header + body


def main():
    pts, skipped = load_points()
    if not pts:
        raise SystemExit('代表点が 0 件（tools/fetch.sh を先に）')
    # 元データの更新日（町字マスターの CSV の日付）
    mt = os.path.getmtime(os.path.join(RAW, 'mt_town_all.csv'))
    d = date.fromtimestamp(mt)
    data_date = d.year * 10000 + d.month * 100 + d.day

    # 丁目の表。並びは「無し」→ 数字の順（同じ数字なら名前順）
    num = {t['chome']: int(t['chome_number'] or 0) for t, _, _ in pts}
    chomes = sorted(num, key=lambda c: (c != '', num[c], c))
    if len(chomes) > 255:
        raise SystemExit('丁目の種類が多すぎる')

    os.makedirs(os.path.join(OUT, 'pref'), exist_ok=True)
    with open(os.path.join(OUT, 'towns.csv'), 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['lg_code', 'machiaza_id', 'machiaza_type', 'pref', 'city', 'oaza_cho', 'chome', 'koaza', 'lat', 'lon'])
        for t, lat, lon in pts:
            w.writerow([t['lg_code'], t['machiaza_id'], t['machiaza_type'], t['pref'], city_name(t),
                        t['oaza_cho'], t['chome'], t['koaza'], f'{lat / 1e6:.6f}', f'{lon / 1e6:.6f}'])

    stats = {'source': 'デジタル庁 アドレス・ベース・レジストリ（町字マスター・町字マスター位置参照拡張、CC BY 4.0）',
             'data_date': data_date, 'points': len(pts), 'skipped_no_name': skipped, 'files': {}}
    blob = build_bin(pts, chomes, data_date)
    open(os.path.join(OUT, 'rgeo_all.bin'), 'wb').write(blob)
    stats['files']['rgeo_all.bin'] = {'points': len(pts), 'bytes': len(blob)}
    for pc in range(1, 48):
        sub = [p for p in pts if int(p[0]['lg_code'][:2]) == pc]
        name = f'rgeo_{pc:02d}.bin'
        b = build_bin(sub, chomes, data_date)
        open(os.path.join(OUT, 'pref', name), 'wb').write(b)
        stats['files']['pref/' + name] = {'pref': sub[0][0]['pref'], 'points': len(sub), 'bytes': len(b)}
    json.dump(stats, open(os.path.join(OUT, 'stats.json'), 'w'), ensure_ascii=False, indent=1)
    print(f"点 {len(pts)}（名前が無く除いた {skipped}）、全国 {len(blob):,} バイト、丁目 {len(chomes)} 種")


if __name__ == '__main__':
    sys.exit(main())
