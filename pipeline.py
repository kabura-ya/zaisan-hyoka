# -*- coding: utf-8 -*-
"""
財産評価明細書 自動生成パイプライン
  Step1: 課税明細書CSV を読み込む
  Step2: 地目に応じて路線価図 or 倍率表PDF を一括ダウンロード

使い方:
    python pipeline.py <課税明細書CSV> [年分] [PDF保存先]
    例: python pipeline.py test_sample.csv r07 output

地目別の取得ロジック:
    宅地        → まず路線価図を試み、404/失敗時に倍率表へフォールバック
    田・畑・山林・その他 → 倍率表のみ取得
"""

import csv
import re
import sys
from pathlib import Path

from scraper import download_bairitu_pdf, download_rosenka_pdf

# 路線価図を優先する地目
ROSENKA_FIRST_CHIMOKU = {"宅地"}

# 倍率表のみを取得する地目（宅地以外）
BAIRITU_ONLY_CHIMOKU = {"田", "畑", "山林", "原野", "雑種地", "農地"}


# ===========================================================
# Step1: CSV読み込み
# ===========================================================

def load_csv(csv_path: str) -> tuple[str | None, list[dict]]:
    """
    CSVを読み込み、（年分, 土地リスト）を返す。
    先頭列「年分」の値を年分として採用する（最初の行の値を使用）。
    """
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    nendo = rows[0].get("年分") if rows else None
    return nendo, rows


# ===========================================================
# Step2: 住所パース
# ===========================================================

_PREFS = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]


def parse_address(address: str) -> tuple[str, str, str, str]:
    """
    地番文字列から（都道府県, 市区町村, 大字, 残り）に分割する。
    郡名は除外し、市区町村名のみを返す。
    政令指定都市の区（「横浜市西区」等）にも対応する。

    例: "東京都西多摩郡奥多摩町小丹波101番地"
        → ("東京都", "奥多摩町", "小丹波", "101番地")
    例: "神奈川県横浜市西区南幸1丁目1番地"
        → ("神奈川県", "横浜市西区", "南幸1丁目", "1番地")
    """
    pref = next((p for p in _PREFS if address.startswith(p)), None)
    if not pref:
        raise ValueError(f"都道府県が特定できません: {address}")

    rest = address[len(pref):]

    # 郡名を先に除去（例: "西多摩郡"）
    gun_match = re.match(r"^.+?郡", rest)
    rest_no_gun = rest[len(gun_match.group(0)):] if gun_match else rest
    gun_prefix = gun_match.group(0) if gun_match else ""

    # 政令市の区（「市」+「区」）を優先、それ以外は一般の市区町村
    # 例: "横浜市西区" → "横浜市西区"（左側が優先）
    # 例: "奥多摩町"   → "奥多摩町"
    m = re.match(r"^(.+?市.+?区|.+?[市区町村])", rest_no_gun)
    if not m:
        raise ValueError(f"市区町村が特定できません: {address}")

    city = m.group(0)  # 例: "横浜市西区" / "奥多摩町" / "千代田区"
    after_city = rest[len(gun_prefix) + len(city):]

    # 大字・地名（地番の手前まで）
    oaza_match = re.match(r"^(.+?)(\d+番(?:地\d*)?)?$", after_city)
    if oaza_match:
        oaza = oaza_match.group(1).strip()
        chiban = oaza_match.group(2) or ""
    else:
        oaza = after_city
        chiban = ""

    return pref, city, oaza, chiban


# ===========================================================
# Step3: PDF取得（地目別ロジック）
# ===========================================================

def fetch_pdf_for_land(
    pref: str,
    city: str,
    oaza: str,
    chimoku: str,
    nendo: str,
    output_dir: str,
    downloaded: dict[str, str],
) -> tuple[str, str]:
    """
    1筆分のPDFを取得する。

    Args:
        downloaded: {キャッシュキー: 保存パス} のキャッシュ（市区町村単位で重複取得を防ぐ）

    Returns:
        (ステータス文字列, 保存パス)
    """
    rosenka_dir = str(Path(output_dir) / "rosenka")
    bairitu_dir = str(Path(output_dir) / "bairitu")

    # 宅地は路線価図を優先、それ以外は倍率表のみ
    try_rosenka = chimoku in ROSENKA_FIRST_CHIMOKU

    if try_rosenka:
        # キャッシュキー: 路線価図は大字単位まで（同一大字なら同一図面）
        cache_key_rosenka = f"rosenka_{pref}_{city}_{oaza}"
        if cache_key_rosenka in downloaded:
            return "キャッシュ済み(路線価)", downloaded[cache_key_rosenka]

        try:
            path = download_rosenka_pdf(pref, city, oaza, nendo, rosenka_dir)
            downloaded[cache_key_rosenka] = path
            return "成功(路線価)", path
        except Exception as e:
            print(f"    路線価図取得失敗（倍率表へ切替）: {e}")

    # 倍率表（市区町村単位でキャッシュ）
    cache_key_bairitu = f"bairitu_{pref}_{city}"
    if cache_key_bairitu in downloaded:
        return "キャッシュ済み(倍率表)", downloaded[cache_key_bairitu]

    try:
        path = download_bairitu_pdf(pref, city, nendo, bairitu_dir)
        downloaded[cache_key_bairitu] = path
        return "成功(倍率表)", path
    except Exception as e:
        return f"失敗: {e}", ""


# ===========================================================
# メインパイプライン
# ===========================================================

def run_pipeline(
    input_path: str,
    nendo: str = "r07",
    pdf_dir: str = "output",
) -> None:
    """
    課税明細書CSVを読み込み、路線価図/倍率表PDFを一括ダウンロードする。

    Args:
        input_path: 課税明細書CSVのパス
        nendo     : 相続開始年分（例: "r07" = 令和7年）。CSVの年分列が優先される。
        pdf_dir   : PDF保存先ディレクトリ
    """
    if not input_path.lower().endswith(".csv"):
        raise ValueError(f"CSVファイルを指定してください: {input_path}")

    csv_nendo, lands = load_csv(input_path)
    nendo = csv_nendo or nendo
    print(f"CSV読み込み完了: {len(lands)}筆（年分: {nendo}）\n")

    Path(pdf_dir).mkdir(parents=True, exist_ok=True)
    result_rows = []
    downloaded: dict[str, str] = {}

    for i, land in enumerate(lands, 1):
        address = land.get("所在地", "").strip()
        chimoku = land.get("地目", "").strip()

        print(f"[{i}/{len(lands)}] {address}（{chimoku}）")

        if not address:
            result_rows.append({**land, "取得ステータス": "住所なし", "PDFパス": ""})
            continue

        try:
            pref, city, oaza, _ = parse_address(address)
        except ValueError as e:
            print(f"  住所パース失敗: {e}")
            result_rows.append({**land, "取得ステータス": f"住所パース失敗: {e}", "PDFパス": ""})
            continue

        status, path = fetch_pdf_for_land(
            pref, city, oaza, chimoku, nendo, pdf_dir, downloaded
        )
        print(f"  → {status}" + (f": {path}" if path else ""))

        result_rows.append({**land, "取得ステータス": status, "PDFパス": path})

    # 結果CSVを出力
    result_csv = Path(pdf_dir) / "result.csv"
    if result_rows:
        with open(result_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(result_rows[0].keys()))
            writer.writeheader()
            writer.writerows(result_rows)

    print(f"\n完了: {len(lands)}筆処理")
    ok = sum(1 for r in result_rows if "成功" in r.get("取得ステータス", ""))
    cached = sum(1 for r in result_rows if "キャッシュ" in r.get("取得ステータス", ""))
    ng = len(result_rows) - ok - cached
    print(f"  成功: {ok}筆  キャッシュ済み: {cached}筆  失敗: {ng}筆")
    print(f"  結果CSV: {result_csv}")


# ===========================================================
# エントリポイント
# ===========================================================

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使い方: python pipeline.py <課税明細書CSV> [年分] [PDF保存先]")
        print("例:     python pipeline.py test_sample.csv r07 output")
        sys.exit(1)

    input_file = sys.argv[1]
    nendo_arg = sys.argv[2] if len(sys.argv) > 2 else "r07"
    pdf_dir_arg = sys.argv[3] if len(sys.argv) > 3 else "output"

    run_pipeline(input_file, nendo_arg, pdf_dir_arg)
