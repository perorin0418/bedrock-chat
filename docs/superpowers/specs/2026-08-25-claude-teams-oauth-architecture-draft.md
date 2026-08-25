# Claude Teams OAuthトークン機能 アーキテクチャ案

作成日: 2026-08-25
状態: 確定（実装計画作成待ち）

## 背景

Claude Teamsプランの定額枠を、複数のCLAUDE_CODE_OAUTH_TOKENを組織で登録し、
チャット時にユーザーが専用モデルとして明示的に選択できるようにする機能。

なりすまし（Anthropic ToS上のグレーゾーン）を避けるため、strandsのAnthropicModelで
独自にシステムプロンプトを組み立てて直接API/SDKを呼ぶ方式ではなく、
`claude-agent-sdk`（Claude Code CLI バイナリをサブプロセスとして実際に起動する
公式Python SDK）を使って本物のClaude Code CLIを実行する方式を採用する。

## 1. トークン管理（Admin API + DynamoDB + Secrets Manager）

- `ClaudeTeamsTokenTable`（新規DynamoDB）
  - token_id, display_name, enabled, cooldown_until(TTL), created_at, last_used_at,
    ラウンドロビン用カーソル
- Secrets Manager: `claude-teams-token/{token_id}` にトークン本体を保存
- Admin API: `POST/GET/PATCH/DELETE /admin/claude-teams-tokens`
  - 登録: 表示名 + トークン文字列
  - 一覧: 表示名 / 有効・無効 / クールダウン状態 / 最終使用日時（トークン本体は非表示・再表示不可）
  - 編集: 有効・無効の切り替え、表示名変更、削除のみ（トークン文字列の更新は削除→再登録で対応）

## 2. モデル定義追加

- `type_model_name` に4つ追加:
  - `claude-teams-opus`
  - `claude-teams-sonnet`
  - `claude-teams-haiku`（既存最新のclaude-v4.5-haiku相当）
  - `claude-teams-fable`
- フロントエンド `useModel.ts` に対応する `ModelItem` を追加
  （ラベルに「Teamsプラン経由」等を明示し、ユーザーが選択可能にする）
- 料金表示は常に `$0.00` 固定（定額枠内のため追加課金なし）
- **調査確定事項**: Anthropic公式APIのネイティブ `model` 値（Bedrock ARNではない）は
  以下の通り。既存の `BASE_MODEL_IDS` のBedrock版サフィックス（`anthropic.`除去後）と
  完全一致するため、新規マッピング辞書（例: `CLAUDE_TEAMS_MODEL_IDS`）は機械的に導出できる:
  - `claude-teams-opus` → `claude-opus-5`
  - `claude-teams-sonnet` → `claude-sonnet-5`
  - `claude-teams-haiku` → `claude-haiku-4-5-20251001`
  - `claude-teams-fable` → `claude-fable-5`
  （方針B: bedrock-chat側で明示的なフルmodel-id文字列を固定管理し、
  Claude Codeのモデルエイリアス自動解決には依存しない）

## 3. トークン選択・クールダウン制御

- ラウンドロビン方式で、enabled かつ cooldown 切れのトークンを順に選択
- Anthropic APIから429/レート制限相当のエラーを検知したら、そのトークンの
  `cooldown_until` をDynamoDBに書き込み（TTLで自動復帰）、次のトークンにフォールバック
- 全トークンがクールダウン中/未登録の場合はユーザーにエラーメッセージを表示する
  （他モデルへの自動フォールバックは行わない）

## 4. 実行エンジン（新規アダプタ）

- 実行方式: **`claude-agent-sdk`（Python）を使用する**
  - 内部的には同じ `claude` CLIバイナリを `--input-format stream-json
    --output-format stream-json` 相当のモードでサブプロセス起動し、標準入出力を
    JSON Linesでやり取りするラッパー。「本物のClaude Code CLIを実際に動かす」という
    原則を保ったまま、ストリーミングイベントの取得やインプロセスMCPツール登録
    （`create_sdk_mcp_server` / `@tool`）、ツール許可・禁止制御が可能になる
  - 素朴な `claude -p` 直接サブプロセス実行（テキスト出力のみ）は採用しない
    （ストリーミング表示・bot個別ツール連携ができないため不採用）
- `usecases/chat.py` の `chat()` 内で、選択モデルが `claude-teams-*` の場合、
  既存のstrands/legacy分岐と並列の第3ルートに分岐する
- 選ばれたトークンを `CLAUDE_CODE_OAUTH_TOKEN` 環境変数としてサブプロセスに渡す
  （`ClaudeAgentOptions.env` 経由）
- ツール制御: ローカルファイル操作系ツール（Bash/Read/Write/Edit/Glob/Grep等）は
  `disallowed_tools` で禁止する。bot設定のMCPサーバー・内蔵ツール（ナレッジ検索等）は
  許可する形で連携する（内蔵ツールは `create_sdk_mcp_server` でインプロセスMCP化）
- **外部MCPサーバー（Bot設定、OAuth方式含む）の橋渡し方式（確定: 案B）**:
  - `claude-agent-sdk` の `ClaudeAgentOptions.mcp_servers` はサーバー起動設定の辞書
    （stdio/SSE/HTTP接続情報）を渡す形式で、strandsの `MCPClient` のように
    Python側の `httpx.Auth` オブジェクトを直接差し込む口がない
  - そのため、外部MCPサーバーへの接続は既存の `mcp_tools_scope`
    （`app/strands_integration/tools/mcp_tools.py`）をそのまま再利用し、
    bedrock-chatのLambdaプロセス内でstrandsの `MCPClient` として一旦接続する
  - 得られた各ツール（`list_tools_sync()`の結果）を `create_sdk_mcp_server` /
    `@tool` でラップし、インプロセスMCPサーバーとしてCLIサブプロセスに渡す
  - OAuth方式（`OAuthClientProvider`）の動的トークン取得・リフレッシュは
    既存ロジックのまま活きる（CLIサブプロセス側での再実装は不要）
- 会話履歴: bedrock-chatの `SimpleMessageModel` 一覧から毎回プロンプトを構築する
  （CLIセッションは使い捨て、`resume`/`session_store` は使わない）
- ストリーミング: SDKから得られる `Message`/`StreamEvent` を既存の
  `on_stream`/`on_thinking`/`on_tool_result`/`on_reasoning` コールバックに変換する
- 使用トークン数: `ResultMessage` の `usage` から取得。`price` は常に `0.0` 固定
- **使用量記録（確定: 案B）**: 既存の `usage_ledger`（`app/repositories/usage_limit.py`
  の `record_usage`）には通常のBedrockチャットと同様に記録する。`price=0.0` で記録するため、
  `check_rate_limit`（5時間/7日USD上限）には一切カウントされず影響しない。将来的な
  利用状況の集計・可視化のために記録だけは残す

## 5. インフラ（CDK, Lambda同梱）

- **調査確定事項**: `claude-agent-sdk`（v0.1.50以降）はプラットフォーム固有の
  ネイティブCLIバイナリ（Node.js/Inkアプリではない）をpipパッケージ（wheel）に
  同梱しており、Node.jsランタイムの別途同梱は不要
- 既存 `HandlerV2`（Lambda, Python 3.13コンテナ, Lambda Web Adapter, x86_64）の
  Python依存に `claude-agent-sdk` を追加するだけでよい（Dockerfile変更は最小限）
- Lambdaの `/tmp` 領域（512MB〜最大10GB設定可）に一時ディレクトリを作成することになるため、
  現状のメモリ/一時ストレージ設定を確認し、必要なら `ephemeralStorageSize` を調整する

## 6. bot指示（instructions）の渡し方: CLAUDE.md方式

- Lambda実行ごとに一時ディレクトリ（例: `/tmp/claude-teams-{request_id}/`）を新規作成する
- bot の instructions を結合し、そのディレクトリに `CLAUDE.md` として書き込む
- `ClaudeAgentOptions.cwd` をこの一時ディレクトリに設定して `claude` CLIを実行する
  → Claude Codeの標準機能により、起動時に `CLAUDE.md` が自動的にコンテキストへ
    読み込まれる（なりすましではない正規の記憶機能）
- Lambdaのウォームスタート（コンテナ再利用）で前リクエストの内容が漏れ残らないよう、
  `CLAUDE_CONFIG_DIR` もリクエストごとに一時ディレクトリ配下に新規発行する
  （`.mcp.json` / `.claude/settings.json` 等の自動読込スコープも同じ一時ディレクトリに
  閉じ込める）
- 実行後（成功・失敗・例外いずれの場合も）に一時ディレクトリを削除するクリーンアップを
  必ず行う
- bot が instructions を持たない場合は `CLAUDE.md` を作成しない（Claude Code標準の
  挙動のみ）

## 7. エラーハンドリング（確定: 案A）

`claude-agent-sdk` から得られる `system/api_retry` イベントの `error` フィールド
（`authentication_failed`, `oauth_org_not_allowed`, `billing_error`, `rate_limit`,
`overloaded`, `invalid_request`, `model_not_found`, `server_error`,
`max_output_tokens`, `unknown`）および `ResultMessage.subtype` を基に、以下のように
種別ごとに扱いを分ける。

- **`rate_limit` / `billing_error`**: そのトークンをクールダウン（`cooldown_until`を
  DynamoDBに書き込み、TTLで自動復帰）し、次のトークンにラウンドロビンでフォールバックする
- **`authentication_failed` / `oauth_org_not_allowed`**: トークン自体が無効・失効している
  と判断し、DynamoDB上で該当トークンを `enabled=false` に自動更新する（クールダウンではなく
  恒久停止。管理者がAdmin画面で気付いて対処する想定）。次のトークンにフォールバックする
- **その他（`overloaded`, `server_error`, `invalid_request`, `model_not_found`,
  `max_output_tokens`, `unknown`、CLIプロセスクラッシュ・タイムアウト等）**:
  トークンのクールダウン/無効化は行わず、そのターンのみユーザーにエラーメッセージを表示する
  （フォールバック・リトライはしない）
- 全トークンがクールダウン中または無効化済みで候補が尽きた場合は、ユーザーに
  「Teamsプランの利用上限に達しています。しばらく待つか、他のモデルをお試しください」等の
  エラーメッセージを表示する（3章の既定方針の再掲）
