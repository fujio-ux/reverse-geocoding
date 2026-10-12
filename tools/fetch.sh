#!/bin/sh
# アドレス・ベース・レジストリ（デジタル庁、CC BY 4.0）の町字マスターと町字マスター位置参照拡張（47 都道府県）を raw/ に用意する
#   入手先の一覧：https://dataset.address-br.digital.go.jp/
#   tools/fetch.sh        … raw/ にある zip を展開するだけ（リポジトリに入っている時点のデータ）
#   tools/fetch.sh -f     … 最新を取り直してから展開する
set -eu
cd "$(dirname "$0")/../raw"
mkdir -p pos
if [ "${1:-}" = "-f" ] || [ ! -f mt_town_all.csv.zip ]; then
  curl -sfL -o mt_town_all.csv.zip https://data.address-br.digital.go.jp/mt_town/mt_town_all.csv.zip
  for i in $(seq -w 1 47); do
    curl -sfL -o pos/mt_town_pos_pref$i.csv.zip https://data.address-br.digital.go.jp/mt_town_pos/pref/mt_town_pos_pref$i.csv.zip
  done
fi
unzip -o -q mt_town_all.csv.zip
(cd pos && unzip -o -q '*.zip')
echo "展開した CSV：$(ls mt_town_all.csv pos/*.csv | wc -l | tr -d ' ') 個"
