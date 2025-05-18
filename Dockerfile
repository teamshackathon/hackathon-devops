# ビルドステージ: 依存関係のインストールと不要なファイルの削除
FROM python:3.12-slim as builder

WORKDIR /app

# 依存パッケージのインストール 
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt && \
    find /root/.local -name '*.pyc' -delete && \
    find /root/.local -type d -name '__pycache__' -delete

# 実行ステージ: 最小限のランタイム環境
FROM python:3.12-slim

# 必要なパッケージだけをシステムにインストール
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ビルドステージからPythonパッケージをコピー
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# アプリケーションコードのコピー
COPY . .

# 環境変数の設定
ENV STREAMLIT_SERVER_BASE_URL_PATH="/monitor" \
    STREAMLIT_SERVER_ENABLE_CORS=false \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1


# tini を使ってプロセス管理を適切に
ENTRYPOINT ["/usr/bin/tini", "--"]

# Streamlitを起動
CMD ["streamlit", "run", "app.py"]

# ポート8501を公開
EXPOSE 8501