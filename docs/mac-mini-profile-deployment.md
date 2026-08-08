# Mac miniプロファイル運用

この手順は、他のサービスが稼働しているMac miniへ、ワコウCSVツールを既存のmacOSログインユーザー内の独立プロファイルとして配置するためのものです。別PCのAIエージェントへ渡しても、秘密情報を会話やログへ出さず、同じ手順で準備・検証できることを目的とします。

## 構成

`production`プロファイル、ポート`8100`の例では、次のように分離されます。

```text
ソースコード
  任意のclone先/csv_transfer_for_wakou

実行プロファイル
  ~/Library/Application Support/WakouCSV/profiles/production/
    ├── .env                         # 秘密情報、権限600
    ├── profile.json                 # ポート・ソース位置などの非秘密設定
    ├── data/export-history.sqlite3  # 共通出力履歴
    └── logs/
        ├── app.log
        └── app-error.log

常駐設定
  ~/Library/LaunchAgents/jp.co.wakou.csv-transfer.production.plist
```

アプリは`127.0.0.1:8100`だけで待ち受けます。複数PCからのアクセスは、ポートをLANやインターネットへ直接公開せず、Tailscale ServeでHTTPS化します。

この方式で分離されるもの：

- launchdラベル
- 待受ポート
- `.env`
- SQLite出力履歴
- 標準出力・エラーログ
- Python依存関係を管理する`uv`プロジェクト

## 前提

- Mac miniへ対象のmacOSユーザーでログインできる
- macOSユーザーのホームディレクトリへ書込みできる
- Gitと`uv`が利用できる
- ShopifyアプリのClient ID／Secretを人間が安全に入力できる
- 本番用の佐川・ヤマト固定値が確認済みである
- 複数PCから使う場合はTailscaleを各端末へ導入できる

これはユーザー単位のLaunchAgentです。Mac miniを再起動した場合、そのmacOSユーザーがログインすると自動起動します。ログイン前から起動するLaunchDaemonが必要な場合は、管理者権限と別の設計が必要です。

## 1. ポートとプロファイル名を決める

他サービスと重複しないポートを選びます。以下では`8100`を使用します。

```bash
lsof -nP -iTCP:8100 -sTCP:LISTEN
```

何も表示されなければ未使用です。

プロファイル名は、小文字英数字とハイフンのみ、32文字以内です。

```text
production
```

本番Shopifyへ接続するプロファイルは1つだけにしてください。同じ本番ストアへ複数プロファイルを接続すると、SQLite履歴が分かれて重複出力防止が成立しません。

## 2. ソースを準備する

```bash
mkdir -p "$HOME/Services"
cd "$HOME/Services"
git clone https://github.com/UNLegume/csv_transfer_for_wakou.git
cd csv_transfer_for_wakou
uv sync --extra dev
```

すでにclone済みの場合は、既存ディレクトリを使用します。

```bash
cd /path/to/csv_transfer_for_wakou
git pull --ff-only
uv sync --extra dev
```

## 3. 実行プロファイルを準備する

リポジトリ直下で実行します。

```bash
uv run wakou-macos-profile prepare \
  --profile production \
  --app-dir "$PWD" \
  --port 8100
```

このコマンドは以下を行います。

- 専用プロファイルディレクトリを権限`700`で作成
- `data`と`logs`を作成
- `.env.example`を`.env`へ初回だけコピー
- `.env`を権限`600`に設定
- 非秘密の`profile.json`を作成

既存の`.env`は上書きしません。

確認：

```bash
uv run wakou-macos-profile validate --profile production
```

初回はサンプル値が残っているため失敗するのが正常です。エラーには環境変数名だけが表示され、秘密値は表示されません。

## 4. 秘密情報を人間が設定する

設定ファイル：

```text
~/Library/Application Support/WakouCSV/profiles/production/.env
```

このファイルには以下が含まれます。

- Shopify Client ID／Secret
- Basic認証パスワード
- ヤマト／佐川CSVの出力設定

AIエージェントは次を厳守します。

- `.env`の内容をチャットへ貼らない
- `cat`、ログ出力、スクリーンショットで秘密値を表示しない
- Client Secretやパスワードをコマンドライン引数へ渡さない
- Gitへ追加しない
- 人間の入力完了前に常駐サービスを起動しない

人間がMac mini上で直接編集します。

```bash
open -e "$HOME/Library/Application Support/WakouCSV/profiles/production/.env"
```

入力後に権限を確認します。

```bash
chmod 600 "$HOME/Library/Application Support/WakouCSV/profiles/production/.env"
```

## 5. 設定を安全に検証する

```bash
cd "$HOME/Services/csv_transfer_for_wakou"
uv run wakou-macos-profile validate --profile production
```

成功時：

```text
profile_validation=ok
```

この検証は秘密値そのものを出力しません。必須項目、サンプル値の残存、ファイル権限、所有者、管理パスのシンボリックリンク差し替え、`uv`とソースコードの位置、ほかのプロファイルとのポート重複、別プロセスによるポート使用を確認します。`install`直前にも同じ検証を再実行するため、`prepare`後に管理パスが差し替えられた場合は起動しません。

## 6. LaunchAgentとして登録する

```bash
uv run wakou-macos-profile install --profile production
```

このコマンドは、現在のmacOSユーザーのLaunchAgentとして登録し、次の条件で起動します。

- `127.0.0.1:8100`だけで待受け
- `--workers 1`
- macOSユーザーのログイン時に自動起動
- 異常終了時に自動再起動
- 作業ディレクトリは`production`プロファイル
- SQLiteとログはプロファイル内へ保存

状態確認：

```bash
uv run wakou-macos-profile status --profile production
curl -fsS http://127.0.0.1:8100/api/health
```

正常時：

```text
launch_agent_loaded=true
health=true
{"status":"ok","service":"wakou-transfer"}
```

再登録中に新しいLaunchAgentの起動または専用ヘルスチェックが失敗した場合、CLIは以前のplistとジョブを復元して失敗終了します。登録解除時も`bootout`に失敗した場合はplistを残し、成功扱いにしません。

ログ：

```text
~/Library/Application Support/WakouCSV/profiles/production/logs/app.log
~/Library/Application Support/WakouCSV/profiles/production/logs/app-error.log
```

ログに秘密値や顧客情報を意図的に出力しないでください。

## 7. 複数PCからHTTPSで利用する

Mac miniと利用PCを同じTailscaleネットワークへ参加させます。TailscaleのCLIが利用できることを確認してから実行します。変更前の設定を記録し、ほかのTailscale Serve設定と衝突しないよう、外向けHTTPSポートにも専用の`8443`を割り当てます。

```bash
SERVE_STATUS_FILE="$HOME/Library/Application Support/WakouCSV/profiles/production/tailscale-serve-before.txt"
tailscale serve status | tee "$SERVE_STATUS_FILE"
if grep -Eq ':8443([[:space:]/]|$)' "$SERVE_STATUS_FILE"; then
  echo "ERROR: Tailscale Serveの8443は既存設定で使用中です。人間の確認前に変更しません。" >&2
  exit 1
fi
if lsof -nP -iTCP:8443 -sTCP:LISTEN; then
  echo "ERROR: TCP 8443は既存プロセスが使用中です。人間の確認前に変更しません。" >&2
  exit 1
fi
tailscale serve --bg --https=8443 http://127.0.0.1:8100
tailscale serve status
```

表示された`https://...ts.net:8443`のURLを担当者PCから開きます。Tailscaleのアクセス制御で、ワコウの担当者と許可端末だけに制限してください。既存サービスがすでに`8443`を使っている場合は、未使用の別ポートを選びます。

確認項目：

- 許可PCからHTTPS URLを開ける
- Basic認証が表示される
- 未許可端末からアクセスできない
- `http://Mac-miniのLAN-IP:8100`ではアクセスできない
- インターネットルーターでポート開放していない

Tailscale ServeのCLI仕様は更新される場合があります。コマンドが異なる場合は、公式ドキュメントの`tailscale serve`を確認し、転送先を`http://127.0.0.1:8100`から変更しないでください。

このツール用の公開設定を解除する場合は、現在のTailscale CLIが表示する解除コマンドを`tailscale serve --help`で確認し、`8443`の設定だけを解除します。`tailscale serve reset`は他サービスの設定も消すため使用しません。解除後は保存した`tailscale-serve-before.txt`と`tailscale serve status`を比較し、既存サービスが維持されていることを確認します。

## 8. 更新する

更新時はリポジトリで以下を実行します。

```bash
cd "$HOME/Services/csv_transfer_for_wakou"
git pull --ff-only
uv sync --extra dev
uv run pytest
uv run wakou-macos-profile install --profile production
uv run wakou-macos-profile status --profile production
```

`install`は同じlaunchdラベルを安全に再登録し、新しいコードで再起動します。`.env`、SQLite履歴、ログはソースコードとは別なので、`git pull`では変更されません。

## 9. バックアップする

最重要ファイル：

```text
~/Library/Application Support/WakouCSV/profiles/production/data/export-history.sqlite3
```

アプリ稼働中に単純コピーせず、SQLiteのバックアップ機能を使用します。

```bash
mkdir -p "$HOME/Backups/WakouCSV"
sqlite3 \
  "$HOME/Library/Application Support/WakouCSV/profiles/production/data/export-history.sqlite3" \
  ".backup '$HOME/Backups/WakouCSV/export-history-$(date +%Y%m%d).sqlite3'"
```

バックアップには個人情報を保存しませんが、重複出力防止に必要な業務データとしてアクセス制限してください。

復元はアプリを停止してから実施します。DB本体だけでなく既存のWAL／SHMも一括退避し、削除してからSQLiteのバックアップ機能で復元します。`YYYYMMDD`は使用するバックアップの日付へ置き換えてください。

```bash
cd "$HOME/Services/csv_transfer_for_wakou"
uv run wakou-macos-profile uninstall --profile production
PROFILE_DIR="$HOME/Library/Application Support/WakouCSV/profiles/production"
DB="$PROFILE_DIR/data/export-history.sqlite3"
BACKUP="$HOME/Backups/WakouCSV/export-history-YYYYMMDD.sqlite3"
SNAPSHOT_DIR="$PROFILE_DIR/data/pre-restore-$(date +%Y%m%d-%H%M%S)"
mkdir -m 700 "$SNAPSHOT_DIR"
for suffix in "" "-wal" "-shm"; do
  if [ -e "$DB$suffix" ]; then
    cp -p "$DB$suffix" "$SNAPSHOT_DIR/"
  fi
done
rm -f "$DB" "$DB-wal" "$DB-shm"
sqlite3 "$BACKUP" ".backup '$DB'"
chmod 600 "$DB"
test "$(sqlite3 "$DB" 'PRAGMA integrity_check;')" = "ok"
uv run wakou-macos-profile install --profile production
uv run wakou-macos-profile status --profile production
```

`integrity_check`が`ok`にならなければ再登録せず、復元を中止します。復元後に出力済み一覧の件数と注文番号検索を確認します。問題があれば再度登録解除し、`$DB`、`$DB-wal`、`$DB-shm`を削除してから、`$SNAPSHOT_DIR`内のファイルを`data`へ戻して再登録します。

CSVファイルには氏名、住所、電話番号が含まれます。担当者PCのダウンロードフォルダに放置せず、保存場所と削除期限を決めてください。

## 10. 停止・登録解除する

LaunchAgentだけを解除し、`.env`、履歴、ログを保持します。

```bash
uv run wakou-macos-profile uninstall --profile production
```

再登録：

```bash
uv run wakou-macos-profile install --profile production
```

プロファイルディレクトリを削除すると履歴も消えます。削除はバックアップ確認後、人間の明示承認を得てから行ってください。

## AIエージェント向け実行手順

別PCのAIエージェントには、このリポジトリと本ファイルを渡し、以下の順序で作業させます。

1. `system_profiler SPHardwareDataType`で対象がMac miniか確認する。
2. `git status`で既存作業を壊さないことを確認する。
3. `git pull --ff-only`と`uv sync --extra dev`を実行する。
4. `lsof`で候補ポートが未使用か確認する。
5. `wakou-macos-profile prepare`を実行する。
6. `.env`入力の直前で停止し、人間へ秘密入力を依頼する。
7. `.env`を表示せず`validate`を実行する。
8. 検証成功後に`install`を実行する。
9. `status`と`/api/health`を確認する。
10. Tailscale Serveをlocalhostのポートへ設定する。
11. 許可した別PCのブラウザからHTTPS接続を確認する。
12. Shopify API接続では、注文内容を出力せずHTTP状態、スコープ、件数だけを確認する。
13. 実注文1件のCSVを作り、ワコウ側システムへ試験取込みする。
14. Mac miniを再起動し、ユーザーログイン後の自動復旧を確認する。

AIエージェントが停止して人間へ確認すべき操作：

- `.env`への秘密値入力
- Tailscaleのログインとアクセス許可
- Mac miniの再起動
- ワコウ側システムへの本番データ取込み
- プロファイルまたはSQLite履歴の削除

## 完了判定

- `validate`が成功する
- LaunchAgentが対象ユーザー内に1つだけ存在する
- `status`が`launch_agent_loaded=true`、`health=true`を返す
- アプリが`127.0.0.1`だけで待ち受ける
- 単一workerで動作する
- TailscaleのHTTPS URLから許可PCだけがアクセスできる
- Shopifyスコープが`read_orders,read_products`だけである
- 出力履歴がプロファイル内SQLiteへ保存される
- Mac mini再起動後、ユーザーログインで自動復旧する
- バックアップと復元手順が確認済みである
- 佐川・ヤマト双方の試験取込みが完了している
