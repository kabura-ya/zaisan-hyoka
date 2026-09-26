# -*- coding: utf-8 -*-
"""
Web版 路線価図・倍率表PDF一括取得ツール

課税明細書CSVをアップロードすると、pipeline.py のロジックで
路線価図・倍率表PDFを一括取得し、ZIPファイルとして返す。
"""

import csv
import io
import os
import tempfile
import zipfile
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from pipeline import run_pipeline

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "zaisan-hyoka-dev")  # 本番はSECRET_KEY環境変数を設定すること

# 1回のアップロードで処理できる筆数の上限（国税庁サイトへの負荷対策。1筆あたり約1〜2秒かかる）
MAX_ROWS = 30


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", max_rows=MAX_ROWS)


@app.route("/run", methods=["POST"])
def run():
    file = request.files.get("csv_file")
    nendo = request.form.get("nendo", "").strip() or "r07"

    if not file or file.filename == "":
        return jsonify(error="CSVファイルを選択してください。"), 400

    if not file.filename.lower().endswith(".csv"):
        return jsonify(error="CSVファイルを指定してください。"), 400

    with tempfile.TemporaryDirectory() as work_dir:
        work_path = Path(work_dir)
        csv_path = work_path / "input.csv"
        file.save(csv_path)

        try:
            with csv_path.open(encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except UnicodeDecodeError:
            return jsonify(error="CSVの文字コードはUTF-8で保存してください。"), 400

        if not rows:
            return jsonify(error="CSVにデータ行がありません。"), 400

        if len(rows) > MAX_ROWS:
            return jsonify(
                error=(
                    f"1回のアップロードで処理できるのは{MAX_ROWS}筆までです"
                    f"（{len(rows)}筆が指定されました）。ファイルを分割してください。"
                )
            ), 400

        output_dir = work_path / "output"

        try:
            run_pipeline(str(csv_path), nendo, str(output_dir))
        except Exception as e:
            return jsonify(error=f"処理中にエラーが発生しました: {e}"), 500

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in output_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(output_dir))
        zip_buffer.seek(0)

    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name="zaisan-hyoka_result.zip",
    )


if __name__ == "__main__":
    app.run(debug=True)
