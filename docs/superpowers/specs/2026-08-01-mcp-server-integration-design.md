# 固定MCPサーバー連携 設計書

## 背景・目的

acrocity-rag-system側にKB(ナレッジベース)ごとのMCPサーバーが構築されており、bedrock-chatのbotからそのMCPサーバー経由でKB検索ツールを利用できるようにする。

利用イメージ:
- 管理者がbot作成時に、接続したいKBのMCPエンドポイント・認証情報を入力して設定する
- 一般利用者は、管理者が用意した既存botをそのまま使うだけで、裏側でMCP連携ツールが動く
- bot 1つにつき MCP接続は1つ（1 bot = 1 KB）。複数KB同時接続は本設計のスコープ外

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

1. botの設定にMCPツールが有効になっていれば、Cognitoからアクセストークンを取得(Lambdaコンテナ内メモリキャッシュ、有効期限90秒前に再取得)
2. `MCPClient` を該当エンドポイント・Bearerトークンでopen
3. `list_tools_sync()` でMCPサーバーが提供するツール一覧を取得し、Strands Agentのツールリストに追加
4. Agentを実行(`agent(strands_messages)`)
5. ターン終了時に `MCPClient` をclose

MCP接続はチャット1ターンの間だけ開き、常時接続は行わない(Lambda実行モデルと自然に合致するため)。

## データモデル（backend）

`backend/app/repositories/models/custom_bot.py` に既存の `InternetToolModel` / `BedrockAgentToolModel` と同じパターンで追加する。

```python
class McpConfigModel(BaseModel):
    endpoint_url: str
    client_id: str
    secret_arn: str              # Secrets Manager上のARN
    client_secret: SecureString  # 保存時はSecrets Managerに格納、取得時はARNから復元

class McpToolModel(BaseModel):
    tool_type: Literal["mcp"] = "mcp"
    name: str
    description: str
    mcpConfig: Optional[McpConfigModel] = None
```

- `ToolModel`(discriminated union: `PlainToolModel | InternetToolModel | BedrockAgentToolModel`)に `McpToolModel` を追加する
- `client_secret` の保存・復元は既存の `FirecrawlConfigModel`(`store_api_key_to_secret_manager` / `get_api_key_from_secret_manager` / `load_secret_from_arn` validator)と全く同じパターンを踏襲する

## トークン取得・キャッシュ（backend新規）

`backend/app/strands_integration/tools/mcp_tools.py` に実装。

```python
_token_cache: dict[str, tuple[str, float]] = {}  # key: secret_arn

def get_mcp_bearer_token(client_id: str, client_secret: str, cognito_domain: str, cache_key: str) -> str:
    cached = _token_cache.get(cache_key)
    if cached and cached[1] > time.time() + 90:
        return cached[0]

    resp = requests.post(
        f"https://{cognito_domain}/oauth2/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials", "scope": "knowledge-mcp/invoke"},
    )
    resp.raise_for_status()
    body = resp.json()
    expires_at = time.time() + body["expires_in"]
    _token_cache[cache_key] = (body["access_token"], expires_at)
    return body["access_token"]
```

- キャッシュはLambdaコンテナ内メモリ(グローバル辞書)。コンテナ間共有はしない(コールドスタート時は再取得されるだけで許容範囲)
- `cache_key` は bot の `secret_arn`(KBごとに異なるためKB単位でキャッシュが分離される)
- Cognitoドメイン・scopeは固定値としてコード内にハードコードする

## Strands統合（MCPClientのライフサイクル）

`mcp_tools.py`:

```python
@contextmanager
def mcp_tools_scope(bot: BotModel | None):
    """botにMCP設定があれば接続してtools一覧をyield、なければ空リストをyield。
    接続失敗時はログのみ出力し空リストにフォールバックする(縮退運転)。"""
    config = _get_mcp_tool_config(bot)
    if not config:
        yield []
        return

    try:
        token = get_mcp_bearer_token(
            config.client_id, config.client_secret, COGNITO_DOMAIN, config.secret_arn
        )
        client = MCPClient(lambda: streamablehttp_client(
            config.endpoint_url,
            headers={"Authorization": f"Bearer {token}"},
        ))
        with client:
            yield client.list_tools_sync()
    except Exception as e:
        logger.error(f"MCP connection failed, falling back without MCP tools: {e}")
        yield []
```

`backend/app/strands_integration/chat_strands.py` の `converse_with_strands`:

```python
with mcp_tools_scope(bot) as mcp_tools:
    agent = create_strands_agent(..., extra_tools=mcp_tools, ...)
    agent.callback_handler = create_callback_handler(...)
    stop_reason, result_message, metrics = run_agent(agent)
```

`backend/app/strands_integration/agent/factory.py` の `create_strands_agent()`:
- 新規引数 `extra_tools: list[StrandsAgentTool] | None = None` を追加
- `tools=get_strands_tools(bot, model_name) + (extra_tools or [])` としてAgentに渡す

## フロントエンド（bot作成/編集画面）

`frontend/src/features/agent/types/index.d.ts`:

```typescript
export type ToolType = 'internet' | 'plain' | 'bedrock_agent' | 'mcp';

export type McpConfig = {
  endpointUrl: string;
  clientId: string;
  clientSecret: string;  // 保存時のみ送信。取得時は空文字(平文は再表示しない)
};

export type McpAgentTool = {
  toolType: 'mcp';
  name: string;
  description: string;
  mcpConfig?: McpConfig;
};

export type AgentTool = InternetAgentTool | PlainAgentTool | BedrockAgentTool | McpAgentTool;
```

- 新規 `McpConfig.tsx`(`BedrockAgentConfig.tsx` と同じ構造): `endpointUrl` / `clientId` / `clientSecret` の3つの `InputText`(`clientSecret` はマスク表示)
- `AvailableTools.tsx` の `handleChangeTool` 等に `bedrock_agent` と同じ分岐を `mcp` 用に追加

## エラーハンドリング

- MCP接続(トークン取得・`list_tools_sync()`・接続確立)が失敗した場合は、既存のFirecrawl失敗時のフォールバック(DuckDuckGoへ切替)と同じ考え方で **縮退運転** する
- `mcp_tools_scope` 内で例外を捕捉しログ出力、空リストをyieldしてチャット自体は継続する
- UI上にMCP接続失敗を明示する通知は本設計では行わない(既存のtool失敗時と同様、サイレント)

## テスト方針

- `get_mcp_bearer_token` はHTTPリクエストをモックした単体テストでキャッシュ・再取得ロジックを検証する
- `mcp_tools_scope` は、bot設定なし/MCP設定なし/接続失敗時にそれぞれ空リストへフォールバックすることを単体テストで検証する
- 実際のMCPサーバーへの疎通を伴う結合テスト(`test_bedrock_agent.py` と同様の実AWSリソースを使うテスト)は、acrocity-rag-system側の実デプロイ環境が必要なため本設計のスコープでは自動テスト化せず、手動疎通確認とする

## スコープ外

- bot単位で複数KB(複数MCPサーバー)への同時接続
- 管理者向けのMCPサーバー登録・一覧管理画面
- MCP接続失敗のUI通知
