# 固定MCPサーバー連携 設計書

## 背景・目的

acrocity-rag-system側にKB(ナレッジベース)ごとのMCPサーバーが構築されており、bedrock-chatのbotからそのMCPサーバー経由でKB検索ツールを利用できるようにする。

利用イメージ:
- 管理者がbot作成時に、接続したいKBのMCPエンドポイント・認証情報を入力して設定する
- 一般利用者は、管理者が用意した既存botをそのまま使うだけで、裏側でMCP連携ツールが動く
- **[更新] bot 1つにつき MCP接続は0〜n個（1 bot = n KB）。1つのbotから複数KBのMCPサーバーへ同時接続できる**(初版では1 bot = 1 KBだったが、実運用要望により複数接続対応に変更)

## 認証方式（前提）

acrocity-rag-system側の指示書（`bedrock-chat-mcp-auth.md`）に基づく:

- Cognito User Poolの `client_credentials` grant（サーバー間認証）
- KBごとに別々の Client ID / Client Secret / MCPエンドポイントURL
- トークンエンドポイント: `https://knowledge-mcp-auth.auth.<region>.amazoncognito.com/oauth2/token`
- `scope=knowledge-mcp/invoke` は固定値。scopeの値自体はサーバー側で検証されない
- アクセストークンの有効期限は `expires_in`（デフォルト3600秒）。期限切れ前に再取得が必要
- MCPリクエストには `Authorization: Bearer <access_token>` ヘッダーを付与する

## アーキテクチャ概要

既存の `strands-agents` SDK（bedrock-chatが既に採用しているAIエージェントフレームワーク）はMCPクライアントをネイティブ対応しており、この機能を利用する。

チャット1ターン(`converse_with_strands`の1回の呼び出し)につき、以下の流れでMCP接続を行う:

1. botの設定にMCPツールが有効になっていれば、設定されている**各MCPサーバーごとに**Cognitoからアクセストークンを取得(Lambdaコンテナ内メモリキャッシュ、有効期限90秒前に再取得)
2. サーバーごとに `MCPClient` を該当エンドポイント・Bearerトークンでopen
3. サーバーごとに `list_tools_sync()` でツール一覧を取得し、ツール名にサーバーの `label` をprefixとして付与して一意化した上で結合する
4. 結合したツールリストをStrands Agentに渡してAgentを実行(`agent(strands_messages)`)
5. ターン終了時に、開いた `MCPClient` を全てclose

**サーバーごとに独立して縮退運転する**: 1つのMCPサーバーへの接続・トークン取得が失敗しても、そのサーバーのツールだけ利用不可になり、他のサーバーのツール・チャット自体は継続する。

MCP接続はチャット1ターンの間だけ開き、常時接続は行わない(Lambda実行モデルと自然に合致するため)。

## データモデル（backend）

`backend/app/repositories/models/custom_bot.py` に既存の `InternetToolModel` / `BedrockAgentToolModel` と同じパターンで追加する。

```python
class McpConfigModel(BaseModel):
    label: str                   # サーバー識別用ラベル。ツール名prefixにも使う(英数字+アンダースコアのみ)
    endpoint_url: str
    client_id: str
    client_secret: SecureString  # 保存時はSecrets Managerに格納、取得時は復元

class McpToolModel(BaseModel):
    tool_type: Literal["mcp"] = "mcp"
    name: str
    description: str
    mcpServers: list[McpConfigModel] = []
    secret_arn: str | None = None  # bot全体で1つ。JSON({label: client_secret, ...})を保存
```

- `ToolModel`(discriminated union: `PlainToolModel | InternetToolModel | BedrockAgentToolModel`)に `McpToolModel` を追加する
- シークレット保存は既存の `store_api_key_to_secret_manager`/`get_api_key_from_secret_manager`(Firecrawlと同じ汎用関数)を再利用しつつ、値は「サーバーlabel → client_secret」のJSONを1つのシークレットにまとめて保存する(bot全体で1つのシークレット)。これにより bot削除時のクリーンアップは既存の1回呼び出し(`delete_api_key_from_secret_manager(user_id, bot_id, "mcp")`)のまま変更不要
- ルートスキーマ側 `McpConfig` に `label: str` を追加し、`McpTool.mcpServers: list[McpConfig]` にラベル重複チェックのバリデーションを追加する

## トークン取得・キャッシュ（backend新規）

`backend/app/strands_integration/tools/mcp_tools.py` に実装。

```python
_token_cache: dict[str, tuple[str, float]] = {}  # key: f"{secret_arn}:{label}"

def get_mcp_bearer_token(client_id: str, client_secret: str, cognito_domain: str, cache_key: str) -> str:
    cached = _token_cache.get(cache_key)
    if cached and cached[1] > time.time() + 90:
        return cached[0]

    resp = requests.post(
        f"https://{cognito_domain}/oauth2/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials", "scope": "knowledge-mcp/invoke"},
        timeout=10,
    )
    resp.raise_for_status()
    body = resp.json()
    expires_at = time.time() + body["expires_in"]
    _token_cache[cache_key] = (body["access_token"], expires_at)
    return body["access_token"]
```

- キャッシュはLambdaコンテナ内メモリ(グローバル辞書)。コンテナ間共有はしない(コールドスタート時は再取得されるだけで許容範囲)
- `cache_key` は `f"{secret_arn}:{label}"`(1 bot内の複数サーバーがシークレットを共有するため、`label` も含めてサーバー単位にキャッシュを分離する)
- Cognitoドメイン・scopeは固定値としてコード内にハードコードする

## Strands統合（複数MCPClientのライフサイクル・ツール名の一意化）

`mcp_tools.py`(方針。詳細実装は実装計画で詰める):

- `mcp_tools_scope(bot)` は bot の `mcpServers` を1件ずつ処理し、`contextlib.ExitStack` で複数の `MCPClient` 接続をまとめて管理する
- サーバーごとに: トークン取得 → `MCPClient` を該当エンドポイントでopen → `list_tools_sync()` → 取得したツール名の先頭に `{label}_` を付与 → 結合リストに追加
- 1サーバーの処理で例外が起きても `except Exception` でその1件だけログ出力してスキップし、他サーバーの処理は継続する
- ツール名の書き換え方法(strands SDKの `MCPAgentTool` が名前変更可能な構造か、ラッパーが必要か)は実装時にSDKを直接確認して決定する
- ターン終了時、`ExitStack` が開いた接続を全てclose(close失敗時も既存のtry/exceptガードでチャット継続を保証)

`backend/app/strands_integration/chat_strands.py` の `converse_with_strands`、`agent/factory.py` の `create_strands_agent(extra_tools=...)` への配線は既存(1サーバー版)と同じ形を維持する(`mcp_tools_scope` が返すツールリストが複数サーバー分の結合済みリストになるだけで、呼び出し側のインターフェースは変わらない)。

## フロントエンド（bot作成/編集画面）

- `McpConfig`(型)に `label: string` を追加、`McpAgentTool.mcpConfig?: McpConfig`(単数)を `McpAgentTool.mcpServers: McpConfig[]`(配列)に変更
- 新規 `McpServersConfig.tsx`: `McpConfig.tsx`(1件分の入力フォーム、`label`欄を追加)をリスト表示し、各行に削除ボタン、リスト末尾に追加ボタンを持つラッパーコンポーネント
- 保存前バリデーション(`BotKbEditPage.tsx`)に、配列内の各サーバーの必須項目チェックとラベル重複チェックを追加

## エラーハンドリング

- サーバーごとの独立縮退運転(前述)。1台失敗しても他は継続、全滅時はMCPツール0件で応答継続
- ラベル重複・必須項目の空値は保存時のバリデーションで弾くため、ランタイムでは発生しない想定
- UI上にMCP接続失敗を明示する通知は本設計では行わない(既存のtool失敗時と同様、サイレント)

## テスト方針

- `get_mcp_bearer_token` はHTTPリクエストをモックした単体テストでキャッシュ・再取得ロジックを検証する(既存)
- 複数サーバー設定でのシークレット保存/復元(JSON往復)の単体テストを追加
- `mcp_tools_scope` は、bot設定なし/MCP設定なし/全サーバー接続失敗/一部サーバーのみ失敗(残りのサーバーのツールのみ返る)をそれぞれ単体テストで検証する
- 実際のMCPサーバーへの疎通を伴う結合テストは、引き続き自動テスト化せず手動疎通確認とする
- フロントエンドのリストUIはコンポーネント単体テストなし(既存の踏襲)

## スコープ外

- 管理者向けのMCPサーバー登録・一覧管理画面(botごとに手入力する形を維持)
- MCP接続失敗のUI通知
- サーバー数の上限設定
