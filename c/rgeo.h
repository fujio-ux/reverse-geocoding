// rgeo.h — RGEO 形式（README）から、緯度経度に一番近い町字を探す。ヘッダーだけで使える C / C++。
//
// ファイルの読み方は呼ぶ側が決める（ESP32 なら LittleFS・SD の File、フラッシュの区画、メモリ上の配列など）：
//   static bool rd(void *ctx, uint32_t off, void *buf, uint32_t len) { ... len バイト読めたら true ... }
//   RgeoFile f;
//   if (rgeo_open(&f, rd, ctx) == RGEO_OK) {
//     RgeoResult r;
//     if (rgeo_nearest(&f, 35.681236, 139.767125, &r) == RGEO_OK)
//       printf("%s%s%s%s %.0fm\n", r.pref, r.city, r.town, r.chome, r.dist_m);
//   }
// メモリは RgeoFile と RgeoResult の分だけ（malloc しない）。
#pragma once
#include <math.h>
#include <stdlib.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

enum { RGEO_OK = 0, RGEO_E_READ = -1, RGEO_E_FORMAT = -2, RGEO_E_NOTFOUND = -3 };

typedef bool (*RgeoRead)(void *ctx, uint32_t off, void *buf, uint32_t len);

typedef struct {
  RgeoRead read;
  void *ctx;
  uint32_t n_points, n_cities;
  int32_t lat0, lon0;     // 区画の南西の端（度×1e6）
  uint32_t cell;          // 区画の大きさ（度×1e6）
  uint16_t rows, cols;
  uint32_t off_cells, off_points, off_cities, off_chome, off_strings, size_strings;
  uint16_t n_chome;
  uint32_t data_date;     // 元データの日付 YYYYMMDD
} RgeoFile;

#ifndef RGEO_NAME_MAX
#define RGEO_NAME_MAX 96
#endif

typedef struct {
  char pref[16];               // 都道府県（例：東京都）
  char city[48];               // 郡・市・区（例：千代田区、札幌市中央区）
  char town[RGEO_NAME_MAX];    // 大字・町＋小字（例：丸の内）
  char chome[24];              // 丁目（例：一丁目。無ければ空）
  uint32_t lg_code;            // 全国地方公共団体コード（6 桁）
  double lat, lon;             // 見つかった町字の代表点
  double dist_m;               // 代表点までのおおよその距離（メートル）
} RgeoResult;

static inline uint32_t rgeo__u32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static inline uint16_t rgeo__u16(const uint8_t *p) { return p[0] | p[1] << 8; }

static inline int rgeo_open(RgeoFile *f, RgeoRead rd, void *ctx) {
  uint8_t h[68];
  memset(f, 0, sizeof *f);
  f->read = rd;
  f->ctx = ctx;
  if (!rd(ctx, 0, h, sizeof h)) return RGEO_E_READ;
  if (memcmp(h, "RGEO", 4) != 0 || rgeo__u16(h + 4) != 1 || rgeo__u16(h + 6) != 68) return RGEO_E_FORMAT;
  f->n_points = rgeo__u32(h + 8);
  f->n_cities = rgeo__u32(h + 12);
  f->lat0 = (int32_t)rgeo__u32(h + 16);
  f->lon0 = (int32_t)rgeo__u32(h + 20);
  f->cell = rgeo__u32(h + 24);
  f->rows = rgeo__u16(h + 28);
  f->cols = rgeo__u16(h + 30);
  f->off_cells = rgeo__u32(h + 32);
  f->off_points = rgeo__u32(h + 36);
  f->off_cities = rgeo__u32(h + 40);
  f->off_chome = rgeo__u32(h + 44);
  f->off_strings = rgeo__u32(h + 48);
  f->size_strings = rgeo__u32(h + 52);
  f->n_chome = rgeo__u16(h + 56);
  f->data_date = rgeo__u32(h + 60);
  if (f->cell == 0 || f->rows == 0 || f->cols == 0) return RGEO_E_FORMAT;
  return RGEO_OK;
}

// 文字列の表の off 番地から、NUL まで（最大 n-1 バイト）を読む
static inline bool rgeo__str(const RgeoFile *f, uint32_t off, char *out, uint32_t n) {
  uint32_t len = n - 1;
  if (off >= f->size_strings) return false;
  if (off + len > f->size_strings) len = f->size_strings - off;
  if (!f->read(f->ctx, f->off_strings + off, out, len)) return false;
  out[len] = 0;
  out[strnlen(out, len)] = 0;
  return true;
}

// 2 点の距離（メートル、正距円筒近似。数十 km までなら十分）
static inline double rgeo__dist(double lat1, double lon1, double lat2, double lon2) {
  const double R = 6371000.0, D = 3.14159265358979323846 / 180.0;
  double x = (lon2 - lon1) * D * cos((lat1 + lat2) * 0.5 * D);
  double y = (lat2 - lat1) * D;
  return R * sqrt(x * x + y * y);
}

// 一番近い代表点を探す。区画を内側から 1 周ずつ広げ、それより外に近い点が無いと分かったら終わる
static inline int rgeo_nearest(const RgeoFile *f, double lat, double lon, RgeoResult *res) {
  const double cellDeg = f->cell / 1e6;
  int r0 = (int)floor((lat * 1e6 - f->lat0) / f->cell);
  int c0 = (int)floor((lon * 1e6 - f->lon0) / f->cell);
  double best = INFINITY;
  uint8_t bestRec[10];
  int bestR = 0, bestC = 0;
  // 1 区画ぶんの最短距離（経度方向が短いので cos を掛ける。北の端で見積もる）
  double cosMax = cos((fabs(lat) + cellDeg) * 3.14159265358979323846 / 180.0);
  double ringM = cellDeg * 111195.0 * (cosMax < 1.0 ? cosMax : 1.0);
  // 範囲の外から始まっても、範囲を全部覆うまで広げられるように
  int maxRing = f->rows + f->cols + abs(r0) + abs(c0);
  for (int k = 0; k <= maxRing; k++) {
    if (best <= (k - 1) * ringM && k > 0) break;   // k 周目より外は、少なくとも (k-1) 区画ぶん離れている
    for (int dr = -k; dr <= k; dr++) {
      int step = (dr == -k || dr == k) ? 1 : 2 * k;   // k = 0 のときは dr == -k なので 1   // 端の行は全部、ほかは左右の 2 つだけ
      for (int dc = -k; dc <= k; dc += step) {
        int r = r0 + dr, c = c0 + dc;
        if (r < 0 || c < 0 || r >= f->rows || c >= f->cols) continue;
        uint8_t se[8];
        uint32_t ci = (uint32_t)r * f->cols + c;
        if (!f->read(f->ctx, f->off_cells + ci * 4, se, 8)) return RGEO_E_READ;
        uint32_t s = rgeo__u32(se), e = rgeo__u32(se + 4);
        double clat = (f->lat0 + (double)r * f->cell) / 1e6, clon = (f->lon0 + (double)c * f->cell) / 1e6;
        uint8_t buf[10 * 32];
        for (uint32_t i = s; i < e;) {
          uint32_t n = e - i > 32 ? 32 : e - i;
          if (!f->read(f->ctx, f->off_points + i * 10, buf, n * 10)) return RGEO_E_READ;
          for (uint32_t j = 0; j < n; j++) {
            const uint8_t *p = buf + j * 10;
            double plat = clat + rgeo__u16(p) * cellDeg / 65535.0;
            double plon = clon + rgeo__u16(p + 2) * cellDeg / 65535.0;
            double d = rgeo__dist(lat, lon, plat, plon);
            if (d < best) { best = d; memcpy(bestRec, p, 10); bestR = r; bestC = c; }
          }
          i += n;
        }
      }
    }
  }
  if (best == INFINITY) return RGEO_E_NOTFOUND;

  memset(res, 0, sizeof *res);
  res->lat = (f->lat0 + (double)bestR * f->cell) / 1e6 + rgeo__u16(bestRec) * cellDeg / 65535.0;
  res->lon = (f->lon0 + (double)bestC * f->cell) / 1e6 + rgeo__u16(bestRec + 2) * cellDeg / 65535.0;
  res->dist_m = best;
  uint16_t city = rgeo__u16(bestRec + 4);
  uint32_t name = bestRec[6] | bestRec[7] << 8 | (uint32_t)bestRec[8] << 16;
  uint8_t chome = bestRec[9];
  uint8_t ce[12];
  if (city >= f->n_cities || !f->read(f->ctx, f->off_cities + city * 12u, ce, 12)) return RGEO_E_READ;
  res->lg_code = rgeo__u32(ce);
  if (!rgeo__str(f, rgeo__u32(ce + 4), res->pref, sizeof res->pref)) return RGEO_E_READ;
  if (!rgeo__str(f, rgeo__u32(ce + 8), res->city, sizeof res->city)) return RGEO_E_READ;
  if (!rgeo__str(f, name, res->town, sizeof res->town)) return RGEO_E_READ;
  if (chome) {
    uint8_t co[4];
    if (chome >= f->n_chome || !f->read(f->ctx, f->off_chome + chome * 4u, co, 4)) return RGEO_E_READ;
    if (!rgeo__str(f, rgeo__u32(co), res->chome, sizeof res->chome)) return RGEO_E_READ;
  }
  return RGEO_OK;
}

#ifdef __cplusplus
}
#endif
