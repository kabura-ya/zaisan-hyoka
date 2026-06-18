# -*- coding: utf-8 -*-
"""
国税庁 財産評価基準書サイト 倍率表PDFダウンローダー
https://www.rosenka.nta.go.jp/

URL構造:
  トップ    : /main_{nendo}/index.htm
  都道府県  : /main_{nendo}/{regional}/{pref}/pref_frm.htm
  倍率表一覧: /main_{nendo}/{regional}/{pref}/ratios/city_frm.htm
  市区町村  : /main_{nendo}/{regional}/{pref}/ratios/html/d{code}rf.htm
  PDF      : /main_{nendo}/{regional}/{pref}/ratios/pdf/d{code}rt.pdf
"""

import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

NTA_BASE = "https://www.rosenka.nta.go.jp"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; KaburaYa-ZaisanHyoka/1.0)",
    "Accept-Language": "ja,en;q=0.5",
}
REQUEST_INTERVAL = 0.8  # 秒（過負荷対策）


# ===========================================================
# HTTPユーティリティ
# ===========================================================

def _get(url: str) -> requests.Response:
    time.sleep(REQUEST_INTERVAL)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "shift_jis"
    return resp


def _soup(resp: requests.Response) -> BeautifulSoup:
    return BeautifulSoup(resp.text, "html.parser")


# ===========================================================
# 都道府県パス解決
# ===========================================================

@lru_cache(maxsize=10)
def _build_pref_path_map(nendo: str) -> dict[str, str]:
    """トップページから {都道府県名: pref_frm.htm の絶対URL} のマップを構築する"""
    top_url = f"{NTA_BASE}/main_{nendo}/index.htm"
    resp = _get(top_url)
    soup = _soup(resp)

    pref_map: dict[str, str] = {}
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        if "pref_frm.htm" in href and text:
            pref_map[text] = urljoin(top_url, href)

    return pref_map


def _get_pref_url(pref: str, nendo: str) -> str:
    pref_map = _build_pref_path_map(nendo)
    url = pref_map.get(pref)
    if not url:
        raise ValueError(f"都道府県 '{pref}' が見つかりません。取得可能: {list(pref_map.keys())}")
    return url


# ===========================================================
# 倍率表ページ解決
# ===========================================================

def _get_ratios_city_url(pref_url: str) -> str:
    """都道府県ページから倍率表一覧（city_frm.htm）のURLを返す"""
    resp = _get(pref_url)
    soup = _soup(resp)
    for a in soup.find_all("a"):
        if "ratios/city_frm.htm" in a.get("href", ""):
            return urljoin(pref_url, a["href"])
    raise ValueError(f"倍率表一覧ページへのリンクが見つかりません: {pref_url}")


def _get_city_ratio_url(ratios_city_url: str, city: str) -> str:
    """倍率表一覧から市区町村名に対応する d*rf.htm のURLを返す"""
    resp = _get(ratios_city_url)
    soup = _soup(resp)

    for a in soup.find_all("a"):
        if city in a.get_text(strip=True) and "rf.htm" in a.get("href", ""):
            return urljoin(ratios_city_url, a["href"])

    # 前方3文字での部分一致フォールバック
    candidates = []
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        if city[:3] in text and "rf.htm" in href:
            candidates.append((text, urljoin(ratios_city_url, href)))
    if candidates:
        return min(candidates, key=lambda x: len(x[0]))[1]

    raise ValueError(f"市区町村 '{city}' の倍率表ページが見つかりません: {ratios_city_url}")


def _get_pdf_url(html_url: str) -> str:
    """倍率表HTMLページのサイドバーリンクからPDF直リンクのURLを返す"""
    resp = _get(html_url)
    soup = _soup(resp)

    # サイドバーの「評価倍率表(PDF)のみを表示」リンクを探す
    for a in soup.find_all("a"):
        href = a.get("href", "")
        if href.endswith("rt.pdf"):
            return urljoin(html_url, href)

    raise ValueError(f"PDF URLが見つかりません: {html_url}")


# ===========================================================
# 公開API
# ===========================================================

def download_bairitu_pdf(
    pref: str,
    city: str,
    nendo: str = "r07",
    output_dir: str = "bairitu_pdfs",
) -> str:
    """
    国税庁サイトから倍率表PDFをダウンロードして保存する。

    Args:
        pref      : 都道府県名（例: "東京都"）
        city      : 市区町村名（例: "奥多摩町"）※郡名は含めない
        nendo     : 年分コード（例: "r06" = 令和6年）
        output_dir: 保存先ディレクトリ

    Returns:
        保存したPDFのファイルパス
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    pref_url = _get_pref_url(pref, nendo)
    ratios_city_url = _get_ratios_city_url(pref_url)
    city_ratio_url = _get_city_ratio_url(ratios_city_url, city)
    pdf_url = _get_pdf_url(city_ratio_url)

    resp = _get(pdf_url)

    filename = f"{nendo}_{pref}_{city}.pdf"
    output_path = Path(output_dir) / filename
    output_path.write_bytes(resp.content)

    return str(output_path)


# ===========================================================
# CLIデバッグ用
# ===========================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("使い方: python scraper.py <都道府県> <市区町村> [年分] [保存先ディレクトリ]")
        print("例:     python scraper.py 東京都 奥多摩町 r06 bairitu_pdfs")
        sys.exit(1)

    pref_arg = sys.argv[1]
    city_arg = sys.argv[2]
    nendo_arg = sys.argv[3] if len(sys.argv) > 3 else "r06"
    output_dir_arg = sys.argv[4] if len(sys.argv) > 4 else "bairitu_pdfs"

    print(f"ダウンロード中: {pref_arg} {city_arg} ({nendo_arg})")
    path = download_bairitu_pdf(pref_arg, city_arg, nendo_arg, output_dir_arg)
    print(f"保存完了: {path}")
