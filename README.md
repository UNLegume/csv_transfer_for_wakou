# csv_transfer_for_wakou

Shopify Admin GraphQL APIから注文を読み取り、株式会社ワコウ指定形式の佐川・ヤマト（ネコポス）送り状CSVを生成する社内向けWebツールです。

## 対象範囲

- Shopifyから決済済み・未発送・未キャンセル注文を読み取る
- 商品／バリエーションのメタフィールドから配送会社を判定する
- 注文単位で佐川／ヤマトへ集約する
- 宛先、郵便番号、電話番号、文字数、CP932互換性を検証する
- ワコウ指定の佐川50列、ヤマト42列CSVをCP932・CRLFで生成する
- 注文ID、注文番号、配送会社、生成日時だけを履歴保存して重複出力を防ぐ

配送会社への直接送信、送り状印刷、発送処理、追跡番号取得、Shopifyへの発送実績反映は対象外です。

## 実運用前の重要事項

`.env.example` の送り状固定値は提供されたサンプルの値です。請求コード、依頼主コード、便種、代引き種別、オプション文言、請求先分類コード、運賃管理番号などは、株式会社ワコウの正式回答を得てから本番値に設定してください。未確認のまま本番出荷には使用しないでください。

## 必要環境

- Python 3.12以上
- [uv](https://docs.astral.sh/uv/)
- Shopifyカスタムアプリ

Shopifyカスタムアプリに必要な基本スコープは次のとおりです。

- `read_orders`
- `read_products`

60日より古い注文を取得する場合はShopifyによる `read_all_orders` の追加承認が必要です。本ツールはShopifyへ書き込みません。

## セットアップ

```bash
git clone https://github.com/UNLegume/csv_transfer_for_wakou.git
cd csv_transfer_for_wakou
uv sync --extra dev
cp .env.example .env
```

`.env` にShopify接続情報、発送元、ワコウ指定の固定値を設定します。`.env` はGit管理されません。

操作画面とCSV出力APIはHTTP Basic認証で保護されます。`WAKOU_AUTH_USERNAME`と`WAKOU_AUTH_PASSWORD`には推測されにくい値を設定してください。

```env
SHOPIFY_STORE_DOMAIN=your-store.myshopify.com
SHOPIFY_ADMIN_ACCESS_TOKEN=shpat_...
SHOPIFY_API_VERSION=2026-07
```

その他の設定項目は `.env.example` を参照してください。

## 商品の配送会社設定

Shopifyの商品またはバリエーションに次のメタフィールドを登録します。バリエーション側の値が商品側より優先されます。

| namespace | key | 型 | 値 |
|---|---|---|---|
| `delivery` | `carrier` | 1行のテキスト | `yamato` または `sagawa` |
| `delivery` | `yamato_max_quantity` | 整数 | 任意。ネコポスで出荷できる商品単位の最大数量 |

判定ルール：

1. 配送会社未設定の商品を含む注文は「要確認」
2. 佐川商品を1点でも含む注文は佐川
3. ヤマト商品のみでも商品別または全体数量上限を超えた場合は佐川
4. それ以外はヤマト
5. 画面で変更する場合は変更理由が必須

詳しい設定手順は `docs/shopify-setup.md` を参照してください。

## 起動

```bash
uv run uvicorn wakou_transfer.app:create_app --factory --host 127.0.0.1 --port 8000
```

ブラウザで `http://127.0.0.1:8000` を開きます。

1. 注文日の開始・終了と出荷予定日を入力
2. 「Shopifyから注文取得」を押す
3. 配送会社、住所検証、出力済み状態を確認
4. 出力する注文を選択
5. ヤマトCSVまたは佐川CSVをダウンロード

配送会社を変更した場合は変更理由が必要です。出力済み注文を再出力する場合も再出力理由が必要です。

## CSV仕様

| 項目 | 佐川 | ヤマト／ネコポス |
|---|---:|---:|
| 列数 | 50 | 42 |
| 文字コード | CP932 | CP932 |
| 改行 | CRLF | CRLF |
| 見出し | あり | あり |
| 行単位 | 1注文1行 | 1注文1行 |

電話番号、郵便番号、請求コード、依頼主コードなどは文字列として扱い、先頭ゼロを保持します。

## セキュリティとデータ保持

- Shopifyトークンは環境変数で管理し、リポジトリやログへ出力しない
- APIは読取専用スコープだけを使用する
- プレビュー中の注文情報はプロセス内メモリだけに保持する
- SQLite履歴には氏名、住所、電話番号を保存しない
- CSV自体には個人情報が含まれるため、安全な場所へ保存して不要になったら削除する

## 開発

```bash
uv run pytest
uv run ruff check .
uv run mypy src tests
```

テストはShopify GraphQLのページネーション・エラー、配送判定、宛先検証、CSV列契約、CP932／CRLF、Web API、重複防止を対象にしています。

## 構成

- `src/wakou_transfer/shopify.py` — Shopify GraphQL読取クライアント
- `src/wakou_transfer/routing.py` — 注文単位の配送会社判定
- `src/wakou_transfer/validation.py` — 宛先の正規化・検証
- `src/wakou_transfer/csv_export.py` — 佐川・ヤマトCSV生成
- `src/wakou_transfer/history.py` — 個人情報を持たない出力履歴
- `src/wakou_transfer/app.py` — Web画面とAPI
