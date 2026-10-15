#!/usr/bin/env python3
"""今の町字の代表点が、1970 年（昭和 45 年 4 月 1 日）にはどの市町村だったかの対応表を作る。

元データ：「地図地理Sandbox」市区町村境界時系列データ（製作 はにしな、目的を問わず利用・改変・再配布可）
  https://hanishina.net/maps/historydata.html  の領域データ polygon.fgb（START〜END の期間ごとの市区町村の形）
  → raw/history/polygon.fgb に置く（tools/fetch.sh では取らない。下の curl で）
     curl -sfL -o raw/history/polygon.fgb https://hanishina.net/maps/tiles/polygon.fgb

やり方：1970-04-01 に有効な市区町村の形（湖・帰属未定地は除く）に、町字の代表点が入るかを調べる。
  どの形にも入らない点（海岸ぎりぎりの点など）は、3km 以内で一番近い形にする。
  1970-04-01 にしたのは、北海道のデータがこの日から始まるため（ほかの都府県は 1947 年から）。

出力
  data/history/muni_1970.csv   今の市区町村 と 1970 年の市町村 の組（都道府県, 今の市区町村, 1970年の市町村, 町字の数）
  data/history/town_1970.csv   町字ごと（lg_code, machiaza_id, 都道府県, 今の市区町村, 町字, 1970年の郡・市, 1970年の市町村）
  data/history/old1970.bin     GeoLog 用。rgeo_all.bin の点の順に、1970 年の市町村の番号（形式は README）
  data/history/bounds.bin      GeoLog の拡大地図用の境界線（今と 1970 年の 2 組。line.fgb から。形式は README）
  data/history/town_2000.csv   町字ごとの 2000 年（平成 12 年国勢調査）の市区町村と町丁・字
  data/history/old2000.bin     GeoLog 用。rgeo_all.bin の点の順に、2000 年の町丁・字の名前（'OLD2'：番号が u32 のほかは old1970.bin と同じ）

2000 年の町丁・字：e-Stat「平成12年国勢調査 町丁・字等別境界データ」（世界測地系緯度経度、Shape、都道府県ごと）。
  出典：政府統計の総合窓口（e-Stat）https://www.e-stat.go.jp/ （政府標準利用規約に従い加工して利用）
  raw/h12/h12kaXX.shp に置く：
    for i in $(seq -w 1 47); do curl -sfL -o raw/h12/h12ka$i.zip "https://www.e-stat.go.jp/gis/statmap-search/data?dlserveyId=A002005212000&code=$i&coordSys=1&format=shape&downloadType=5"; done
    （それぞれ unzip。測地系は JGD2000。今の点（JGD2011・JGD2000）との差は数 m 以下）

必要：pyogrio 0.13.0・shapely 2.2.0（~/Documents/clock/.venv に入れてある）
"""
import csv
import os
import struct
import sys
from collections import Counter

import numpy as np
import pyogrio
import shapely

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
FGB = os.path.join(ROOT, 'raw', 'history', 'polygon.fgb')
LINE_FGB = os.path.join(ROOT, 'raw', 'history', 'line.fgb')   # curl -sfL -o raw/history/line.fgb https://hanishina.net/maps/tiles/line.fgb
# 境界線の種類（line.fgb の TYPE）→ 番号。番号が小さいほど上の境界
LINE_TYPES = {'県': 1, '支庁': 2, '郡': 3, '市': 4, '町': 4, '村': 4, '区': 5, '特別区': 5, '海': 7, '湖': 8}
OUT = os.path.join(ROOT, 'data', 'history')
DATE = np.datetime64('1970-04-01')
NEAR_DEG = 0.03          # 約 3km
NOW_DATE = '2026-10-01'  # 「今」の境界線の日付（元データを新しくしたら合わせる）


def load_1970():
    meta, _, geom, f = pyogrio.raw.read(FGB)
    col = {n: f[i] for i, n in enumerate(meta['fields'].tolist())}
    m = (col['START'] <= DATE) & (col['END'] > DATE) & ~np.isin(col['TYPE'], ['湖', '帰属未定地'])
    idx = np.where(m)[0]
    polys = shapely.from_wkb(geom[idx])
    # 名前：郡（政令市の区では市の名前が入っている）と市町村
    prefix = [col['KEN'][i] + (col['GUN'][i] or '') for i in idx]
    name = [col['NAME'][i] for i in idx]
    return polys, prefix, name


def assign(polys, lat, lon):
    """点ごとに、入っている形の番号（無ければ近い形、それも無ければ -1）"""
    pts = shapely.points(lon, lat)
    tree = shapely.STRtree(polys)
    res = np.full(len(pts), -1, dtype=np.int64)
    pi, gi = tree.query(pts, predicate='within')
    res[pi] = gi                      # 2 つに入る点（境界の上）は後のほう。どちらでもよい
    miss = np.where(res < 0)[0]
    if len(miss):
        mi, mg = tree.query_nearest(pts[miss], max_distance=NEAR_DEG)
        res[miss[mi]] = mg
    return res


def rgeo_points(path):
    """rgeo_all.bin の点を、ファイルの順（＝GeoLog の RGeo の番号）に読む"""
    b = open(path, 'rb').read()
    (magic, ver, hsize, n, ncity, lat0, lon0, cell, rows, cols, off_cells, off_points) = struct.unpack_from('<4sHHIIiiIHHII', b, 0)
    assert magic == b'RGEO'
    starts = struct.unpack_from(f'<{rows * cols + 1}I', b, off_cells)
    lat = np.zeros(n)
    lon = np.zeros(n)
    cd = cell / 1e6
    for r in range(rows):
        for c in range(cols):
            k = r * cols + c
            s, e = starts[k], starts[k + 1]
            for i in range(s, e):
                dlat, dlon = struct.unpack_from('<HH', b, off_points + i * 10)
                lat[i] = (lat0 + r * cell) / 1e6 + dlat * cd / 65535
                lon[i] = (lon0 + c * cell) / 1e6 + dlon * cd / 65535
    return lat, lon


def main():
    os.makedirs(OUT, exist_ok=True)
    polys, prefix, name = load_1970()
    print(f'1970-04-01 の市区町村：{len(polys)}')

    # 町字ごと（towns.csv）
    rows = list(csv.DictReader(open(os.path.join(ROOT, 'data', 'towns.csv'), encoding='utf-8')))
    a = assign(polys, np.array([float(r['lat']) for r in rows]), np.array([float(r['lon']) for r in rows]))
    pair = Counter()
    with open(os.path.join(OUT, 'town_1970.csv'), 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['lg_code', 'machiaza_id', 'pref', 'city', 'town', 'gun_1970', 'muni_1970'])
        for r, k in zip(rows, a):
            g = prefix[k][len(r['pref']):] if k >= 0 and prefix[k].startswith(r['pref']) else (prefix[k] if k >= 0 else '')
            nm = name[k] if k >= 0 else ''
            w.writerow([r['lg_code'], r['machiaza_id'], r['pref'], r['city'], r['oaza_cho'] + r['koaza'] + r['chome'], g, nm])
            pair[(r['pref'], r['city'], prefix[k] + nm if k >= 0 else '')] += 1
    with open(os.path.join(OUT, 'muni_1970.csv'), 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['pref', 'city_now', 'muni_1970', 'towns'])
        for (p, c, o), n in sorted(pair.items()):
            w.writerow([p, c, o, n])
    print(f'町字 {len(rows)}（1970 年の市町村が分からない {int((a < 0).sum())}）、組 {len(pair)}')

    # GeoLog 用（rgeo_all.bin の点の順）
    lat, lon = rgeo_points(os.path.join(ROOT, 'data', 'rgeo_all.bin'))
    a2 = assign(polys, lat, lon)
    names = {}
    table = ['']                       # 0 番 = 分からない
    idx = np.zeros(len(a2), dtype=np.uint16)
    for i, k in enumerate(a2):
        if k < 0:
            continue
        key = prefix[k] + '\t' + name[k]
        if key not in names:
            names[key] = len(table)
            table.append(key)
        idx[i] = names[key]
    blob = bytearray()
    offs = []
    for s in table:
        offs.append(len(blob))
        blob += s.encode('utf-8') + b'\0'
    head = struct.pack('<4sIII', b'OLD1', len(idx), len(table), 19700401)
    body = idx.astype('<u2').tobytes() + struct.pack(f'<{len(offs)}I', *offs) + bytes(blob)
    open(os.path.join(OUT, 'old1970.bin'), 'wb').write(head + body)
    print(f'old1970.bin：点 {len(idx)}、名前 {len(table) - 1}、{len(head) + len(body):,} バイト')

    build_bounds([NOW_DATE, '1970-04-01'])
    build_2000(lat, lon, rows)


KANJI_DIGITS = '〇一二三四五六七八九'


def kanji_number(n):
    """1〜99 を漢数字に（例：1→一、10→十、12→十二、40→四十）。範囲外はそのまま"""
    if not 1 <= n <= 99:
        return str(n)
    tens, ones = divmod(n, 10)
    return ('' if tens == 0 else ('十' if tens == 1 else KANJI_DIGITS[tens] + '十')) + ('' if ones == 0 else KANJI_DIGITS[ones])


def normalize_chome(name):
    """「丸の内１丁目」「４０丁目」のように、丁目の前の数字（全角・半角）だけを漢数字にする（今のデータの「丸の内一丁目」に合わせる）。
    条・号など丁目以外の数字は変えない"""
    import re
    return re.sub(r'([0-9０-９]+)丁目',
                  lambda m: kanji_number(int(m.group(1).translate(str.maketrans('０１２３４５６７８９', '0123456789')))) + '丁目', name)


def load_h12():
    """平成 12 年国勢調査の町丁・字の形と名前（水面などは除く）"""
    import glob
    polys, muni, town = [], [], []
    for shp in sorted(glob.glob(os.path.join(ROOT, 'raw', 'h12', 'h12ka*.shp'))):
        meta, _, geom, f = pyogrio.raw.read(shp, encoding='cp932')
        col = {n: f[i] for i, n in enumerate(meta['fields'].tolist())}
        for i in range(len(geom)):
            if col['HCODE'][i] != 8101 or geom[i] is None:
                continue
            gst, css = col['GST_NAME'][i] or '', col['CSS_NAME'][i] or ''
            pref = col['PREF_NAME'][i] or col['KEN_NAME'][i] or ''   # 都道府県名が空の行がある（KEN_NAME で補う）
            m = pref + (gst + css if css else (col['CITY_NAME'][i] or gst))
            polys.append(geom[i])
            muni.append(m)
            town.append(normalize_chome(col['S_NAME'][i] or ''))
    return shapely.from_wkb(polys), muni, town


def build_2000(lat, lon, rows):
    polys, muni, town = load_h12()
    print(f'2000 年の町丁・字：{len(polys)}')
    a = assign(polys, np.array([float(r['lat']) for r in rows]), np.array([float(r['lon']) for r in rows]))
    with open(os.path.join(OUT, 'town_2000.csv'), 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['lg_code', 'machiaza_id', 'pref', 'city', 'town', 'muni_2000', 'town_2000'])
        for r, k in zip(rows, a):
            w.writerow([r['lg_code'], r['machiaza_id'], r['pref'], r['city'], r['oaza_cho'] + r['koaza'] + r['chome'],
                        muni[k] if k >= 0 else '', town[k] if k >= 0 else ''])
    a2 = assign(polys, lat, lon)
    # GeoLog が使うのは町丁・字の名前だけ（市町村は 1970 年のものを出す）
    names, table = {}, ['']
    idx = np.zeros(len(a2), dtype=np.uint32)
    for i, k in enumerate(a2):
        if k < 0 or not town[k]:
            continue
        key = town[k]
        if key not in names:
            names[key] = len(table)
            table.append(key)
        idx[i] = names[key]
    write_old(os.path.join(OUT, 'old2000.bin'), idx, table, 20001001)
    noname = sum(1 for k in a if k >= 0 and not town[k])
    print(f'町字 {len(rows)}（2000 年の区域に入らない {int((a < 0).sum())}、区域はあるが名前が無い {noname}）、old2000.bin 名前 {len(table) - 1}')


def write_old(path, idx, table, date):
    """名前の番号が 65535 までなら 'OLD1'（u16）、超えたら 'OLD2'（u32）"""
    blob = bytearray()
    offs = []
    for s in table:
        offs.append(len(blob))
        blob += s.encode('utf-8') + b'\0'
    wide = len(table) > 65535
    head = struct.pack('<4sIII', b'OLD2' if wide else b'OLD1', len(idx), len(table), date)
    open(path, 'wb').write(head + idx.astype('<u4' if wide else '<u2').tobytes() + struct.pack(f'<{len(offs)}I', *offs) + bytes(blob))


def build_bounds(dates):
    """境界線：dates の日ごとに有効な線。'BND1', u32 組の数 / 組ごと：u32 日付, u32 線の数 /
    線ごと：u8 種類, u8 陸湖海, u16 点の数, i32 南, 西, 北, 東（度×1e6）, 点の数 ×（i32 緯度, i32 経度）"""
    meta, _, geom, f = pyogrio.raw.read(LINE_FGB)
    col = {n: f[i] for i, n in enumerate(meta['fields'].tolist())}
    out = bytearray(struct.pack('<4sI', b'BND1', len(dates)))
    for d in dates:
        D = np.datetime64(d)
        m = np.where((col['START'] <= D) & (col['END'] > D))[0]
        lines = []
        for i in m:
            g = shapely.from_wkb(geom[i])
            for part in (g.geoms if hasattr(g, 'geoms') else [g]):
                c = shapely.get_coordinates(part)
                for k in range(0, len(c) - 1, 65000):   # 点の数は u16 に収める
                    lines.append((LINE_TYPES.get(col['TYPE'][i], 9), int(col['TYPE2'][i] or 0), c[k:k + 65001]))
        out += struct.pack('<II', int(d.replace('-', '')), len(lines))
        npts = 0
        for t, t2, c in lines:
            la = np.round(c[:, 1] * 1e6).astype('<i4')
            lo = np.round(c[:, 0] * 1e6).astype('<i4')
            out += struct.pack('<BBHiiii', t, t2, len(c), int(la.min()), int(lo.min()), int(la.max()), int(lo.max()))
            out += np.column_stack([la, lo]).astype('<i4').tobytes()
            npts += len(c)
        print(f'境界線 {d}：線 {len(lines)}、点 {npts}')
    open(os.path.join(OUT, 'bounds.bin'), 'wb').write(out)
    print(f'bounds.bin：{len(out):,} バイト')


if __name__ == '__main__':
    # 丁目の正規化の確認
    assert normalize_chome('丸の内１丁目') == '丸の内一丁目'
    assert normalize_chome('戸頭5丁目') == '戸頭五丁目'
    assert normalize_chome('十二丁目') == '十二丁目'
    assert normalize_chome('４０丁目') == '四十丁目' and normalize_chome('１２丁目') == '十二丁目' and normalize_chome('２１丁目') == '二十一丁目'
    assert normalize_chome('北一条西１０丁目') == '北一条西十丁目'
    assert normalize_chome('春光町一区１１条') == '春光町一区１１条'
    sys.exit(main())
