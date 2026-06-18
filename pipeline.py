# -*- coding: utf-8 -*-
"""
財産評価明細書 自動生成パイプライン
  Step1: 課税明細書CSV（Claude Chatで変換済みのもの）を読み込む
  Step2: 倍率表PDFをダウンロード（市区町村単位）
"""

import csv
import re

from scraper import download_bairitu_pdf


# ===========================================================
# Step1: CSV読み込み
# ===========================================================

def load_csv(csv_path: str) -> tuple[str | None, list[dict]]:
    """
    CSVを読み込み、（年分, 土地リスト）を返す。
    先頭列「年分」の値を年分として採用する（最初の行の値を使用）。
    「年分」列がない場合は None を返す。
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


def parse_address(address: str) -> tuple[str, str, str]:
    """地番文字列から（都道府県, 市区町村, 残り）に分割する。郡名は除く。"""
    pref = next((p for p in _PREFS if address.startswith(p)), None)
    if not pref:
        raise ValueError(f"都道府県が特定できません: {address}")

    rest = address[len(pref):]
    match = re.match(r"^(.+?[市区町村])", rest)
    if not match:
        raise ValueError(f"市区町村が特定できません: {address}")

    city_full = match.group(1)
    # 「○○郡△△町」のような場合、郡を除いて「△△町」のみを返す
    gun_match = re.match(r"^.+?郡(.+?[市区町村])$", city_full)
    city = gun_match.group(1) if gun_match else city_full

    chiban = rest[len(city_full):]
    return pref, city, chiban


# ===========================================================
# メインパイプライン
# ===========================================================

def run_pipeline(
    input_path: str,
    nendo: str = "r07",
    pdf_dir: str = "bairitu_pdfs",
) -> None:
    """
    課税明細書CSVを読み込み、倍率表PDFを市区町村単位でダウンロードする。
    CSVはClaude Chatで課税明細書PDFを変換して用意すること。

    Args:
        input_path: 課税明細書CSVのパス
        nendo     : 相続開始年分（例: "r07" = 令和7年）。CSVの年分列が優先される。
        pdf_dir   : 倍率表PDF保存先ディレクトリ
    """
    if not input_path.lower().endswith(".csv"):
        raise ValueError(f"CSVファイルを指定してください: {input_path}")

    csv_nendo, lands = load_csv(input_path)
    nendo = csv_nendo or nendo
    print(f"CSV読み込み: {len(lands)}筆（年分: {nendo}）")

    downloaded: set[str] = set()

    for land in lands:
        address = land["所在地"].strip()
        pref, city, _ = parse_address(address)
        cache_key = f"{pref}_{city}"

        if cache_key not in downloaded:
            print(f"倍率表PDFダウンロード中: {pref}{city}（{nendo}）")
            try:
                path = download_bairitu_pdf(pref, city, nendo, pdf_dir)
                downloaded.add(cache_key)
                print(f"  → 保存: {path}")
            except Exception as e:
                print(f"  → 失敗: {e}")


# ===========================================================
# 使用例
# ===========================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("使い方: python pipeline.py <課税明細書CSV> [年分] [PDF保存先]")
        print("例:     python pipeline.py kazeimeisaisho.csv r07 bairitu_pdfs")
        print("※ CSVはClaude ChatでPDFを変換して用意してください")
        sys.exit(1)

    input_file = sys.argv[1]
    nendo_arg = sys.argv[2] if len(sys.argv) > 2 else "r07"
    pdf_dir_arg = sys.argv[3] if len(sys.argv) > 3 else "bairitu_pdfs"

    run_pipeline(input_file, nendo_arg, pdf_dir_arg)
