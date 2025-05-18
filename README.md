# Git Release Monitor & K8s Manager

Streamlitを使用したGitリポジトリのリリースモニタリングとKubernetesデプロイメント管理アプリケーション。

## 機能

このアプリケーションは以下の2つの主要な機能を提供します：

1. **リリースモニタリング**:
   - 指定したGitリポジトリの新しいリリースを定期的に確認します
   - 新しいリリースが検出された場合、Kubernetesデプロイメントを自動的に再起動します
   - 設定可能な間隔でポーリング処理を行います
   - 複数のリポジトリとデプロイメントを同時に監視できます
   - 最新リリース情報はconfig.jsonに保存され、アプリケーション再起動間で保持されます

2. **バージョン管理とロールバック**:
   - Gitリポジトリのリリース一覧を表示します
   - 特定のバージョンを選択してロールバックを実行できます
   - Kubernetesデプロイメントの詳細ステータスを確認できます

## 必要条件

- Python 3.8以上
- Streamlit
- Kubernetes Python クライアント
- Requests

## インストール

1. リポジトリをクローンします
   ```bash
   git clone https://github.com/yourusername/hackathon-devops.git
   cd hackathon-devops
   ```

2. 必要なパッケージをインストールします
   ```bash
   pip install -r requirements.txt
   ```

## 使用方法

1. アプリケーションを起動します
   ```bash
   streamlit run app.py
   ```

2. ブラウザでStreamlitアプリにアクセスします（通常は http://localhost:8501）

3. サイドバーで以下の情報を設定します:
   - GitHubリポジトリ（形式: owner/repo）
   - GitHub Token（プライベートリポジトリやAPIレート制限を避けるため）
   - Kubernetesネームスペース
   - Kubernetesデプロイメント名
   - ポーリング間隔（秒）

4. 「Start Monitoring」をクリックしてモニタリングを開始します

5. 「Release History」タブでバージョンを選択してロールバックを実行できます

6. 「K8s Status」タブでデプロイメントの詳細情報を確認できます

## Kubernetes設定

このアプリケーションは以下の方法でKubernetesクラスターに接続します:

1. ローカルの `.kube/config` ファイルを使用
2. クラスター内実行時は自動的にクラスター内認証情報を使用

適切な権限を持つサービスアカウントで実行するか、必要な権限を持つkubeconfigを使用してください。

## データ永続化

- モニタリング設定とターゲット情報は `config.json` に保存されます
- 最新のリリース情報も `config.json` に保存され、アプリケーションの再起動後も利用可能です
- アクティブなモニタリングは自動的にアプリケーション起動時に再開されます

## 注意事項

- セキュリティのため、GitHub Tokenやその他の機密情報は環境変数を使用するか、Kubernetesのシークレットとして管理することをお勧めします
- 本番環境での使用前に、アクセス管理とセキュリティ設定を十分に確認してください