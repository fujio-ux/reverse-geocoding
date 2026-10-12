// rgeo.h をパソコンで試す：./rgeo_cli <rgeo.bin> <緯度> <経度> [<緯度> <経度> ...]
// 「-」を渡すと標準入力から「緯度 経度」の行を読み、1 行ずつ「lg_code,町字,距離」を出す（検証用）
#include <stdio.h>
#include <string.h>
#include "rgeo.h"

static bool rd(void *ctx, uint32_t off, void *buf, uint32_t len) {
  FILE *fp = (FILE *)ctx;
  return fseek(fp, off, SEEK_SET) == 0 && fread(buf, 1, len, fp) == len;
}

static void one(RgeoFile *f, double lat, double lon, bool csv) {
  RgeoResult r;
  int e = rgeo_nearest(f, lat, lon, &r);
  if (e != RGEO_OK) { printf(csv ? "ERR,%d\n" : "見つからない（%d）\n", e); return; }
  if (csv) printf("%06u,%s%s,%.1f\n", r.lg_code, r.town, r.chome, r.dist_m);
  else printf("%.6f,%.6f → %s%s%s%s（代表点 %.6f,%.6f まで %.0f m）\n", lat, lon, r.pref, r.city, r.town, r.chome, r.lat, r.lon, r.dist_m);
}

int main(int argc, char **argv) {
  if (argc < 3) { fprintf(stderr, "使い方：%s rgeo.bin 緯度 経度 ... | -\n", argv[0]); return 2; }
  FILE *fp = fopen(argv[1], "rb");
  if (!fp) { perror(argv[1]); return 1; }
  RgeoFile f;
  int e = rgeo_open(&f, rd, fp);
  if (e != RGEO_OK) { fprintf(stderr, "開けない（%d）\n", e); return 1; }
  if (strcmp(argv[2], "-") == 0) {
    double lat, lon;
    while (scanf("%lf %lf", &lat, &lon) == 2) one(&f, lat, lon, true);
    return 0;
  }
  printf("点 %u、市区町村 %u、区画 %ux%u、元データ %u\n", f.n_points, f.n_cities, f.rows, f.cols, f.data_date);
  for (int i = 2; i + 1 < argc; i += 2) one(&f, atof(argv[i]), atof(argv[i + 1]), false);
  return 0;
}
