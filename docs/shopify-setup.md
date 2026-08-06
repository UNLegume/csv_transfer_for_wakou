# Shopify Admin API・配送メタフィールド設定

## API 接続

Shopify Dev Dashboardでアプリを作成し、Admin APIの `read_orders` と
`read_products` スコープだけを付与したバージョンをリリースしてストアへインストールします。
この連携は注文・商品を読み取るだけで、更新系Mutationは使用しません。Client Credentials
Grantを使うため、次の環境変数を実行環境に設定してください。

- `SHOPIFY_STORE_DOMAIN`: `example.myshopify.com` 形式のストアドメイン
- `SHOPIFY_CLIENT_ID`: Dev Dashboardのアプリ設定に表示されるClient ID
- `SHOPIFY_CLIENT_SECRET`: Dev Dashboardのアプリ設定に表示されるSecret
- `SHOPIFY_API_VERSION`: 任意。未指定時は `2026-07`

Client Secretをリポジトリやログへ記録しないでください。アプリは注文取得時に24時間有効な
アクセストークンを自動取得します。

## 配送会社メタフィールド

管理画面の「設定」→「カスタムデータ」で、商品とバリエーションの両方に次の定義を作成します。

| 用途 | namespace / key | Shopify 型 | 許容値 |
| --- | --- | --- | --- |
| 配送会社 | `delivery.carrier` | 1行のテキスト | `yamato` または `sagawa` |
| ネコポス等の最大数量（任意） | `delivery.yamato_max_quantity` | 整数 | 1以上の整数 |

各商品またはバリエーションの編集画面で値を設定します。配送会社は商品名から推測しません。
バリエーション側の値が設定されていれば商品側より優先し、バリエーション側が未設定の場合だけ
商品側を使用します。配送会社が未設定なら未設定のまま、不正な値なら `needs_review` として扱い、
いずれも自動的にヤマトまたは佐川へ振り分けません。最大数量は将来の数量判定用の任意項目です。

Shopify の標準注文エクスポート CSV には商品・バリエーションのメタフィールドが含まれません。
そのため、本アプリは Admin GraphQL API で注文明細とメタフィールドを同時に取得します。
