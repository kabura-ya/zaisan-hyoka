# -*- coding: utf-8 -*-
"""
国税庁 財産評価基準書サイト スクレイパー
https://www.rosenka.nta.go.jp/

URL構造:
  トップ           : /main_{nendo}/index.htm
  都道府県         : /main_{nendo}/{regional}/{pref}/pref_frm.htm
  ── 倍率表 ──
  市区町村一覧     : /main_{nendo}/{regional}/{pref}/ratios/city_frm.htm
  市区町村HTML     : /main_{nendo}/{regional}/{pref}/ratios/html/d{code}rf.htm
  倍率表PDF        : /main_{nendo}/{regional}/{pref}/ratios/pdf/d{code}rt.pdf
  ── 路線価図 ──
  市区町村一覧     : /main_{nendo}/{regional}/{pref}/prices/city_frm.htm
  地名索引         : /main_{nendo}/{regional}/{pref}/prices/d{code}fr.htm
     └ テーブル行に大字名と html/{図番}f.htm リンク
  図番HTMLページ   : /main_{nendo}/{regional}/{pref}/prices/html/{図番}f.htm
     └ 「路線価図(PDF)のみを表示」リンク → ../pdf/{図番}.pdf
  路線価図PDF      : /main_{nendo}/{regional}/{pref}/prices/pdf/{図番}.pdf
"""

import re
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
REQUEST_INTERVAL = 1.0  # 秒（過負荷対策）


# ===========================================================
# HTTPユーティリティ
# ===========================================================

def _get(url: str) -> requests.Response:
    time.sleep(REQUEST_INTERVAL)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp


def _soup(resp: requests.Response) -> BeautifulSoup:
    # content（バイト列）から直接パース: Shift-JIS を正確に処理する
    return BeautifulSoup(resp.content, "html.parser", from_encoding="shift_jis")


def _get_safe(url: str) -> requests.Response | None:
    """404/403 の場合に None を返す安全版"""
    try:
        return _get(url)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (404, 403):
            return None
        raise


# ===========================================================
# 大字名の正規化
# ===========================================================

def _normalize_oaza(oaza: str) -> str:
    """
    大字名を正規化する。
    - 半角数字 → 全角数字（サイト側が全角表記のため）
    - 末尾「丁目」を除去
    例: "丸の内1丁目" → "丸の内１"
    """
    result = ""
    for ch in oaza:
        if "0" <= ch <= "9":
            result += chr(ord("０") + ord(ch) - ord("0"))
        else:
            result += ch
    return result.replace("丁目", "").strip()


# ===========================================================
# 都道府県パス解決
# ===========================================================

@lru_cache(maxsize=10)
def _build_pref_path_map(nendo: str) -> dict[str, str]:
    """トップページから {都道府県名: pref_frm.htm の絶対URL} のマップを構築する"""
    top_url = f"{NTA_BASE}/main_{nendo}/index.htm"
    soup = _soup(_get(top_url))

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


def _pref_base_url(pref_url: str) -> str:
    """pref_frm.htm の一つ上のディレクトリURL（末尾スラッシュあり）"""
    return pref_url.rsplit("/", 1)[0] + "/"


# ===========================================================
# 共通: 市区町村リンク検索
# ===========================================================

def _find_city_link(city_frm_url: str, city: str) -> str:
    """
    city_frm.htm から市区町村名に対応するリンクURLを返す。
    政令市の区（「横浜市西区」→「西区」）にも対応する。
    """
    soup = _soup(_get(city_frm_url))
    base = city_frm_url.rsplit("/", 1)[0] + "/"

    links = [(a.get_text(strip=True), a.get("href", ""))
             for a in soup.find_all("a") if a.get("href")]

    # 1. 完全一致
    for text, href in links:
        if text == city:
            return urljoin(base, href)

    # 2. 政令市の区：「横浜市西区」→「西区」で再検索
    #    サイト側が「西区」のみのリンクテキストになっている場合の対応
    if re.search(r"市.+区$", city):
        ward = re.sub(r"^.+市", "", city)  # "西区"
        for text, href in links:
            if text == ward:
                return urljoin(base, href)

    # 3. 前方3文字フォールバック
    candidates = [(text, urljoin(base, href))
                  for text, href in links if city[:3] in text]
    if candidates:
        return min(candidates, key=lambda x: len(x[0]))[1]

    raise ValueError(f"市区町村 '{city}' のリンクが見つかりません: {city_frm_url}")


# ===========================================================
# 倍率表 取得
# ===========================================================

def _get_ratios_city_url(pref_url: str) -> str:
    """都道府県ページから倍率表一覧（ratios/city_frm.htm）のURLを返す"""
    soup = _soup(_get(pref_url))
    for a in soup.find_all("a"):
        if "ratios/city_frm.htm" in a.get("href", ""):
            return urljoin(pref_url, a["href"])
    return _pref_base_url(pref_url) + "ratios/city_frm.htm"


def _get_bairitu_pdf_url(city_html_url: str) -> str:
    """倍率表HTMLページから PDF直リンク URL を返す"""
    soup = _soup(_get(city_html_url))
    for a in soup.find_all("a"):
        href = a.get("href", "")
        if href.endswith("rt.pdf"):
            return urljoin(city_html_url, href)
    raise ValueError(f"倍率表PDFリンクが見つかりません: {city_html_url}")


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
        nendo     : 年分コード（例: "r07" = 令和7年）
        output_dir: 保存先ディレクトリ

    Returns:
        保存したPDFのファイルパス
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    pref_url = _get_pref_url(pref, nendo)
    ratios_city_url = _get_ratios_city_url(pref_url)
    city_html_url = _find_city_link(ratios_city_url, city)
    pdf_url = _get_bairitu_pdf_url(city_html_url)

    resp = _get(pdf_url)
    filename = f"{nendo}_{pref}_{city}_bairitu.pdf"
    output_path = Path(output_dir) / filename
    output_path.write_bytes(resp.content)

    return str(output_path)


# ===========================================================
# 路線価図 取得
# ===========================================================

def _get_prices_city_url(pref_url: str) -> str:
    """都道府県ページから路線価図一覧（prices/city_frm.htm）のURLを返す"""
    soup = _soup(_get(pref_url))
    for a in soup.find_all("a"):
        if "prices/city_frm.htm" in a.get("href", ""):
            return urljoin(pref_url, a["href"])
    return _pref_base_url(pref_url) + "prices/city_frm.htm"


def _find_zuhan_from_table(city_index_url: str, oaza: str) -> list[str]:
    """
    d{code}fr.htm のテーブルから大字名に対応する html/{図番}f.htm URL を返す。

    テーブル構造:
      <tr><td>大字名</td><td><a href="html/15004f.htm">15004</a></td>...</tr>

    Args:
        city_index_url: d{code}fr.htm の絶対URL
        oaza          : 大字名（例: "丸の内1丁目"）

    Returns:
        対応する html/{図番}f.htm の絶対URLリスト（重複なし・見つかった順）
    """
    soup = _soup(_get(city_index_url))
    base = city_index_url.rsplit("/", 1)[0] + "/"

    oaza_norm = _normalize_oaza(oaza)
    # プレフィックス（丁目番号を除いた部分）
    oaza_prefix = re.sub(r"[０-９\d]+$", "", oaza_norm).strip()

    result_urls = []
    for tr in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        flat = "".join(cells)

        match = (
            oaza_norm in flat
            or (oaza_prefix and any(
                c == oaza_norm or (oaza_prefix and c.startswith(oaza_prefix))
                for c in cells
            ))
        )
        if not match:
            continue

        for a in tr.find_all("a"):
            href = a.get("href", "")
            if re.search(r"html/\d+f\.htm", href):
                result_urls.append(urljoin(base, href))

    # 重複除去・順序保持
    seen: set[str] = set()
    unique = []
    for u in result_urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def _get_rosenka_pdf_url(zuhan_htm_url: str) -> str:
    """
    html/{図番}f.htm から「路線価図(PDF)のみを表示」リンクを返す。
    リンクパターン: ../pdf/{図番}.pdf
    """
    soup = _soup(_get(zuhan_htm_url))
    for a in soup.find_all("a"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if ("PDF" in text or re.search(r"\.\./pdf/\d+\.pdf", href)) \
                and href.endswith(".pdf") and not href.endswith("rt.pdf"):
            return urljoin(zuhan_htm_url, href)
    raise ValueError(f"路線価図PDFリンクが見つかりません: {zuhan_htm_url}")


def download_rosenka_pdf(
    pref: str,
    city: str,
    oaza: str = "",
    nendo: str = "r07",
    output_dir: str = "rosenka_pdfs",
) -> list[str]:
    """
    国税庁サイトから路線価図PDFをダウンロードして保存する。

    路線価地域のみ対象。路線価地域でない場合（404等）は ValueError を送出するため、
    呼び出し元で倍率表にフォールバックすること。

    Args:
        pref      : 都道府県名（例: "東京都"）
        city      : 市区町村名（例: "千代田区"）
        oaza      : 大字・丁目名（例: "丸の内1丁目"）指定すると該当図を優先
        nendo     : 年分コード（例: "r07" = 令和7年）
        output_dir: 保存先ディレクトリ

    Returns:
        保存したPDFのファイルパスのリスト（大字に複数の図番がある場合は複数）
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    pref_url = _get_pref_url(pref, nendo)
    prices_city_url = _get_prices_city_url(pref_url)
    city_index_url = _find_city_link(prices_city_url, city)

    # 大字名からテーブルで図番HTMを取得
    zuhan_htm_urls = _find_zuhan_from_table(city_index_url, oaza) if oaza else []

    # oaza 未指定またはテーブルでヒットしない場合 → 最初の図番を使用
    if not zuhan_htm_urls:
        soup = _soup(_get(city_index_url))
        base = city_index_url.rsplit("/", 1)[0] + "/"
        seen: set[str] = set()
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if re.search(r"html/\d+f\.htm", href):
                url = urljoin(base, href)
                if url not in seen:
                    seen.add(url)
                    zuhan_htm_urls.append(url)
                    break

    if not zuhan_htm_urls:
        raise ValueError(f"図番HTMリンクが見つかりません: {city_index_url}")

    oaza_part = f"_{_normalize_oaza(oaza)}" if oaza else ""
    saved_paths = []
    for zuhan_htm_url in zuhan_htm_urls:
        m = re.search(r"html/(\d+)f\.htm", zuhan_htm_url)
        zuhan_no = f"_{m.group(1)}" if m else f"_{len(saved_paths) + 1}"
        pdf_url = _get_rosenka_pdf_url(zuhan_htm_url)
        resp = _get(pdf_url)
        filename = f"{nendo}_{pref}_{city}{oaza_part}{zuhan_no}_rosenka.pdf"
        output_path = Path(output_dir) / filename
        output_path.write_bytes(resp.content)
        saved_paths.append(str(output_path))

    return saved_paths


# ===========================================================
# CLIデバッグ用
# ===========================================================

if __name__ == "__main__":
    import sys

    usage = (
        "使い方: python scraper.py <モード> <都道府県> <市区町村> [大字] [年分] [保存先]\n"
        "  モード: bairitu（倍率表）または rosenka（路線価図）\n"
        "例（倍率表）: python scraper.py bairitu 東京都 奥多摩町 '' r07 output\n"
        "例（路線価）: python scraper.py rosenka 東京都 千代田区 丸の内1丁目 r07 output"
    )

    if len(sys.argv) < 4:
        print(usage)
        sys.exit(1)

    mode = sys.argv[1]
    pref_arg = sys.argv[2]
    city_arg = sys.argv[3]
    oaza_arg = sys.argv[4] if len(sys.argv) > 4 else ""
    nendo_arg = sys.argv[5] if len(sys.argv) > 5 else "r07"
    output_dir_arg = sys.argv[6] if len(sys.argv) > 6 else "output"

    if mode == "bairitu":
        print(f"倍率表ダウンロード中: {pref_arg} {city_arg} ({nendo_arg})")
        path = download_bairitu_pdf(pref_arg, city_arg, nendo_arg, output_dir_arg)
        print(f"保存完了: {path}")
    elif mode == "rosenka":
        print(f"路線価図ダウンロード中: {pref_arg} {city_arg} {oaza_arg} ({nendo_arg})")
        path = download_rosenka_pdf(pref_arg, city_arg, oaza_arg, nendo_arg, output_dir_arg)
        print(f"保存完了: {path}")
    else:
        print(f"不明なモード: {mode}")
        print(usage)
        sys.exit(1)
