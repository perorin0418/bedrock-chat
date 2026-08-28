# Claude Teams OAuthトークン機能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 組織で複数登録したClaude Teamsプラン用OAuthトークン（`CLAUDE_CODE_OAUTH_TOKEN`）を使い、Anthropic Claude Code CLI（`claude-agent-sdk`経由）を通じてチャットを実行する専用モデル（`claude-teams-opus/sonnet/haiku/fable`）をユーザーがモデル選択画面から選べるようにする。

**Architecture:** 既存の`chat()`（`usecases/chat.py`）にstrands/legacyと並列の第3実行ルートを追加し、`claude-agent-sdk`（Claude Code CLIバイナリをサブプロセス実行する公式Python SDK）でチャットを処理する。トークンは新規DynamoDBテーブル（メタデータ）+ Secrets Manager（トークン本体）で管理し、ラウンドロビン選択とクールダウン/無効化によるフォールバックを行う。bot固有ツール（内蔵ツール・外部MCP）はstrandsの既存接続を使い、`create_sdk_mcp_server`でインプロセスMCP化してCLIサブプロセスに橋渡しする。bot指示は一時ディレクトリに`CLAUDE.md`として書き込み、Claude Code標準の記憶機能で読み込ませる。

**Tech Stack:** Python 3.13 (FastAPI, boto3, pydantic), `claude-agent-sdk` (新規依存), AWS Lambda (既存`HandlerV2`, Lambda Web Adapter), DynamoDB, Secrets Manager, React + TypeScript (フロントエンド), AWS CDK (TypeScript)

## Global Constraints

- なりすまし回避のため、Claude Code純正のCLIバイナリを`claude-agent-sdk`経由で実際に起動する。独自にシステムプロンプトを組み立ててAnthropic APIへ直接叩く実装は行わない。
- Anthropic公式APIのネイティブ`model`値: `claude-teams-opus`→`claude-opus-5`, `claude-teams-sonnet`→`claude-sonnet-5`, `claude-teams-haiku`→`claude-haiku-4-5-20251001`, `claude-teams-fable`→`claude-fable-5`。
- ローカルファイル操作系ツール（Bash/Read/Write/Edit/Glob/Grep/WebFetch/WebSearch）はCLIサブプロセスで必ず`disallowed_tools`により禁止する。
- Teams経由チャットの`price`は常に`0.0`固定。`usage_ledger`には記録するが、`check_rate_limit`（5時間/7日USD上限）には影響しない。
- 全トークンがクールダウン中/無効化済みで候補が尽きた場合は他モデルへの自動フォールバックをせず、ユーザーにエラー表示する。
- Secrets Managerに保存したトークン本体はAdmin API・UIから再表示不可（書き込み専用）。
- 参照設計書: `docs/superpowers/specs/2026-08-25-claude-teams-oauth-architecture-draft.md`

---

## Task 1: バックエンド依存関係に `claude-agent-sdk` を追加

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/poetry.lock` (poetry lockが自動更新)

**Interfaces:**
- Produces: `claude_agent_sdk` パッケージがPython環境で import 可能になる

- [ ] **Step 1: pyproject.tomlに依存を追加**

`backend/pyproject.toml`の`[tool.poetry.dependencies]`セクションに追記:

```toml
claude-agent-sdk = "^0.1.50"
```

- [ ] **Step 2: poetry lockを更新**

```bash
cd backend
poetry lock --no-update
```

Expected: `poetry.lock`が更新され、`claude-agent-sdk`とその依存(`anyio`, `mcp`, `typing_extensions`等)が解決される。

- [ ] **Step 3: インストールしてimportを確認**

```bash
cd backend
poetry install
poetry run python -c "import claude_agent_sdk; print(claude_agent_sdk.__version__)"
```

Expected: バージョン文字列が出力される（エラーなし）。

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml backend/poetry.lock
git commit -m "chore: add claude-agent-sdk dependency"
```

---

## Task 2: `type_model_name` に Teams経由モデル4種を追加

**Files:**
- Modify: `backend/app/routes/schemas/conversation.py:8-48`
- Test: `backend/tests/test_repositories/test_models/test_bot.py` (既存テストが壊れないことを確認)

**Interfaces:**
- Produces: `type_model_name` Literalに `"claude-teams-opus"`, `"claude-teams-sonnet"`, `"claude-teams-haiku"`, `"claude-teams-fable"` が追加される。これにより`ActiveModelsModel`（`app/repositories/models/custom_bot.py`）や`ActiveModelsInput/Output`（`app/routes/schemas/bot.py`）にも自動的に対応フィールドが生える（`get_args(type_model_name)`から動的生成されるため、コード変更不要）。

- [ ] **Step 1: type_model_nameに追記**

`backend/app/routes/schemas/conversation.py`の8-48行目を編集:

```python
type_model_name = Literal[
    "claude-v4-opus",
    "claude-v4.1-opus",
    "claude-v4.5-opus",
    "claude-v4.6-opus",
    "claude-v4.7-opus",
    "claude-v5-opus",
    "claude-v4-sonnet",
    "claude-v4.5-sonnet",
    "claude-v4.6-sonnet",
    "claude-v5-sonnet",
    "claude-v5-fable",
    "claude-v4.5-haiku",
    "claude-v3.5-sonnet",
    "claude-v3.5-sonnet-v2",
    "claude-v3.7-sonnet",
    "claude-v3.5-haiku",
    "claude-v3-haiku",
    "claude-v3-opus",
    # Mistral
    "mistral-7b-instruct",
    "mixtral-8x7b-instruct",
    "mistral-large",
    "mistral-large-2",
    # New Amazon Nova models
    "amazon-nova-pro",
    "amazon-nova-lite",
    "amazon-nova-micro",
    # DeepSeek models
    "deepseek-r1",
    # Meta Llama 3 models
    "llama3-3-70b-instruct",
    "llama3-2-1b-instruct",
    "llama3-2-3b-instruct",
    "llama3-2-11b-instruct",
    "llama3-2-90b-instruct",
    "gpt-oss-20b",
    "gpt-oss-120b",
    # xAI models
    "grok-4.6",
    # Claude Teams plan (via Claude Code CLI OAuth token, flat-rate quota)
    "claude-teams-opus",
    "claude-teams-sonnet",
    "claude-teams-haiku",
    "claude-teams-fable",
]
```

- [ ] **Step 2: 既存テストが壊れていないことを確認**

```bash
cd backend
poetry run pytest tests/test_repositories/test_models/test_bot.py -v
```

Expected: 全テストPASS（`ActiveModelsModel`は`get_args(type_model_name)`から動的生成されるため、新モデル追加で既存アサーションが崩れないことを確認する）。

- [ ] **Step 3: Commit**

```bash
git add backend/app/routes/schemas/conversation.py
git commit -m "feat: add claude-teams-* model names to type_model_name"
```

---

## Task 3: Anthropicネイティブmodel-idマッピングと判定ヘルパーを追加

**Files:**
- Create: `backend/app/claude_teams/__init__.py`
- Create: `backend/app/claude_teams/models.py`
- Test: `backend/tests/test_claude_teams/__init__.py`
- Test: `backend/tests/test_claude_teams/test_models.py`

**Interfaces:**
- Produces:
  - `CLAUDE_TEAMS_MODEL_IDS: dict[str, str]` — bedrock-chatモデルID → Anthropicネイティブmodel-id
  - `is_claude_teams_model(model: str) -> bool`
  - `get_claude_teams_native_model_id(model: str) -> str` (未知のモデルなら`ValueError`)

- [ ] **Step 1: テストディレクトリの初期化ファイルを作成**

`backend/tests/test_claude_teams/__init__.py`:

```python
```

- [ ] **Step 2: 失敗するテストを書く**

`backend/tests/test_claude_teams/test_models.py`:

```python
import sys
import unittest

sys.path.insert(0, ".")
from app.claude_teams.models import (
    CLAUDE_TEAMS_MODEL_IDS,
    get_claude_teams_native_model_id,
    is_claude_teams_model,
)


class TestClaudeTeamsModels(unittest.TestCase):
    def test_mapping_contains_all_four_models(self):
        self.assertEqual(
            CLAUDE_TEAMS_MODEL_IDS,
            {
                "claude-teams-opus": "claude-opus-5",
                "claude-teams-sonnet": "claude-sonnet-5",
                "claude-teams-haiku": "claude-haiku-4-5-20251001",
                "claude-teams-fable": "claude-fable-5",
            },
        )

    def test_is_claude_teams_model_true_for_teams_models(self):
        self.assertTrue(is_claude_teams_model("claude-teams-opus"))
        self.assertTrue(is_claude_teams_model("claude-teams-fable"))

    def test_is_claude_teams_model_false_for_other_models(self):
        self.assertFalse(is_claude_teams_model("claude-v5-opus"))
        self.assertFalse(is_claude_teams_model("amazon-nova-lite"))

    def test_get_claude_teams_native_model_id_returns_mapped_value(self):
        self.assertEqual(
            get_claude_teams_native_model_id("claude-teams-sonnet"), "claude-sonnet-5"
        )

    def test_get_claude_teams_native_model_id_raises_for_unknown_model(self):
        with self.assertRaises(ValueError):
            get_claude_teams_native_model_id("claude-v5-opus")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: テストを実行して失敗を確認**

```bash
cd backend
poetry run pytest tests/test_claude_teams/test_models.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.claude_teams'`

- [ ] **Step 4: パッケージ初期化ファイルを作成**

`backend/app/claude_teams/__init__.py`:

```python
```

- [ ] **Step 5: models.pyを実装**

`backend/app/claude_teams/models.py`:

```python
"""Model definitions for Claude Teams plan (OAuth token, via Claude Code CLI).

These models bypass Amazon Bedrock entirely: chat requests for them are
executed through the Claude Code CLI (via `claude-agent-sdk`), authenticated
with a `CLAUDE_CODE_OAUTH_TOKEN` drawn from a pool of organization-registered
tokens (see `app/claude_teams/token_pool.py`). Usage is billed against the
flat-rate Claude Teams/Pro/Max plan quota, not Bedrock on-demand pricing.
"""

from app.routes.schemas.conversation import type_model_name

# bedrock-chat model name -> Anthropic API native `model` value.
# These match the corresponding Bedrock BASE_MODEL_IDS entries in
# app/bedrock.py with the "anthropic." prefix stripped, except claude-haiku
# which pins the same snapshot as claude-v4.5-haiku.
CLAUDE_TEAMS_MODEL_IDS: dict[str, str] = {
    "claude-teams-opus": "claude-opus-5",
    "claude-teams-sonnet": "claude-sonnet-5",
    "claude-teams-haiku": "claude-haiku-4-5-20251001",
    "claude-teams-fable": "claude-fable-5",
}


def is_claude_teams_model(model: str) -> bool:
    """Whether `model` should be routed through the Claude Code CLI /
    Claude Teams OAuth token pool instead of Amazon Bedrock."""
    return model in CLAUDE_TEAMS_MODEL_IDS


def get_claude_teams_native_model_id(model: type_model_name | str) -> str:
    """Return the Anthropic API native `model` value for a `claude-teams-*`
    model name.

    Raises:
        ValueError: if `model` is not a known Claude Teams model name.
    """
    native_id = CLAUDE_TEAMS_MODEL_IDS.get(model)
    if native_id is None:
        raise ValueError(f"Not a Claude Teams model: {model}")
    return native_id
```

- [ ] **Step 6: テストを実行してPASSを確認**

```bash
cd backend
poetry run pytest tests/test_claude_teams/test_models.py -v
```

Expected: 全5テストPASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/claude_teams/__init__.py backend/app/claude_teams/models.py backend/tests/test_claude_teams/
git commit -m "feat: add claude-teams model id mapping"
```

---

## Task 4: CDKに `ClaudeTeamsTokenTable` を追加

**Files:**
- Modify: `cdk/lib/constructs/database.ts:1-209`

**Interfaces:**
- Produces: `Database.claudeTeamsTokenTable: Table` — PK `TokenId` (String)。属性: `DisplayName` (String), `Enabled` (Boolean), `CooldownUntil` (Number, epoch seconds, TTL属性), `CreatedAt` (Number), `LastUsedAt` (Number, nullable)。GSI不要（Admin一覧はScan、選択はテーブル全件Scanで十分な規模）。

- [ ] **Step 1: database.tsにテーブル定義を追加**

`cdk/lib/constructs/database.ts`の`mcpOAuthStateTable`定義の直後（178行目付近）に追記:

```typescript
    // Claude Teams plan OAuth token pool. PK: TokenId. Metadata only —
    // the actual OAuth token string lives in Secrets Manager
    // (`claude-teams-token/{TokenId}`), never in this table.
    // `CooldownUntil` doubles as the DynamoDB TTL attribute: once a token's
    // rate-limit cooldown time passes, the item's cooldown marker expires
    // and DynamoDB best-effort-deletes stale cooldown state (the item
    // itself, including `Enabled`, persists — TTL here only prunes the
    // cooldown timestamp's staleness for cleanliness, actual cooldown
    // checks compare `CooldownUntil` against current time in application
    // code, not by relying on the item having been deleted).
    const claudeTeamsTokenTable = new Table(this, "ClaudeTeamsTokenTable", {
      partitionKey: { name: "TokenId", type: AttributeType.STRING },
      billingMode: BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.DESTROY,
      encryption: TableEncryption.AWS_MANAGED,
    });
```

- [ ] **Step 2: クラスフィールドとエクスポートに追加**

同ファイルの`readonly mcpOAuthStateTable: Table;`の下に追記:

```typescript
  readonly claudeTeamsTokenTable: Table;
```

`this.mcpOAuthStateTable = mcpOAuthStateTable;`の下に追記:

```typescript
    this.claudeTeamsTokenTable = claudeTeamsTokenTable;
```

`new CfnOutput(this, "McpOAuthStateTableName", ...)`ブロックの下に追記:

```typescript
    new CfnOutput(this, "ClaudeTeamsTokenTableName", {
      value: claudeTeamsTokenTable.tableName,
    });
```

- [ ] **Step 3: CDKのsynthが通ることを確認**

```bash
cd cdk
npx cdk synth BedrockChatStack > /dev/null
```

Expected: エラーなく完了する（既存スタック名は`cdk.json`/`bin/bedrock-chat.ts`を参照して調整。合成に失敗する場合は既存の別テーブル追加コミット、例 `9bc059f`のdiffを参考に該当スタック名を確認する）。

- [ ] **Step 4: Commit**

```bash
git add cdk/lib/constructs/database.ts
git commit -m "feat: add ClaudeTeamsTokenTable to CDK database construct"
```

---

## Task 5: `HandlerV2` Lambdaに新テーブル名・Secrets Manager権限を配線

**Files:**
- Modify: `cdk/lib/constructs/api.ts:228-256, 292-335`

**Interfaces:**
- Consumes: Task 4で作った `database.claudeTeamsTokenTable: Table`
- Produces: `HandlerV2` Lambdaの環境変数に `CLAUDE_TEAMS_TOKEN_TABLE_NAME` が追加され、IAMロールに当該テーブルの読み書き権限と `claude-teams-token/*` Secrets Manager権限が付与される

- [ ] **Step 1: Secrets Manager権限のresourcesに追記**

`cdk/lib/constructs/api.ts`の既存の`secretsmanager:*`系`PolicyStatement`（228-256行目）の`resources`配列に追記:

```typescript
          `arn:aws:secretsmanager:${Stack.of(this).region}:${
            Stack.of(this).account
          }:secret:claude-teams-token/*`,
```

- [ ] **Step 2: DynamoDBテーブルへの読み書き権限を付与**

`handlerRole.addToPolicy(...)`群の並びの近く、`props.rateLimitSevenDayParam.grantRead(handlerRole);`の直後（261行目付近）に追記:

```typescript
    database.claudeTeamsTokenTable.grantReadWriteData(handlerRole);
```

- [ ] **Step 3: 環境変数を追加**

`environment: { ... }`ブロック内、`MCP_OAUTH_STATE_TABLE_NAME: database.mcpOAuthStateTable.tableName,`の直後に追記:

```typescript
        CLAUDE_TEAMS_TOKEN_TABLE_NAME: database.claudeTeamsTokenTable.tableName,
```

- [ ] **Step 4: `ApiProps`は変更不要であることを確認**

`database: Database`が既に`ApiProps`に含まれているため、`Api`コンストラクタ内で`database.claudeTeamsTokenTable`にそのままアクセスできる。追加のprops配線は不要。

- [ ] **Step 5: CDK synthを確認**

```bash
cd cdk
npx cdk synth BedrockChatStack > /dev/null
```

Expected: エラーなく完了する。

- [ ] **Step 6: Commit**

```bash
git add cdk/lib/constructs/api.ts
git commit -m "feat: wire ClaudeTeamsTokenTable and secret access into HandlerV2 Lambda"
```

---

## Task 6: `repositories/common.py` に ClaudeTeamsTokenTable クライアントを追加

**Files:**
- Modify: `backend/app/repositories/common.py:1-46`

**Interfaces:**
- Consumes: 環境変数 `CLAUDE_TEAMS_TOKEN_TABLE_NAME`
- Produces: `get_claude_teams_token_table_client() -> Table`（既存の`get_mcp_oauth_state_table_client`と同パターン、行レベルアクセス制御なし）

- [ ] **Step 1: 環境変数定数を追加**

`backend/app/repositories/common.py`の`MCP_OAUTH_STATE_TABLE_NAME = os.environ.get(...)`の直後に追記:

```python
CLAUDE_TEAMS_TOKEN_TABLE_NAME = os.environ.get("CLAUDE_TEAMS_TOKEN_TABLE_NAME", "")
```

- [ ] **Step 2: `type_table` Literalと`_table_name_map`に追記**

```python
type_table = Literal[
    "conversation", "bot", "usage_ledger", "api_key_owner", "mcp_oauth_state",
    "claude_teams_token",
]
_table_name_map = {
    "conversation": CONVERSATION_TABLE_NAME,
    "bot": BOT_TABLE_NAME,
    "usage_ledger": USAGE_LEDGER_TABLE_NAME,
    "api_key_owner": API_KEY_OWNER_TABLE_NAME,
    "mcp_oauth_state": MCP_OAUTH_STATE_TABLE_NAME,
    "claude_teams_token": CLAUDE_TEAMS_TOKEN_TABLE_NAME,
}
```

- [ ] **Step 3: テーブルクライアント関数を追加**

`get_mcp_oauth_state_table_client`関数の直後に追記:

```python
def get_claude_teams_token_table_client():
    """Get a DynamoDB table client for the Claude Teams OAuth token pool table.
    Note: No row-level access control (server-side reference data, not
    scoped per Cognito user, same as `get_bot_table_client`).
    """
    return _get_aws_resource(
        "dynamodb", table_name=CLAUDE_TEAMS_TOKEN_TABLE_NAME
    ).Table(CLAUDE_TEAMS_TOKEN_TABLE_NAME)
```

- [ ] **Step 4: importして動作確認**

```bash
cd backend
poetry run python -c "from app.repositories.common import get_claude_teams_token_table_client; print('ok')"
```

Expected: `ok` が出力される。

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/common.py
git commit -m "feat: add claude_teams_token table client accessor"
```

---

## Task 7: トークンプール・リポジトリ（DynamoDB CRUD + ラウンドロビン + クールダウン）

**Files:**
- Create: `backend/app/claude_teams/token_repository.py`
- Test: `backend/tests/test_claude_teams/test_token_repository.py`

**Interfaces:**
- Consumes: `get_claude_teams_token_table_client()` (Task 6), `get_current_time()` (`app/utils.py`)
- Produces:
  - `@dataclass ClaudeTeamsTokenItem`: `token_id: str`, `display_name: str`, `enabled: bool`, `cooldown_until: int | None`, `created_at: int`, `last_used_at: int | None`
  - `create_token(display_name: str) -> ClaudeTeamsTokenItem` (token_idは`str(ULID())`で新規生成、Secrets Managerへの保存はTask 8で別関数として行う。ここではDynamoDBメタデータのみ)
  - `list_tokens() -> list[ClaudeTeamsTokenItem]`
  - `set_enabled(token_id: str, enabled: bool) -> None`
  - `set_cooldown(token_id: str, cooldown_until_epoch_seconds: int) -> None`
  - `disable_token(token_id: str) -> None` (`set_enabled(token_id, False)`の別名。エラーハンドリングでの恒久停止用)
  - `delete_token(token_id: str) -> None`
  - `pick_next_available_token() -> ClaudeTeamsTokenItem | None`: `enabled=True`かつ(`cooldown_until`が未設定または現在時刻より過去)のトークンをラウンドロビンで1件選ぶ。選んだトークンの`last_used_at`を現在時刻に更新してから返す。ラウンドロビンは「`last_used_at`が最も古い(または未設定の)ものを選ぶ」方式で実現する（追加のカーソル管理テーブル不要）。候補がなければ`None`。

- [ ] **Step 1: テストを書く**

`backend/tests/test_claude_teams/test_token_repository.py`:

```python
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.claude_teams.token_repository import (
    create_token,
    delete_token,
    disable_token,
    list_tokens,
    pick_next_available_token,
    set_cooldown,
    set_enabled,
)


class TestClaudeTeamsTokenRepository(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("boto3.resource")
        self.mock_boto3_resource = self.patcher.start()
        self.mock_table = MagicMock()
        self.mock_boto3_resource.return_value.Table.return_value = self.mock_table

        os.environ["CLAUDE_TEAMS_TOKEN_TABLE_NAME"] = "test-claude-teams-token-table"
        os.environ["BEDROCK_REGION"] = "us-east-1"

    def tearDown(self):
        self.patcher.stop()
        os.environ.pop("CLAUDE_TEAMS_TOKEN_TABLE_NAME", None)
        os.environ.pop("BEDROCK_REGION", None)

    @patch(
        "app.claude_teams.token_repository.get_current_time",
        return_value=1_700_000_000_000,
    )
    def test_create_token_puts_expected_item(self, mock_time):
        item = create_token(display_name="team-a-token")

        self.mock_table.put_item.assert_called_once()
        put_item = self.mock_table.put_item.call_args.kwargs["Item"]
        self.assertEqual(put_item["DisplayName"], "team-a-token")
        self.assertTrue(put_item["Enabled"])
        self.assertEqual(put_item["CreatedAt"], 1_700_000_000_000)
        self.assertIsNone(put_item.get("CooldownUntil"))
        self.assertEqual(item.display_name, "team-a-token")
        self.assertTrue(item.enabled)

    def test_list_tokens_returns_items_from_scan(self):
        self.mock_table.scan.return_value = {
            "Items": [
                {
                    "TokenId": "tok-1",
                    "DisplayName": "team-a",
                    "Enabled": True,
                    "CreatedAt": 1_700_000_000_000,
                }
            ]
        }

        tokens = list_tokens()

        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].token_id, "tok-1")
        self.assertEqual(tokens[0].display_name, "team-a")
        self.assertTrue(tokens[0].enabled)
        self.assertIsNone(tokens[0].cooldown_until)
        self.assertIsNone(tokens[0].last_used_at)

    def test_list_tokens_paginates(self):
        self.mock_table.scan.side_effect = [
            {
                "Items": [{"TokenId": "tok-1", "DisplayName": "a", "Enabled": True, "CreatedAt": 1}],
                "LastEvaluatedKey": {"TokenId": "tok-1"},
            },
            {
                "Items": [{"TokenId": "tok-2", "DisplayName": "b", "Enabled": True, "CreatedAt": 2}],
            },
        ]

        tokens = list_tokens()

        self.assertEqual([t.token_id for t in tokens], ["tok-1", "tok-2"])
        self.assertEqual(self.mock_table.scan.call_count, 2)

    def test_set_enabled_updates_item(self):
        set_enabled("tok-1", False)

        self.mock_table.update_item.assert_called_once()
        kwargs = self.mock_table.update_item.call_args.kwargs
        self.assertEqual(kwargs["Key"], {"TokenId": "tok-1"})
        self.assertEqual(
            kwargs["ExpressionAttributeValues"][":enabled"], False
        )

    def test_disable_token_calls_set_enabled_false(self):
        disable_token("tok-1")

        self.mock_table.update_item.assert_called_once()
        kwargs = self.mock_table.update_item.call_args.kwargs
        self.assertEqual(kwargs["ExpressionAttributeValues"][":enabled"], False)

    @patch(
        "app.claude_teams.token_repository.get_current_time",
        return_value=1_700_000_000_000,
    )
    def test_set_cooldown_updates_item(self, mock_time):
        set_cooldown("tok-1", cooldown_until_epoch_seconds=1_700_000_300)

        self.mock_table.update_item.assert_called_once()
        kwargs = self.mock_table.update_item.call_args.kwargs
        self.assertEqual(kwargs["Key"], {"TokenId": "tok-1"})
        self.assertEqual(
            kwargs["ExpressionAttributeValues"][":cooldown_until"], 1_700_000_300
        )

    def test_delete_token_deletes_item(self):
        delete_token("tok-1")

        self.mock_table.delete_item.assert_called_once_with(Key={"TokenId": "tok-1"})

    @patch(
        "app.claude_teams.token_repository.get_current_time",
        return_value=1_700_000_000_000,
    )
    def test_pick_next_available_token_skips_disabled_and_cooling_down(
        self, mock_time
    ):
        self.mock_table.scan.return_value = {
            "Items": [
                {
                    "TokenId": "tok-disabled",
                    "DisplayName": "disabled",
                    "Enabled": False,
                    "CreatedAt": 1,
                    "LastUsedAt": 100,
                },
                {
                    "TokenId": "tok-cooling",
                    "DisplayName": "cooling",
                    "Enabled": True,
                    "CreatedAt": 1,
                    "LastUsedAt": 50,
                    "CooldownUntil": 1_700_000_500,  # future
                },
                {
                    "TokenId": "tok-available-older",
                    "DisplayName": "available-older",
                    "Enabled": True,
                    "CreatedAt": 1,
                    "LastUsedAt": 10,
                },
                {
                    "TokenId": "tok-available-newer",
                    "DisplayName": "available-newer",
                    "Enabled": True,
                    "CreatedAt": 1,
                    "LastUsedAt": 900,
                },
            ]
        }

        picked = pick_next_available_token()

        self.assertIsNotNone(picked)
        assert picked is not None
        # Round robin: pick the one least-recently used among available candidates.
        self.assertEqual(picked.token_id, "tok-available-older")
        self.mock_table.update_item.assert_called_once()
        update_kwargs = self.mock_table.update_item.call_args.kwargs
        self.assertEqual(update_kwargs["Key"], {"TokenId": "tok-available-older"})
        self.assertEqual(
            update_kwargs["ExpressionAttributeValues"][":last_used_at"],
            1_700_000_000_000,
        )

    def test_pick_next_available_token_treats_expired_cooldown_as_available(self):
        self.mock_table.scan.return_value = {
            "Items": [
                {
                    "TokenId": "tok-1",
                    "DisplayName": "a",
                    "Enabled": True,
                    "CreatedAt": 1,
                    "CooldownUntil": 1,  # long past
                },
            ]
        }

        picked = pick_next_available_token()

        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked.token_id, "tok-1")

    def test_pick_next_available_token_returns_none_when_no_candidates(self):
        self.mock_table.scan.return_value = {"Items": []}

        picked = pick_next_available_token()

        self.assertIsNone(picked)
        self.mock_table.update_item.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストを実行して失敗を確認**

```bash
cd backend
poetry run pytest tests/test_claude_teams/test_token_repository.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.claude_teams.token_repository'`

- [ ] **Step 3: token_repository.pyを実装**

`backend/app/claude_teams/token_repository.py`:

```python
"""DynamoDB-backed metadata repository for the Claude Teams OAuth token pool.

The actual OAuth token strings live in Secrets Manager
(`claude-teams-token/{token_id}`, see `token_secrets.py`), never in this
table. This module only manages: which tokens exist, whether each is
enabled, its cooldown state, and round-robin selection bookkeeping
(`last_used_at`).
"""

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from ulid import ULID

from app.repositories.common import get_claude_teams_token_table_client
from app.utils import get_current_time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@dataclass
class ClaudeTeamsTokenItem:
    token_id: str
    display_name: str
    enabled: bool
    created_at: int
    cooldown_until: int | None = None
    last_used_at: int | None = None


def _item_to_model(item: dict) -> ClaudeTeamsTokenItem:
    return ClaudeTeamsTokenItem(
        token_id=item["TokenId"],
        display_name=item["DisplayName"],
        enabled=bool(item["Enabled"]),
        created_at=int(item["CreatedAt"]),
        cooldown_until=(
            int(item["CooldownUntil"]) if "CooldownUntil" in item else None
        ),
        last_used_at=(int(item["LastUsedAt"]) if "LastUsedAt" in item else None),
    )


def create_token(display_name: str) -> ClaudeTeamsTokenItem:
    """Create a new token pool entry (metadata only). Caller is responsible
    for separately storing the actual token string in Secrets Manager
    (see `token_secrets.store_claude_teams_token`)."""
    table = get_claude_teams_token_table_client()
    token_id = str(ULID())
    now_ms = get_current_time()
    table.put_item(
        Item={
            "TokenId": token_id,
            "DisplayName": display_name,
            "Enabled": True,
            "CreatedAt": now_ms,
        }
    )
    return ClaudeTeamsTokenItem(
        token_id=token_id,
        display_name=display_name,
        enabled=True,
        created_at=now_ms,
    )


def list_tokens() -> list[ClaudeTeamsTokenItem]:
    """Return all registered tokens (metadata only), unordered."""
    table = get_claude_teams_token_table_client()
    items: list[dict] = []
    response = table.scan()
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return [_item_to_model(item) for item in items]


def set_enabled(token_id: str, enabled: bool) -> None:
    table = get_claude_teams_token_table_client()
    table.update_item(
        Key={"TokenId": token_id},
        UpdateExpression="SET Enabled = :enabled",
        ExpressionAttributeValues={":enabled": enabled},
    )


def disable_token(token_id: str) -> None:
    """Permanently disable a token (e.g. after an authentication_failed /
    oauth_org_not_allowed error, meaning the token itself is invalid)."""
    set_enabled(token_id, False)


def set_cooldown(token_id: str, cooldown_until_epoch_seconds: int) -> None:
    """Mark a token as cooling down (e.g. after a rate_limit / billing_error
    response) until the given epoch-seconds timestamp."""
    table = get_claude_teams_token_table_client()
    table.update_item(
        Key={"TokenId": token_id},
        UpdateExpression="SET CooldownUntil = :cooldown_until",
        ExpressionAttributeValues={":cooldown_until": cooldown_until_epoch_seconds},
    )


def delete_token(token_id: str) -> None:
    table = get_claude_teams_token_table_client()
    table.delete_item(Key={"TokenId": token_id})


def pick_next_available_token() -> ClaudeTeamsTokenItem | None:
    """Round-robin selection: among enabled tokens whose cooldown (if any)
    has already passed, pick the one with the oldest `last_used_at` (or one
    that has never been used), mark it as just-used, and return it.

    Returns None if no token is currently available.
    """
    now_ms = get_current_time()
    now_s = now_ms // 1000

    candidates = [
        token
        for token in list_tokens()
        if token.enabled and (token.cooldown_until is None or token.cooldown_until <= now_s)
    ]
    if not candidates:
        return None

    # Round robin: the least-recently-used candidate goes first. A token
    # that has never been used (last_used_at is None) sorts before any
    # that has, so brand-new tokens get tried before recycling old ones.
    candidates.sort(key=lambda t: (t.last_used_at is not None, t.last_used_at or 0))
    picked = candidates[0]

    table = get_claude_teams_token_table_client()
    table.update_item(
        Key={"TokenId": picked.token_id},
        UpdateExpression="SET LastUsedAt = :last_used_at",
        ExpressionAttributeValues={":last_used_at": now_ms},
    )
    picked.last_used_at = now_ms
    return picked
```

- [ ] **Step 4: テストを実行してPASSを確認**

```bash
cd backend
poetry run pytest tests/test_claude_teams/test_token_repository.py -v
```

Expected: 全11テストPASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/claude_teams/token_repository.py backend/tests/test_claude_teams/test_token_repository.py
git commit -m "feat: add Claude Teams token pool DynamoDB repository"
```

---

## Task 8: トークン本体の Secrets Manager 保存/取得/削除

**Files:**
- Create: `backend/app/claude_teams/token_secrets.py`
- Test: `backend/tests/test_claude_teams/test_token_secrets.py`

**Interfaces:**
- Produces:
  - `store_claude_teams_token(token_id: str, token_value: str) -> None`
  - `get_claude_teams_token(token_id: str) -> str`
  - `delete_claude_teams_token(token_id: str) -> None`

- [ ] **Step 1: テストを書く**

`backend/tests/test_claude_teams/test_token_secrets.py`:

```python
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.claude_teams.token_secrets import (
    delete_claude_teams_token,
    get_claude_teams_token,
    store_claude_teams_token,
)


class TestClaudeTeamsTokenSecrets(unittest.TestCase):
    @patch("boto3.client")
    def test_store_creates_new_secret(self, mock_boto3_client):
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.describe_secret.side_effect = Exception("not used in create path")
        from botocore.exceptions import ClientError

        mock_client.describe_secret.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "nope"}},
            "DescribeSecret",
        )

        store_claude_teams_token("tok-1", "sk-oauth-abc123")

        mock_client.create_secret.assert_called_once()
        kwargs = mock_client.create_secret.call_args.kwargs
        self.assertEqual(kwargs["Name"], "claude-teams-token/tok-1")
        self.assertEqual(kwargs["SecretString"], "sk-oauth-abc123")

    @patch("boto3.client")
    def test_store_updates_existing_secret(self, mock_boto3_client):
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.describe_secret.return_value = {"ARN": "arn:aws:secretsmanager:..."}

        store_claude_teams_token("tok-1", "sk-oauth-new")

        mock_client.update_secret.assert_called_once_with(
            SecretId="claude-teams-token/tok-1", SecretString="sk-oauth-new"
        )
        mock_client.create_secret.assert_not_called()

    @patch("boto3.client")
    def test_get_returns_secret_string(self, mock_boto3_client):
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.get_secret_value.return_value = {"SecretString": "sk-oauth-abc123"}

        value = get_claude_teams_token("tok-1")

        self.assertEqual(value, "sk-oauth-abc123")
        mock_client.get_secret_value.assert_called_once_with(
            SecretId="claude-teams-token/tok-1"
        )

    @patch("boto3.client")
    def test_delete_deletes_secret(self, mock_boto3_client):
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client

        delete_claude_teams_token("tok-1")

        mock_client.delete_secret.assert_called_once_with(
            SecretId="claude-teams-token/tok-1", ForceDeleteWithoutRecovery=True
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストを実行して失敗を確認**

```bash
cd backend
poetry run pytest tests/test_claude_teams/test_token_secrets.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.claude_teams.token_secrets'`

- [ ] **Step 3: token_secrets.pyを実装**

`backend/app/claude_teams/token_secrets.py`:

```python
"""Secrets Manager storage for Claude Teams OAuth token strings.

Deterministic secret name `claude-teams-token/{token_id}` — the token_id
comes from the DynamoDB pool (see `token_repository.py`) and doubles as the
Secrets Manager key, so no ARN needs to be tracked separately.

The token string is stored as a raw `SecretString` (not JSON-wrapped),
unlike `store_api_key_to_secret_manager` in `app/utils.py`, since there is
exactly one value to store per secret and no per-bot/per-user namespacing
is needed here.
"""

import logging

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _secret_name(token_id: str) -> str:
    return f"claude-teams-token/{token_id}"


def store_claude_teams_token(token_id: str, token_value: str) -> None:
    """Create or update the Secrets Manager entry for one pool token."""
    secret_name = _secret_name(token_id)
    client = boto3.client("secretsmanager")

    try:
        client.describe_secret(SecretId=secret_name)
        client.update_secret(SecretId=secret_name, SecretString=token_value)
        logger.info(f"Updated existing secret for token {token_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            client.create_secret(Name=secret_name, SecretString=token_value)
            logger.info(f"Created new secret for token {token_id}")
        else:
            raise


def get_claude_teams_token(token_id: str) -> str:
    """Return the raw OAuth token string for `token_id`."""
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=_secret_name(token_id))
    return response["SecretString"]


def delete_claude_teams_token(token_id: str) -> None:
    """Permanently delete the Secrets Manager entry for `token_id`
    (no recovery window — the pool table row is deleted alongside this,
    and there is no reason to keep a de-registered OAuth token around)."""
    client = boto3.client("secretsmanager")
    client.delete_secret(SecretId=_secret_name(token_id), ForceDeleteWithoutRecovery=True)
```

- [ ] **Step 4: テストを実行してPASSを確認**

```bash
cd backend
poetry run pytest tests/test_claude_teams/test_token_secrets.py -v
```

Expected: 全4テストPASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/claude_teams/token_secrets.py backend/tests/test_claude_teams/test_token_secrets.py
git commit -m "feat: add Claude Teams token Secrets Manager storage"
```

---

## Task 9: Admin API（トークン登録・一覧・有効無効切替・削除）

**Files:**
- Create: `backend/app/routes/schemas/claude_teams.py`
- Create: `backend/app/usecases/claude_teams_admin.py`
- Modify: `backend/app/routes/admin.py`
- Test: `backend/tests/test_usecases/test_claude_teams_admin.py`

**Interfaces:**
- Consumes: `create_token`, `list_tokens`, `set_enabled`, `delete_token` (Task 7), `store_claude_teams_token`, `delete_claude_teams_token` (Task 8), `check_admin` dependency (`app/dependencies.py`)
- Produces:
  - Pydantic schemas: `ClaudeTeamsTokenOutput` (token_id, display_name, enabled, is_cooling_down: bool, last_used_at: int | None), `CreateClaudeTeamsTokenInput` (display_name: str, token_value: str), `UpdateClaudeTeamsTokenInput` (enabled: bool | None = None, display_name: str | None = None)
  - Routes: `POST /admin/claude-teams-tokens`, `GET /admin/claude-teams-tokens`, `PATCH /admin/claude-teams-tokens/{token_id}`, `DELETE /admin/claude-teams-tokens/{token_id}` (すべて`Depends(check_admin)`)
  - Usecase functions: `create_claude_teams_token(display_name, token_value) -> ClaudeTeamsTokenItem`, `list_claude_teams_tokens() -> list[ClaudeTeamsTokenItem]`, `update_claude_teams_token(token_id, enabled, display_name) -> None`, `delete_claude_teams_token_usecase(token_id) -> None`

- [ ] **Step 1: usecaseの失敗するテストを書く**

`backend/tests/test_usecases/test_claude_teams_admin.py`:

```python
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.usecases.claude_teams_admin import (
    create_claude_teams_token,
    delete_claude_teams_token_usecase,
    list_claude_teams_tokens,
    update_claude_teams_token,
)
from app.claude_teams.token_repository import ClaudeTeamsTokenItem


class TestClaudeTeamsAdminUsecase(unittest.TestCase):
    @patch("app.usecases.claude_teams_admin.store_claude_teams_token")
    @patch("app.usecases.claude_teams_admin.create_token")
    def test_create_claude_teams_token_stores_secret_then_metadata(
        self, mock_create_token, mock_store_secret
    ):
        mock_create_token.return_value = ClaudeTeamsTokenItem(
            token_id="tok-1", display_name="team-a", enabled=True, created_at=1
        )

        result = create_claude_teams_token(display_name="team-a", token_value="sk-oauth-abc")

        mock_create_token.assert_called_once_with(display_name="team-a")
        mock_store_secret.assert_called_once_with("tok-1", "sk-oauth-abc")
        self.assertEqual(result.token_id, "tok-1")

    @patch("app.usecases.claude_teams_admin.list_tokens")
    def test_list_claude_teams_tokens_returns_repository_result(self, mock_list):
        mock_list.return_value = [
            ClaudeTeamsTokenItem(
                token_id="tok-1", display_name="a", enabled=True, created_at=1
            )
        ]

        result = list_claude_teams_tokens()

        self.assertEqual(result, mock_list.return_value)

    @patch("app.usecases.claude_teams_admin.set_enabled")
    def test_update_claude_teams_token_sets_enabled(self, mock_set_enabled):
        update_claude_teams_token(token_id="tok-1", enabled=False, display_name=None)

        mock_set_enabled.assert_called_once_with("tok-1", False)

    @patch("app.usecases.claude_teams_admin.delete_claude_teams_token")
    @patch("app.usecases.claude_teams_admin.delete_token")
    def test_delete_claude_teams_token_usecase_deletes_both(
        self, mock_delete_token, mock_delete_secret
    ):
        delete_claude_teams_token_usecase("tok-1")

        mock_delete_token.assert_called_once_with("tok-1")
        mock_delete_secret.assert_called_once_with("tok-1")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストを実行して失敗を確認**

```bash
cd backend
poetry run pytest tests/test_usecases/test_claude_teams_admin.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.usecases.claude_teams_admin'`

- [ ] **Step 3: usecases/claude_teams_admin.pyを実装**

`backend/app/usecases/claude_teams_admin.py`:

```python
"""Admin usecases for managing the Claude Teams OAuth token pool."""

from app.claude_teams.token_repository import (
    ClaudeTeamsTokenItem,
    create_token,
    delete_token,
    list_tokens,
    set_enabled,
)
from app.claude_teams.token_secrets import (
    delete_claude_teams_token,
    store_claude_teams_token,
)


def create_claude_teams_token(display_name: str, token_value: str) -> ClaudeTeamsTokenItem:
    """Register a new Claude Teams OAuth token: store the metadata row
    first (to get a token_id), then the secret."""
    item = create_token(display_name=display_name)
    store_claude_teams_token(item.token_id, token_value)
    return item


def list_claude_teams_tokens() -> list[ClaudeTeamsTokenItem]:
    return list_tokens()


def update_claude_teams_token(
    token_id: str, enabled: bool | None, display_name: str | None
) -> None:
    """Update mutable fields on a token. Only `enabled` is currently
    supported for update (display_name rename and token-string rotation
    are out of scope — see architecture doc: token string updates are
    delete + re-register)."""
    if enabled is not None:
        set_enabled(token_id, enabled)


def delete_claude_teams_token_usecase(token_id: str) -> None:
    """Delete both the DynamoDB metadata row and the Secrets Manager entry."""
    delete_token(token_id)
    delete_claude_teams_token(token_id)
```

- [ ] **Step 4: テストを実行してPASSを確認**

```bash
cd backend
poetry run pytest tests/test_usecases/test_claude_teams_admin.py -v
```

Expected: 全4テストPASS

- [ ] **Step 5: Pydanticスキーマを作成**

`backend/app/routes/schemas/claude_teams.py`:

```python
from app.routes.schemas.base import BaseSchema


class ClaudeTeamsTokenOutput(BaseSchema):
    token_id: str
    display_name: str
    enabled: bool
    is_cooling_down: bool
    created_at: int
    last_used_at: int | None


class CreateClaudeTeamsTokenInput(BaseSchema):
    display_name: str
    token_value: str


class UpdateClaudeTeamsTokenInput(BaseSchema):
    enabled: bool | None = None
```

- [ ] **Step 6: admin.pyにルートを追加**

`backend/app/routes/admin.py`の先頭import群に追記:

```python
from app.routes.schemas.claude_teams import (
    ClaudeTeamsTokenOutput,
    CreateClaudeTeamsTokenInput,
    UpdateClaudeTeamsTokenInput,
)
from app.usecases.claude_teams_admin import (
    create_claude_teams_token,
    delete_claude_teams_token_usecase,
    list_claude_teams_tokens,
    update_claude_teams_token,
)
from app.utils import get_current_time
```

ファイル末尾に追記:

```python
def _to_claude_teams_token_output(token) -> ClaudeTeamsTokenOutput:
    now_s = get_current_time() // 1000
    return ClaudeTeamsTokenOutput(
        token_id=token.token_id,
        display_name=token.display_name,
        enabled=token.enabled,
        is_cooling_down=token.cooldown_until is not None and token.cooldown_until > now_s,
        created_at=token.created_at,
        last_used_at=token.last_used_at,
    )


@router.post("/admin/claude-teams-tokens", response_model=ClaudeTeamsTokenOutput)
def create_claude_teams_token_route(
    body: CreateClaudeTeamsTokenInput,
    admin_check=Depends(check_admin),
):
    """Register a new Claude Teams OAuth token. The token string is written
    to Secrets Manager and never returned again by any endpoint."""
    token = create_claude_teams_token(
        display_name=body.display_name, token_value=body.token_value
    )
    return _to_claude_teams_token_output(token)


@router.get("/admin/claude-teams-tokens", response_model=list[ClaudeTeamsTokenOutput])
def list_claude_teams_tokens_route(admin_check=Depends(check_admin)):
    """List registered Claude Teams OAuth tokens. The token string itself
    is never included in the response."""
    tokens = list_claude_teams_tokens()
    return [_to_claude_teams_token_output(token) for token in tokens]


@router.patch("/admin/claude-teams-tokens/{token_id}")
def update_claude_teams_token_route(
    token_id: str,
    body: UpdateClaudeTeamsTokenInput,
    admin_check=Depends(check_admin),
):
    """Enable or disable a token. Renaming and token-string rotation are
    not supported here (delete + re-register instead)."""
    update_claude_teams_token(
        token_id=token_id, enabled=body.enabled, display_name=None
    )


@router.delete("/admin/claude-teams-tokens/{token_id}")
def delete_claude_teams_token_route(
    token_id: str,
    admin_check=Depends(check_admin),
):
    """Permanently remove a token from the pool and delete its secret."""
    delete_claude_teams_token_usecase(token_id)
```

- [ ] **Step 7: バックエンド全体のテストスイートを実行し、既存テストが壊れていないことを確認**

```bash
cd backend
poetry run pytest tests/test_routes tests/test_usecases tests/test_claude_teams -v
```

Expected: 全テストPASS（既存admin routeテストも含む）。

- [ ] **Step 8: Commit**

```bash
git add backend/app/routes/schemas/claude_teams.py backend/app/usecases/claude_teams_admin.py backend/app/routes/admin.py backend/tests/test_usecases/test_claude_teams_admin.py
git commit -m "feat: add admin CRUD API for Claude Teams OAuth token pool"
```

---

## Task 10: CLI用MCPブリッジ（bot内蔵ツール・外部MCPをインプロセスMCP化）

**Files:**
- Create: `backend/app/claude_teams/mcp_bridge.py`
- Test: `backend/tests/test_claude_teams/test_mcp_bridge.py`

**Interfaces:**
- Consumes: `mcp_tools_scope(bot)` (`app/strands_integration/tools/mcp_tools.py`), `get_strands_tools(bot, model_name)` (`app/strands_integration/utils.py`), strandsの`MCPAgentTool`/`AgentTool`インターフェース（`invoke_async(tool_use: ToolUse, invocation_state: dict) -> AsyncIterator[ToolResult]` — strandsの`AgentTool`基底クラスの標準呼び出し規約）
- Produces:
  - `build_claude_teams_mcp_servers(bot: BotModel | None, model_name: str) -> tuple[dict, list[str], contextlib.AbstractContextManager]`: strandsの全ツール（内蔵ツール＋外部MCP、`mcp_tools_scope`のコンテキスト内で取得）を`claude_agent_sdk.create_sdk_mcp_server`でラップした1つの`McpSdkServerConfig`を含む辞書（`{"bedrock_chat_tools": server_config}`）、`ClaudeAgentOptions.allowed_tools`にそのまま渡せる`mcp__bedrock_chat_tools__{tool_name}`形式の名前一覧、そしてそれを解放するコンテキストマネージャの3つを返す
  - `list_allowed_tool_names(server_name: str, strands_tools: list) -> list[str]`: `ClaudeAgentOptions.allowed_tools`に渡す `mcp__{server_name}__{tool_name}` 形式の名前一覧（`build_claude_teams_mcp_servers`の内部実装で使う下位ヘルパー）

- [ ] **Step 1: テストを書く**

`backend/tests/test_claude_teams/test_mcp_bridge.py`:

```python
import asyncio
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.claude_teams.mcp_bridge import (
    build_claude_teams_mcp_servers,
    list_allowed_tool_names,
)


class _FakeStrandsTool:
    def __init__(self, tool_name: str):
        self.tool_name = tool_name

    async def invoke_async(self, tool_use, invocation_state):
        yield {
            "status": "success",
            "content": [{"text": f"result for {self.tool_name}"}],
        }


class TestMcpBridge(unittest.TestCase):
    def test_list_allowed_tool_names_prefixes_with_mcp_server(self):
        tools = [_FakeStrandsTool("internet_search"), _FakeStrandsTool("bedrock_agent")]

        names = list_allowed_tool_names("bedrock_chat_tools", tools)

        self.assertEqual(
            names,
            [
                "mcp__bedrock_chat_tools__internet_search",
                "mcp__bedrock_chat_tools__bedrock_agent",
            ],
        )

    @patch("app.claude_teams.mcp_bridge.mcp_tools_scope")
    @patch("app.claude_teams.mcp_bridge.get_strands_tools")
    def test_build_claude_teams_mcp_servers_wraps_all_tools_into_one_server(
        self, mock_get_strands_tools, mock_mcp_tools_scope
    ):
        mock_get_strands_tools.return_value = [_FakeStrandsTool("internet_search")]
        mock_mcp_tools_scope.return_value.__enter__.return_value = [
            _FakeStrandsTool("atlassian_search")
        ]
        mock_mcp_tools_scope.return_value.__exit__.return_value = False

        mcp_servers, allowed_tools, cleanup = build_claude_teams_mcp_servers(
            bot=None, model_name="claude-teams-sonnet"
        )

        self.assertIn("bedrock_chat_tools", mcp_servers)
        self.assertEqual(
            allowed_tools,
            [
                "mcp__bedrock_chat_tools__internet_search",
                "mcp__bedrock_chat_tools__atlassian_search",
            ],
        )
        cleanup.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストを実行して失敗を確認**

```bash
cd backend
poetry run pytest tests/test_claude_teams/test_mcp_bridge.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.claude_teams.mcp_bridge'`

- [ ] **Step 3: mcp_bridge.pyを実装**

`backend/app/claude_teams/mcp_bridge.py`:

```python
"""Bridges bedrock-chat's existing strands-based tools (built-in tools like
internet search/knowledge search, plus a bot's configured external MCP
servers) into an in-process MCP server that the Claude Code CLI subprocess
(via `claude-agent-sdk`) can call.

Why bridge through strands instead of talking to `ClaudeAgentOptions.mcp_servers`
directly: that option only accepts server *launch* configs (stdio/SSE/HTTP
connection info), with no way to plug in a live `httpx.Auth` object the way
strands' `MCPClient` supports. Bedrock-chat's OAuth-type MCP servers need
that live, refreshable auth. Reusing `mcp_tools_scope` (already handles
connecting, auth, and per-server failure isolation) and wrapping the
resulting tools as `@tool`-decorated functions keeps that logic in one
place.
"""

import contextlib
import logging
from typing import Any

from app.repositories.models.custom_bot import BotModel
from app.routes.schemas.conversation import type_model_name
from app.strands_integration.tools.mcp_tools import mcp_tools_scope
from app.strands_integration.utils import get_strands_tools
from claude_agent_sdk import create_sdk_mcp_server, tool as sdk_tool

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BEDROCK_CHAT_MCP_SERVER_NAME = "bedrock_chat_tools"


def list_allowed_tool_names(server_name: str, strands_tools: list) -> list[str]:
    """The `ClaudeAgentOptions.allowed_tools` entries for every tool in
    `strands_tools`, in the `mcp__{server_name}__{tool_name}` form the CLI
    uses for MCP-provided tools."""
    return [f"mcp__{server_name}__{t.tool_name}" for t in strands_tools]


def _wrap_strands_tool_as_sdk_tool(strands_agent_tool: Any):
    """Wrap one strands `AgentTool` as a `claude_agent_sdk` in-process MCP
    tool. Uses a permissive `{}` (any-shape dict) input schema since the
    strands tool already validates/handles its own arguments; the SDK's
    `@tool` decorator only needs *a* schema, not a strict one, to register
    the tool."""

    async def _handler(args: dict) -> dict:
        result_content: list[dict] = []
        status = "success"
        async for tool_result in strands_agent_tool.invoke_async(
            tool_use={
                "toolUseId": "claude-teams-bridge",
                "name": strands_agent_tool.tool_name,
                "input": args,
            },
            invocation_state={},
        ):
            status = tool_result.get("status", status)
            for content in tool_result.get("content", []):
                if "text" in content:
                    result_content.append({"type": "text", "text": content["text"]})
                elif "json" in content:
                    import json as _json

                    result_content.append(
                        {"type": "text", "text": _json.dumps(content["json"])}
                    )
        if not result_content:
            result_content = [{"type": "text", "text": ""}]
        return {"content": result_content, "is_error": status == "error"}

    return sdk_tool(
        strands_agent_tool.tool_name,
        f"Bedrock Chat tool: {strands_agent_tool.tool_name}",
        {},  # permissive schema; the wrapped strands tool validates its own input
    )(_handler)


def build_claude_teams_mcp_servers(
    bot: BotModel | None, model_name: type_model_name
) -> tuple[dict[str, Any], list[str], contextlib.AbstractContextManager]:
    """Return (mcp_servers dict for ClaudeAgentOptions, the matching
    allowed_tools list, a context manager to close when the chat turn
    finishes).

    All of a bot's built-in tools and external MCP server tools (already
    fetched live via `mcp_tools_scope`) are combined into a single
    in-process MCP server named `bedrock_chat_tools`.
    """
    stack = contextlib.ExitStack()
    mcp_tools = stack.enter_context(mcp_tools_scope(bot))
    builtin_tools = get_strands_tools(bot, model_name)

    all_tools = [*builtin_tools, *mcp_tools]

    if not all_tools:
        return {}, [], stack

    sdk_tools = [_wrap_strands_tool_as_sdk_tool(t) for t in all_tools]
    allowed_tools = list_allowed_tool_names(BEDROCK_CHAT_MCP_SERVER_NAME, all_tools)

    server = create_sdk_mcp_server(
        name=BEDROCK_CHAT_MCP_SERVER_NAME,
        tools=sdk_tools,
    )
    return {BEDROCK_CHAT_MCP_SERVER_NAME: server}, allowed_tools, stack
```

- [ ] **Step 4: テストを実行してPASSを確認**

```bash
cd backend
poetry run pytest tests/test_claude_teams/test_mcp_bridge.py -v
```

Expected: 全2テストPASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/claude_teams/mcp_bridge.py backend/tests/test_claude_teams/test_mcp_bridge.py
git commit -m "feat: bridge strands tools into an in-process MCP server for Claude Code CLI"
```

---

## Task 11: CLAUDE.md一時ディレクトリ管理

**Files:**
- Create: `backend/app/claude_teams/workspace.py`
- Test: `backend/tests/test_claude_teams/test_workspace.py`

**Interfaces:**
- Produces: `claude_teams_workspace(instructions: list[str]) -> contextlib.AbstractContextManager[str]`: コンテキストマネージャとして使い、`with claude_teams_workspace(instructions) as workspace_dir:`のように使う。`workspace_dir`は新規一時ディレクトリのパス。`instructions`が空でなければ`workspace_dir/CLAUDE.md`を書き込む。exit時にディレクトリを再帰削除する。

- [ ] **Step 1: テストを書く**

`backend/tests/test_claude_teams/test_workspace.py`:

```python
import os
import sys
import unittest

sys.path.insert(0, ".")
from app.claude_teams.workspace import claude_teams_workspace


class TestClaudeTeamsWorkspace(unittest.TestCase):
    def test_writes_claude_md_with_joined_instructions(self):
        with claude_teams_workspace(["Be concise.", "Always answer in Japanese."]) as workspace_dir:
            self.assertTrue(os.path.isdir(workspace_dir))
            claude_md_path = os.path.join(workspace_dir, "CLAUDE.md")
            self.assertTrue(os.path.isfile(claude_md_path))
            with open(claude_md_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("Be concise.", content)
            self.assertIn("Always answer in Japanese.", content)

    def test_does_not_write_claude_md_when_no_instructions(self):
        with claude_teams_workspace([]) as workspace_dir:
            self.assertTrue(os.path.isdir(workspace_dir))
            claude_md_path = os.path.join(workspace_dir, "CLAUDE.md")
            self.assertFalse(os.path.isfile(claude_md_path))

    def test_deletes_workspace_dir_on_exit(self):
        captured_dir = None
        with claude_teams_workspace(["hello"]) as workspace_dir:
            captured_dir = workspace_dir
            self.assertTrue(os.path.isdir(captured_dir))

        self.assertFalse(os.path.isdir(captured_dir))

    def test_deletes_workspace_dir_on_exception(self):
        captured_dir = None
        with self.assertRaises(ValueError):
            with claude_teams_workspace(["hello"]) as workspace_dir:
                captured_dir = workspace_dir
                raise ValueError("boom")

        self.assertFalse(os.path.isdir(captured_dir))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストを実行して失敗を確認**

```bash
cd backend
poetry run pytest tests/test_claude_teams/test_workspace.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.claude_teams.workspace'`

- [ ] **Step 3: workspace.pyを実装**

`backend/app/claude_teams/workspace.py`:

```python
"""Per-chat-turn temporary workspace for the Claude Code CLI subprocess.

Bot instructions are written as a `CLAUDE.md` file in a fresh temporary
directory, which the CLI reads automatically as project memory (a
legitimate, documented Claude Code feature — not an impersonation trick).
The directory (and any `CLAUDE_CONFIG_DIR` scoped inside it, wired up by the
caller) is deleted when the chat turn ends, so nothing leaks across a
warm-started Lambda's reused execution environment.
"""

import contextlib
import logging
import os
import shutil
import tempfile

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

CLAUDE_MD_FILENAME = "CLAUDE.md"


@contextlib.contextmanager
def claude_teams_workspace(instructions: list[str]):
    """Yield a fresh temporary directory, containing a `CLAUDE.md` built
    from `instructions` (skipped if `instructions` is empty). The directory
    is always removed on exit, including when the body raises."""
    workspace_dir = tempfile.mkdtemp(prefix="claude-teams-")
    try:
        joined = "\n\n".join(instructions).strip()
        if joined:
            claude_md_path = os.path.join(workspace_dir, CLAUDE_MD_FILENAME)
            with open(claude_md_path, "w", encoding="utf-8") as f:
                f.write(joined)
        yield workspace_dir
    finally:
        shutil.rmtree(workspace_dir, ignore_errors=True)
```

- [ ] **Step 4: テストを実行してPASSを確認**

```bash
cd backend
poetry run pytest tests/test_claude_teams/test_workspace.py -v
```

Expected: 全4テストPASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/claude_teams/workspace.py backend/tests/test_claude_teams/test_workspace.py
git commit -m "feat: add per-turn temporary workspace with CLAUDE.md for Claude Code CLI"
```

---

## Task 12: エラー分類ヘルパー

**Files:**
- Create: `backend/app/claude_teams/errors.py`
- Test: `backend/tests/test_claude_teams/test_errors.py`

**Interfaces:**
- Produces:
  - `class ClaudeTeamsAllTokensUnavailableError(Exception)`: 全トークン利用不可時にraiseする
  - `class ClaudeTeamsExecutionError(Exception)`: その他実行時エラー（フォールバックせずユーザーにそのまま表示するエラー）
  - `classify_api_retry_error(error_kind: str) -> Literal["cooldown", "disable", "passthrough"]`: `system/api_retry`イベントの`error`フィールド文字列を受け取り、3分類のどれかを返す

- [ ] **Step 1: テストを書く**

`backend/tests/test_claude_teams/test_errors.py`:

```python
import sys
import unittest

sys.path.insert(0, ".")
from app.claude_teams.errors import classify_api_retry_error


class TestClassifyApiRetryError(unittest.TestCase):
    def test_rate_limit_and_billing_error_classify_as_cooldown(self):
        self.assertEqual(classify_api_retry_error("rate_limit"), "cooldown")
        self.assertEqual(classify_api_retry_error("billing_error"), "cooldown")

    def test_auth_errors_classify_as_disable(self):
        self.assertEqual(classify_api_retry_error("authentication_failed"), "disable")
        self.assertEqual(classify_api_retry_error("oauth_org_not_allowed"), "disable")

    def test_other_errors_classify_as_passthrough(self):
        for kind in [
            "overloaded",
            "invalid_request",
            "model_not_found",
            "server_error",
            "max_output_tokens",
            "unknown",
            "some_future_error_type",
        ]:
            self.assertEqual(classify_api_retry_error(kind), "passthrough")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストを実行して失敗を確認**

```bash
cd backend
poetry run pytest tests/test_claude_teams/test_errors.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.claude_teams.errors'`

- [ ] **Step 3: errors.pyを実装**

`backend/app/claude_teams/errors.py`:

```python
"""Error classification for Claude Code CLI / claude-agent-sdk failures,
driving the token pool's cooldown/disable/passthrough behavior.

See `system/api_retry` event `error` field values documented at
https://code.claude.com/docs/en/headless#handle-api-retries.
"""

from typing import Literal

type_error_action = Literal["cooldown", "disable", "passthrough"]

_COOLDOWN_ERRORS = {"rate_limit", "billing_error"}
_DISABLE_ERRORS = {"authentication_failed", "oauth_org_not_allowed"}


class ClaudeTeamsAllTokensUnavailableError(Exception):
    """Raised when no Claude Teams OAuth token is currently available
    (all are disabled or cooling down)."""


class ClaudeTeamsExecutionError(Exception):
    """Raised for a Claude Code CLI failure that should be surfaced to the
    user as-is for this turn, without cooldown/disable or fallback."""


def classify_api_retry_error(error_kind: str) -> type_error_action:
    """Classify a `system/api_retry` event's `error` field into one of:

    - "cooldown": the token's flat-rate quota is exhausted for now; put it
      in cooldown and try the next token.
    - "disable": the token itself is invalid/revoked; disable it
      permanently and try the next token.
    - "passthrough": some other failure (overload, bad request, etc); do
      not touch the token, surface the error for this turn only.
    """
    if error_kind in _COOLDOWN_ERRORS:
        return "cooldown"
    if error_kind in _DISABLE_ERRORS:
        return "disable"
    return "passthrough"
```

- [ ] **Step 4: テストを実行してPASSを確認**

```bash
cd backend
poetry run pytest tests/test_claude_teams/test_errors.py -v
```

Expected: 全3テストPASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/claude_teams/errors.py backend/tests/test_claude_teams/test_errors.py
git commit -m "feat: add Claude Code CLI error classification for token cooldown/disable"
```

---

## Task 13: 実行エンジン本体 `converse_with_claude_teams`

**Files:**
- Create: `backend/app/claude_teams/chat.py`
- Test: `backend/tests/test_claude_teams/test_chat.py`

**Interfaces:**
- Consumes:
  - `pick_next_available_token()`, `set_cooldown()`, `disable_token()` (Task 7)
  - `get_claude_teams_token()` (Task 8)
  - `build_claude_teams_mcp_servers()`, `list_allowed_tool_names()` (Task 10)
  - `claude_teams_workspace()` (Task 11)
  - `classify_api_retry_error()`, `ClaudeTeamsAllTokensUnavailableError`, `ClaudeTeamsExecutionError` (Task 12)
  - `get_claude_teams_native_model_id()` (Task 3)
  - `OnStopInput` (`app/stream.py`)
  - `SimpleMessageModel`, `TextContentModel` (`app/repositories/models/conversation.py`)
- Produces: `def converse_with_claude_teams(bot, chat_input, instructions, messages, on_stream=None, on_thinking=None, on_tool_result=None, on_reasoning=None) -> OnStopInput` — `converse_with_strands`（`app/strands_integration/chat_strands.py`）と同じシグネチャ形状で、`usecases/chat.py`から呼べる形にする。`claude_agent_sdk.query()`を非同期で呼ぶため、内部で`asyncio.run()`を使う。

- [ ] **Step 1: テストを書く**

`backend/tests/test_claude_teams/test_chat.py`:

```python
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, ".")
from app.claude_teams.chat import converse_with_claude_teams
from app.claude_teams.errors import ClaudeTeamsAllTokensUnavailableError
from app.claude_teams.token_repository import ClaudeTeamsTokenItem
from app.repositories.models.conversation import SimpleMessageModel, TextContentModel
from app.routes.schemas.conversation import ChatInput, MessageInput


def _make_chat_input(model="claude-teams-sonnet"):
    return ChatInput(
        conversation_id="conv-1",
        message=MessageInput(
            role="user",
            content=[{"content_type": "text", "body": "hello"}],
            model=model,
            parent_message_id=None,
        ),
    )


def _make_messages():
    return [
        SimpleMessageModel(
            role="user",
            content=[TextContentModel(content_type="text", body="hello")],
        )
    ]


class TestConverseWithClaudeTeams(unittest.TestCase):
    @patch("app.claude_teams.chat.pick_next_available_token")
    def test_raises_when_no_token_available(self, mock_pick):
        mock_pick.return_value = None

        with self.assertRaises(ClaudeTeamsAllTokensUnavailableError):
            converse_with_claude_teams(
                bot=None,
                chat_input=_make_chat_input(),
                instructions=[],
                messages=_make_messages(),
            )

    @patch("app.claude_teams.chat._run_claude_query")
    @patch("app.claude_teams.chat.pick_next_available_token")
    @patch("app.claude_teams.chat.get_claude_teams_token", return_value="sk-oauth-abc")
    def test_returns_on_stop_input_with_zero_price(
        self, mock_get_token, mock_pick, mock_run_query
    ):
        mock_pick.return_value = ClaudeTeamsTokenItem(
            token_id="tok-1", display_name="a", enabled=True, created_at=1
        )
        mock_run_query.return_value = {
            "text": "Hi there!",
            "input_tokens": 10,
            "output_tokens": 5,
        }

        result = converse_with_claude_teams(
            bot=None,
            chat_input=_make_chat_input(),
            instructions=[],
            messages=_make_messages(),
        )

        self.assertEqual(result["price"], 0.0)
        self.assertEqual(result["input_token_count"], 10)
        self.assertEqual(result["output_token_count"], 5)
        self.assertEqual(result["message"].content[0].body, "Hi there!")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストを実行して失敗を確認**

```bash
cd backend
poetry run pytest tests/test_claude_teams/test_chat.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.claude_teams.chat'`

- [ ] **Step 3: chat.pyを実装**

`backend/app/claude_teams/chat.py`:

```python
"""Chat execution engine for `claude-teams-*` models: runs the real Claude
Code CLI binary (via `claude-agent-sdk`) authenticated with a
`CLAUDE_CODE_OAUTH_TOKEN` drawn from the token pool, instead of talking to
Amazon Bedrock.
"""

import asyncio
import logging
import time
from typing import Callable, TypedDict

from app.agents.tools.agent_tool import ToolRunResult
from app.claude_teams.errors import (
    ClaudeTeamsAllTokensUnavailableError,
    ClaudeTeamsExecutionError,
    classify_api_retry_error,
)
from app.claude_teams.mcp_bridge import build_claude_teams_mcp_servers
from app.claude_teams.models import get_claude_teams_native_model_id
from app.claude_teams.token_repository import disable_token, pick_next_available_token, set_cooldown
from app.claude_teams.token_secrets import get_claude_teams_token
from app.claude_teams.workspace import claude_teams_workspace
from app.repositories.models.conversation import SimpleMessageModel, TextContentModel
from app.repositories.models.custom_bot import BotModel
from app.routes.schemas.conversation import ChatInput
from app.stream import OnStopInput, OnThinking
from app.utils import get_current_time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Tools the CLI subprocess must never be allowed to use for a chat request:
# local filesystem/shell/network primitives, as opposed to the in-process
# MCP tools bridged in via `build_claude_teams_mcp_servers`.
DISALLOWED_LOCAL_TOOLS = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
]

# How long a token stays in cooldown after a rate_limit/billing_error
# response, before it's tried again.
COOLDOWN_SECONDS = 5 * 60


class _RunResult(TypedDict):
    text: str
    input_tokens: int
    output_tokens: int


def _simple_messages_to_prompt(messages: list[SimpleMessageModel]) -> str:
    """Flatten the conversation history into a single prompt string.

    Each turn is rendered as "User: ..." / "Assistant: ..." so the CLI sees
    the full conversation on every call (the CLI session itself is
    disposable — no --resume/session_store is used, per the architecture
    doc). Only text content is included; other content types are not
    supported for Claude Teams chats in this version.
    """
    lines: list[str] = []
    for message in messages:
        role_label = "User" if message.role == "user" else "Assistant"
        text_parts = [
            content.body
            for content in message.content
            if isinstance(content, TextContentModel)
        ]
        if text_parts:
            lines.append(f"{role_label}: {' '.join(text_parts)}")
    return "\n\n".join(lines)


async def _run_claude_query(
    prompt: str,
    model_id: str,
    oauth_token: str,
    workspace_dir: str,
    mcp_servers: dict,
    allowed_tools: list[str],
    on_stream: Callable[[str], None] | None,
    on_reasoning: Callable[[str], None] | None,
) -> _RunResult:
    """Run one Claude Code CLI query via claude-agent-sdk and collect the
    final text + token usage. Raises `ClaudeTeamsExecutionError` for
    passthrough failures, or a tuple-carrying exception classified by the
    caller for cooldown/disable-worthy failures (see `_ApiRetryFailure`)."""
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

    options = ClaudeAgentOptions(
        model=model_id,
        cwd=workspace_dir,
        env={"CLAUDE_CODE_OAUTH_TOKEN": oauth_token, "CLAUDE_CONFIG_DIR": workspace_dir},
        disallowed_tools=DISALLOWED_LOCAL_TOOLS,
        mcp_servers=mcp_servers,
        allowed_tools=allowed_tools,
    )

    text_parts: list[str] = []
    input_tokens = 0
    output_tokens = 0
    last_error_kind: str | None = None

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                    if on_stream:
                        on_stream(block.text)
        elif type(message).__name__ == "SystemMessage" and getattr(
            message, "subtype", None
        ) == "api_retry":
            last_error_kind = getattr(message, "data", {}).get("error", "unknown")
        elif isinstance(message, ResultMessage):
            usage = getattr(message, "usage", None) or {}
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            if message.subtype != "success":
                if last_error_kind:
                    raise _ApiRetryFailure(last_error_kind)
                raise ClaudeTeamsExecutionError(
                    f"Claude Code CLI run failed: subtype={message.subtype}"
                )

    return _RunResult(
        text="".join(text_parts), input_tokens=input_tokens, output_tokens=output_tokens
    )


class _ApiRetryFailure(Exception):
    """Internal-only: wraps a classified `system/api_retry` error kind so
    `converse_with_claude_teams` can act on the token pool before
    re-raising (or falling back) for the caller."""

    def __init__(self, error_kind: str):
        super().__init__(error_kind)
        self.error_kind = error_kind


def converse_with_claude_teams(
    bot: BotModel | None,
    chat_input: ChatInput,
    instructions: list[str],
    messages: list[SimpleMessageModel],
    on_stream: Callable[[str], None] | None = None,
    on_thinking: Callable[[OnThinking], None] | None = None,
    on_tool_result: Callable[[ToolRunResult], None] | None = None,
    on_reasoning: Callable[[str], None] | None = None,
) -> OnStopInput:
    """Chat with a `claude-teams-*` model via the Claude Code CLI, retrying
    across the token pool on cooldown/disable-worthy failures. Raises
    `ClaudeTeamsAllTokensUnavailableError` if no token is available at all,
    or if every available token fails with a cooldown/disable-worthy error
    in turn."""
    model_id = get_claude_teams_native_model_id(chat_input.message.model)
    prompt = _simple_messages_to_prompt(messages)

    tried_token_ids: set[str] = set()

    while True:
        token = pick_next_available_token()
        if token is None or token.token_id in tried_token_ids:
            raise ClaudeTeamsAllTokensUnavailableError(
                "No Claude Teams OAuth token is currently available."
            )
        tried_token_ids.add(token.token_id)
        oauth_token = get_claude_teams_token(token.token_id)

        with claude_teams_workspace(instructions) as workspace_dir:
            mcp_servers, allowed_tools, mcp_cleanup = build_claude_teams_mcp_servers(
                bot=bot, model_name=chat_input.message.model
            )
            try:
                run_result = asyncio.run(
                    _run_claude_query(
                        prompt=prompt,
                        model_id=model_id,
                        oauth_token=oauth_token,
                        workspace_dir=workspace_dir,
                        mcp_servers=mcp_servers,
                        allowed_tools=allowed_tools,
                        on_stream=on_stream,
                        on_reasoning=on_reasoning,
                    )
                )
            except _ApiRetryFailure as failure:
                action = classify_api_retry_error(failure.error_kind)
                if action == "cooldown":
                    set_cooldown(
                        token.token_id,
                        cooldown_until_epoch_seconds=int(time.time()) + COOLDOWN_SECONDS,
                    )
                    continue
                elif action == "disable":
                    disable_token(token.token_id)
                    continue
                else:
                    raise ClaudeTeamsExecutionError(
                        f"Claude Code CLI error: {failure.error_kind}"
                    ) from failure
            finally:
                mcp_cleanup.close()

        break

    message = _build_message_model(run_result["text"], chat_input.message.model)

    return OnStopInput(
        message=message,
        stop_reason="end_turn",
        input_token_count=run_result["input_tokens"],
        output_token_count=run_result["output_tokens"],
        cache_read_input_count=0,
        cache_write_input_count=0,
        price=0.0,
    )


def _build_message_model(text: str, model_name):
    from app.repositories.models.conversation import MessageModel

    return MessageModel(
        role="assistant",
        content=[TextContentModel(content_type="text", body=text)],
        model=model_name,
        children=[],
        parent=None,
        create_time=get_current_time() / 1000.0,
    )
```

- [ ] **Step 4: テストを実行してPASSを確認**

```bash
cd backend
poetry run pytest tests/test_claude_teams/test_chat.py -v
```

Expected: 全2テストPASS（`_run_claude_query`をモックしているため`claude_agent_sdk`の実際の起動は発生しない）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/claude_teams/chat.py backend/tests/test_claude_teams/test_chat.py
git commit -m "feat: add converse_with_claude_teams execution engine"
```

---

## Task 14: `usecases/chat.py` への第3ルート配線

**Files:**
- Modify: `backend/app/usecases/chat.py:212-382`
- Test: `backend/tests/test_usecases/test_chat.py` (既存テストが壊れていないことを確認。専用の分岐テストを追加)

**Interfaces:**
- Consumes: `is_claude_teams_model()` (Task 3), `converse_with_claude_teams()` (Task 13)
- Produces: `chat()`関数が`chat_input.message.model`がTeams系のとき`converse_with_claude_teams`に分岐する

- [ ] **Step 1: chat.pyのモデル分岐部分を編集**

`backend/app/usecases/chat.py`の328-367行目（既存の`use_strands`分岐ブロック）を以下に置き換える:

```python
    """
    Routes to Claude Teams CLI, Strands, or legacy implementation based on
    the selected model / USE_STRANDS environment variable.
    """
    import os

    from app.claude_teams.models import is_claude_teams_model

    if is_claude_teams_model(chat_input.message.model):
        from app.claude_teams.chat import converse_with_claude_teams

        result = converse_with_claude_teams(
            bot=bot,
            chat_input=chat_input,
            instructions=instructions,
            messages=messages,
            on_stream=on_stream,
            on_thinking=on_thinking,
            on_tool_result=on_tool_run_result,
            on_reasoning=on_reasoning,
        )

    else:
        use_strands = os.environ.get("USE_STRANDS", "true").lower() == "true"

        if use_strands:
            from app.strands_integration.chat_strands import converse_with_strands

            result = converse_with_strands(
                bot=bot,
                chat_input=chat_input,
                instructions=instructions,
                generation_params=generation_params,
                guardrail=guardrail,
                display_citation=display_citation,
                messages=messages,
                search_results=search_results,
                on_stream=on_stream,
                on_thinking=on_thinking,
                on_tool_result=on_tool_run_result,
                on_reasoning=on_reasoning,
            )

        else:
            result = converse_legacy(
                bot=bot,
                chat_input=chat_input,
                instructions=instructions,
                generation_params=generation_params,
                guardrail=guardrail,
                display_citation=display_citation,
                messages=messages,
                search_results=search_results,
                on_stream=on_stream,
                on_thinking=on_thinking,
                on_tool_result=on_tool_run_result,
                on_reasoning=on_reasoning,
            )
```

- [ ] **Step 2: 既存のchat.pyテストが壊れていないことを確認**

```bash
cd backend
poetry run pytest tests/test_usecases/test_chat.py -v
```

Expected: 全テストPASS（既存のstrands/legacy分岐は変更していないため）。

- [ ] **Step 3: 分岐が正しくClaude Teamsルートに入ることの単体テストを追加**

`backend/tests/test_usecases/test_chat.py`の末尾に追記（既存importに追記が必要な場合は追加する）:

```python
class TestChatRoutesToClaudeTeams(unittest.TestCase):
    @patch("app.claude_teams.chat.converse_with_claude_teams")
    @patch("app.usecases.chat.prepare_conversation")
    @patch("app.usecases.chat.post_process_result")
    def test_chat_routes_claude_teams_model_to_converse_with_claude_teams(
        self, mock_post_process, mock_prepare_conversation, mock_converse_teams
    ):
        from app.repositories.models.conversation import (
            ConversationModel,
            MessageModel,
            SimpleMessageModel,
            TextContentModel,
        )
        from app.routes.schemas.conversation import ChatInput, MessageInput
        from app.stream import OnStopInput
        from app.usecases.chat import chat
        from app.user import User

        user_msg = MessageModel(
            role="user",
            content=[TextContentModel(content_type="text", body="hi")],
            model="claude-teams-sonnet",
            children=[],
            parent=None,
            create_time=0,
        )
        conversation = ConversationModel(
            id="conv-1",
            title="t",
            create_time=0,
            message_map={"user-1": user_msg},
            last_message_id="user-1",
            total_price=0,
            should_continue=False,
        )
        mock_prepare_conversation.return_value = ("user-1", conversation, None)
        mock_converse_teams.return_value = OnStopInput(
            message=MessageModel(
                role="assistant",
                content=[TextContentModel(content_type="text", body="hello back")],
                model="claude-teams-sonnet",
                children=[],
                parent=None,
                create_time=0,
            ),
            stop_reason="end_turn",
            input_token_count=1,
            output_token_count=1,
            cache_read_input_count=0,
            cache_write_input_count=0,
            price=0.0,
        )
        mock_post_process.return_value = (conversation, mock_converse_teams.return_value["message"])

        chat_input = ChatInput(
            conversation_id="conv-1",
            message=MessageInput(
                role="user",
                content=[{"content_type": "text", "body": "hi"}],
                model="claude-teams-sonnet",
                parent_message_id=None,
            ),
        )
        user = User(id="user-1", name="user-1", email="user@example.com", groups=[])

        chat(user=user, chat_input=chat_input)

        mock_converse_teams.assert_called_once()
```

注: 既存の`ConversationModel`/`MessageModel`の必須フィールドはこのファイル冒頭のimportとフィクスチャに既存パターンがあるため、実行前に既存テストファイルの`ConversationModel`/`MessageModel`生成箇所を確認し、フィールド名の不一致があれば実際の定義に合わせて調整する。

- [ ] **Step 4: 新規テストを実行してPASSを確認**

```bash
cd backend
poetry run pytest tests/test_usecases/test_chat.py -v -k ClaudeTeams
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/usecases/chat.py backend/tests/test_usecases/test_chat.py
git commit -m "feat: route claude-teams-* models to the Claude Code CLI execution engine"
```

---

## Task 15: エラーのHTTP応答マッピング

**Files:**
- Modify: `backend/app/main.py:1-31, 94-104`
- Test: `backend/tests/test_main.py` (存在しなければ新規作成、既存パターンに合わせる)

**Interfaces:**
- Consumes: `ClaudeTeamsAllTokensUnavailableError`, `ClaudeTeamsExecutionError` (Task 12)
- Produces: `ClaudeTeamsAllTokensUnavailableError`→HTTP 429、`ClaudeTeamsExecutionError`→HTTP 502 のエラーハンドラ登録

- [ ] **Step 1: main.pyにimportとハンドラ登録を追加**

`backend/app/main.py`の`from app.routes.user import router as user_router`の直後に追記:

```python
from app.claude_teams.errors import (
    ClaudeTeamsAllTokensUnavailableError,
    ClaudeTeamsExecutionError,
)
```

`app.add_exception_handler(RateLimitExceededError, error_handler_factory(429))`の直後に追記:

```python
app.add_exception_handler(ClaudeTeamsAllTokensUnavailableError, error_handler_factory(429))
app.add_exception_handler(ClaudeTeamsExecutionError, error_handler_factory(502))
```

- [ ] **Step 2: バックエンド起動確認（importエラーがないことの確認）**

```bash
cd backend
poetry run python -c "from app.main import app; print('ok')"
```

Expected: `ok` が出力される（起動時例外なし）。

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: map Claude Teams errors to HTTP 429/502 responses"
```

---

## Task 16: `usage_ledger` への記録配線確認

**Files:**
- Modify: `backend/app/usecases/chat.py` (Task 14で変更した箇所と同一ファイル、追加変更なし。確認のみ)

**Interfaces:**
- 確認事項: `post_process_result`（`chat.py:514-600`）は`result["price"]`（常に`0.0`）を使って`record_usage(user.rate_limit_id, result["price"])`を呼ぶ。この経路は`converse_with_claude_teams`が返す`OnStopInput`にも既存のまま適用されるため、**コード変更は不要**。

- [ ] **Step 1: post_process_resultがモデル種別に関わらず同じパスを通ることを確認するテストを書く**

`backend/tests/test_usecases/test_chat.py`に追記:

```python
class TestPostProcessResultRecordsZeroPriceForClaudeTeams(unittest.TestCase):
    @patch("app.usecases.chat.record_usage")
    @patch("app.usecases.chat.store_conversation")
    @patch("app.usecases.chat.modify_bot_last_used_time")
    @patch("app.usecases.chat.modify_bot_stats")
    def test_post_process_result_records_zero_price(
        self, mock_stats, mock_last_used, mock_store_conv, mock_record_usage
    ):
        from app.repositories.models.conversation import (
            ConversationModel,
            MessageModel,
            TextContentModel,
        )
        from app.routes.schemas.conversation import ChatInput, MessageInput
        from app.stream import OnStopInput
        from app.usecases.chat import post_process_result
        from app.user import User

        user_msg = MessageModel(
            role="user",
            content=[TextContentModel(content_type="text", body="hi")],
            model="claude-teams-sonnet",
            children=[],
            parent=None,
            create_time=0,
        )
        conversation = ConversationModel(
            id="conv-1",
            title="t",
            create_time=0,
            message_map={"user-1": user_msg},
            last_message_id="user-1",
            total_price=0,
            should_continue=False,
        )
        result = OnStopInput(
            message=MessageModel(
                role="assistant",
                content=[TextContentModel(content_type="text", body="hello")],
                model="claude-teams-sonnet",
                children=[],
                parent=None,
                create_time=0,
            ),
            stop_reason="end_turn",
            input_token_count=5,
            output_token_count=3,
            cache_read_input_count=0,
            cache_write_input_count=0,
            price=0.0,
        )
        chat_input = ChatInput(
            conversation_id="conv-1",
            message=MessageInput(
                role="user",
                content=[{"content_type": "text", "body": "hi"}],
                model="claude-teams-sonnet",
                parent_message_id=None,
            ),
        )
        user = User(id="user-1", name="user-1", email="user@example.com", groups=[])

        post_process_result(
            result=result,
            message_for_continue_generate=None,
            conversation=conversation,
            user_msg_id="user-1",
            bot=None,
            user=user,
            chat_input=chat_input,
            search_results=[],
            related_documents=[],
        )

        mock_record_usage.assert_called_once_with(user.rate_limit_id, 0.0)
```

注: `ConversationModel`/`MessageModel`必須フィールドの実際のシグネチャに合わせて微調整すること（既存の`tests/test_usecases/test_chat.py`内の同種フィクスチャ生成箇所を参照）。

- [ ] **Step 2: テストを実行してPASSを確認**

```bash
cd backend
poetry run pytest tests/test_usecases/test_chat.py -v -k ZeroPrice
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_usecases/test_chat.py
git commit -m "test: verify usage_ledger records zero price for claude-teams chats"
```

---

## Task 17: Lambdaランタイム設定調整（`/tmp`確保）

**Files:**
- Modify: `cdk/lib/constructs/api.ts:281-351`

**Interfaces:**
- Produces: `HandlerV2` Lambdaの`ephemeralStorageSize`が明示的に設定される（デフォルト512MBのままで十分か確認し、必要なら増量）

- [ ] **Step 1: PythonFunctionの定義に`ephemeralStorageSize`を追加**

`cdk/lib/constructs/api.ts`の`const handler = new PythonFunction(this, "HandlerV2", {`ブロック内、`timeout: Duration.minutes(15),`の直後に追記:

```typescript
      ephemeralStorage: Size.mebibytes(1024),
```

ファイル冒頭のimportに`Size`を追加（既存の`import { CfnOutput, CfnResource, Duration } from "aws-cdk-lib";`を編集）:

```typescript
import { CfnOutput, CfnResource, Duration, Size } from "aws-cdk-lib";
```

- [ ] **Step 2: CDK synthを確認**

```bash
cd cdk
npx cdk synth BedrockChatStack > /dev/null
```

Expected: エラーなく完了する。

- [ ] **Step 3: Commit**

```bash
git add cdk/lib/constructs/api.ts
git commit -m "feat: increase Lambda ephemeral storage for Claude Code CLI temp workspaces"
```

---

## Task 18: フロントエンド — モデル定義追加（`claude-teams-*`）

**Files:**
- Modify: `frontend/src/constants/index.ts:84-119`
- Modify: `frontend/src/hooks/useModel.ts:189-217`
- Modify: `frontend/src/i18n/en/index.ts` (model section)
- Modify: `frontend/src/i18n/ja/index.ts` (model section)

**Interfaces:**
- Produces: `AVAILABLE_MODEL_KEYS`に4種追加。`useModel`の`availableModels`に4種の`ModelItem`追加（`supportMediaType: CLAUDE_SUPPORTED_MEDIA_TYPES`, `supportReasoning: true`）。i18nキー`model.claude-teams-opus`等を英語・日本語で追加

- [ ] **Step 1: constants/index.tsに追記**

`frontend/src/constants/index.ts`の`'claude-v3-opus',`の直後（97行目）に追記:

```typescript
  // Claude Teams plan (via Claude Code CLI OAuth token pool)
  'claude-teams-opus',
  'claude-teams-sonnet',
  'claude-teams-haiku',
  'claude-teams-fable',
```

- [ ] **Step 2: useModel.tsのavailableModelsに追記**

`frontend/src/hooks/useModel.ts`の`'claude-v3-opus'`のModelItemブロック(210-216行目)の直後に追記:

```typescript
      // Claude Teams plan (via Claude Code CLI OAuth token pool)
      {
        modelId: 'claude-teams-opus',
        label: t('model.claude-teams-opus.label'),
        description: t('model.claude-teams-opus.description'),
        supportMediaType: CLAUDE_SUPPORTED_MEDIA_TYPES,
        supportReasoning: true,
      },
      {
        modelId: 'claude-teams-sonnet',
        label: t('model.claude-teams-sonnet.label'),
        description: t('model.claude-teams-sonnet.description'),
        supportMediaType: CLAUDE_SUPPORTED_MEDIA_TYPES,
        supportReasoning: true,
      },
      {
        modelId: 'claude-teams-haiku',
        label: t('model.claude-teams-haiku.label'),
        description: t('model.claude-teams-haiku.description'),
        supportMediaType: CLAUDE_SUPPORTED_MEDIA_TYPES,
        supportReasoning: true,
      },
      {
        modelId: 'claude-teams-fable',
        label: t('model.claude-teams-fable.label'),
        description: t('model.claude-teams-fable.description'),
        supportMediaType: CLAUDE_SUPPORTED_MEDIA_TYPES,
        supportReasoning: true,
      },
```

- [ ] **Step 3: 英語i18nに追記**

`frontend/src/i18n/en/index.ts`の`model`セクション内、`claude-v3-opus`のエントリの直後に追記（既存フォーマットに合わせてラベルに「(Teams Plan)」を付記):

```typescript
      'claude-teams-opus': {
        label: 'Claude Opus 5 (Teams Plan)',
        description:
          'Claude Opus 5 running through the Claude Code CLI, billed against a pooled Claude Teams/Pro/Max flat-rate quota instead of Bedrock on-demand pricing.',
      },
      'claude-teams-sonnet': {
        label: 'Claude Sonnet 5 (Teams Plan)',
        description:
          'Claude Sonnet 5 running through the Claude Code CLI, billed against a pooled Claude Teams/Pro/Max flat-rate quota instead of Bedrock on-demand pricing.',
      },
      'claude-teams-haiku': {
        label: 'Claude Haiku 4.5 (Teams Plan)',
        description:
          'Claude Haiku 4.5 running through the Claude Code CLI, billed against a pooled Claude Teams/Pro/Max flat-rate quota instead of Bedrock on-demand pricing.',
      },
      'claude-teams-fable': {
        label: 'Claude Fable 5 (Teams Plan)',
        description:
          'Claude Fable 5 running through the Claude Code CLI, billed against a pooled Claude Teams/Pro/Max flat-rate quota instead of Bedrock on-demand pricing.',
      },
```

- [ ] **Step 4: 日本語i18nに追記**

`frontend/src/i18n/ja/index.ts`の同じ位置に、日本語版として追記:

```typescript
      'claude-teams-opus': {
        label: 'Claude Opus 5（Teamsプラン経由）',
        description:
          'Claude Code CLI経由で動作するClaude Opus 5。Bedrockの従量課金ではなく、Claude Teams/Pro/Maxプランの定額枠（プール管理）を消費します。',
      },
      'claude-teams-sonnet': {
        label: 'Claude Sonnet 5（Teamsプラン経由）',
        description:
          'Claude Code CLI経由で動作するClaude Sonnet 5。Bedrockの従量課金ではなく、Claude Teams/Pro/Maxプランの定額枠（プール管理）を消費します。',
      },
      'claude-teams-haiku': {
        label: 'Claude Haiku 4.5（Teamsプラン経由）',
        description:
          'Claude Code CLI経由で動作するClaude Haiku 4.5。Bedrockの従量課金ではなく、Claude Teams/Pro/Maxプランの定額枠（プール管理）を消費します。',
      },
      'claude-teams-fable': {
        label: 'Claude Fable 5（Teamsプラン経由）',
        description:
          'Claude Code CLI経由で動作するClaude Fable 5。Bedrockの従量課金ではなく、Claude Teams/Pro/Maxプランの定額枠（プール管理）を消費します。',
      },
```

- [ ] **Step 5: フロントエンドの型チェックとビルドを確認**

```bash
cd frontend
npm run lint
npm run build
```

Expected: エラーなく完了する。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/constants/index.ts frontend/src/hooks/useModel.ts frontend/src/i18n/en/index.ts frontend/src/i18n/ja/index.ts
git commit -m "feat: add Claude Teams plan models to the frontend model picker"
```

---

## Task 19: フロントエンド — 料金表示を `$0.00` 固定にする

**Files:**
- Modify: `frontend/src/utils/PriceUtils.ts` (もし存在すれば。存在しない場合は該当の価格フォーマット関数を検索して特定)
- Test: 該当ユーティリティの既存テストファイル

**Interfaces:**
- 前提調査: このタスク実行前に、実装者は以下を実行して価格表示コンポーネント/フォーマット関数を特定すること:

```bash
cd frontend
grep -rn "totalPrice\|formatUSD\|message.price" src/components src/utils src/hooks | grep -v test
```

- 特定した関数（例: `formatUSD(price: number): string`）に対して、`claude-teams-*`モデルのメッセージは`price`がバックエンドから既に`0.0`で返ってくるため（Task 13/14で保証済み)、**フロントエンド側の追加分岐は不要**であることを確認する。`$0.00`は既存のフォーマット関数にそのまま`0.0`を渡せば自然に表示されるため、Task 18のバックエンド変更だけで表示要件を満たす。

- [ ] **Step 1: 既存の価格フォーマット関数とその呼び出し箇所を確認**

```bash
cd frontend
grep -rn "totalPrice\|formatUSD" src/components src/hooks 2>&1 | head -30
```

- [ ] **Step 2: `formatUSD(0)`相当の出力を確認するテスト（既存テストファイルがあれば実行、なければユーティリティ関数を直接確認）**

```bash
cd frontend
npm test -- --run src/utils/__tests__/ 2>&1 | tail -30
```

Expected: 既存テストがPASSし、`0`入力に対して`$0.00`形式の文字列が返ることを確認する（既存実装で対応済みのはず。対応していない場合のみ、次のステップで修正する）。

- [ ] **Step 3 (該当関数が0を正しく$0.00表示しない場合のみ): 修正**

該当関数の実装を確認し、`0`が特別扱いされて空文字や非表示になっていないかを確認する。もし問題があれば、`price === 0`のケースを明示的に`'$0.00'`を返すよう修正する。問題がなければこのステップは不要。

- [ ] **Step 4: Commit（変更があった場合のみ）**

```bash
git add frontend/src/utils/PriceUtils.ts
git commit -m "fix: ensure zero-price messages display as \$0.00"
```

---

## Task 20: 統合確認（E2E相当のバックエンド結線テスト）

**Files:**
- Test: `backend/tests/test_claude_teams/test_integration.py`

**Interfaces:**
- 目的: Task 1〜17で作った全モジュールが正しくimportでき、`chat()`のエントリポイントから`claude-teams-sonnet`を選ぶと`converse_with_claude_teams`まで到達することを、モックを使わない範囲（外部I/Oのみモック）で確認する

- [ ] **Step 1: 統合テストを書く**

`backend/tests/test_claude_teams/test_integration.py`:

```python
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")


class TestClaudeTeamsModuleWiring(unittest.TestCase):
    def test_all_claude_teams_modules_import_cleanly(self):
        import app.claude_teams.chat  # noqa: F401
        import app.claude_teams.errors  # noqa: F401
        import app.claude_teams.mcp_bridge  # noqa: F401
        import app.claude_teams.models  # noqa: F401
        import app.claude_teams.token_repository  # noqa: F401
        import app.claude_teams.token_secrets  # noqa: F401
        import app.claude_teams.workspace  # noqa: F401
        import app.usecases.claude_teams_admin  # noqa: F401

    def test_type_model_name_includes_all_teams_models(self):
        from typing import get_args
        from app.routes.schemas.conversation import type_model_name

        names = get_args(type_model_name)
        for expected in [
            "claude-teams-opus",
            "claude-teams-sonnet",
            "claude-teams-haiku",
            "claude-teams-fable",
        ]:
            self.assertIn(expected, names)

    def test_is_claude_teams_model_matches_type_model_name_entries(self):
        from typing import get_args
        from app.claude_teams.models import CLAUDE_TEAMS_MODEL_IDS
        from app.routes.schemas.conversation import type_model_name

        names = set(get_args(type_model_name))
        for teams_model in CLAUDE_TEAMS_MODEL_IDS:
            self.assertIn(teams_model, names)

    @patch("app.claude_teams.chat.converse_with_claude_teams")
    def test_usecases_chat_module_imports_claude_teams_chat_lazily(
        self, mock_converse
    ):
        # Importing app.usecases.chat must not fail even though it lazily
        # imports app.claude_teams.chat / app.claude_teams.models inside
        # chat().
        import app.usecases.chat  # noqa: F401


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストを実行してPASSを確認**

```bash
cd backend
poetry run pytest tests/test_claude_teams/test_integration.py -v
```

Expected: 全4テストPASS

- [ ] **Step 3: バックエンド全テストスイートを実行し、regressionがないことを確認**

```bash
cd backend
poetry run pytest -v 2>&1 | tail -60
```

Expected: 既存テストを含め全てPASS（既存のBedrock/strands経路には変更を加えていないため、回帰は発生しない想定）。

- [ ] **Step 4: mypyの型チェックを実行**

```bash
cd backend
poetry run mypy app/claude_teams --config-file mypy.ini
```

Expected: エラーなし、または既存コードベースのmypy運用方針（`mypy.ini`の設定）に沿った結果。エラーが出た場合は型アノテーションを修正する。

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_claude_teams/test_integration.py
git commit -m "test: add integration wiring checks for Claude Teams feature"
```

---

## Task 21: ドキュメント更新

**Files:**
- Create: `docs/CLAUDE_TEAMS_OAUTH.md`

**Interfaces:**
- 目的: `docs/CLAUDE_CODE_COST_SYNC.md`と同様のスタイルで、機能の概要・管理者向け設定手順・既知の制約を記述する

- [ ] **Step 1: ドキュメントを作成**

`docs/CLAUDE_TEAMS_OAUTH.md`:

```markdown
# Claude Teams OAuth Token Chat

## Overview

This feature lets Bedrock Chat users select dedicated "(Teams Plan)" model
variants (`claude-teams-opus`, `claude-teams-sonnet`, `claude-teams-haiku`,
`claude-teams-fable`) that route chat requests through the real Claude Code
CLI (via the `claude-agent-sdk` Python package) instead of Amazon Bedrock.
Requests are billed against a pool of organization-registered Claude
Teams/Pro/Max plan OAuth tokens (`CLAUDE_CODE_OAUTH_TOKEN`), consuming their
flat-rate quota rather than Bedrock on-demand pricing.

This is designed to avoid impersonating the Claude Code CLI (a documented
Anthropic ToS concern with directly forging its system prompt against the
plain Anthropic API): the actual Claude Code CLI binary, bundled inside the
`claude-agent-sdk` pip package as a native executable, is spawned as a
subprocess for every chat turn.

## Setup

1. Generate one or more Claude Code OAuth tokens (Claude Pro/Max/Teams
   plan): run `claude setup-token` on a machine with an authenticated
   Claude Code CLI session, or extract a long-lived token via your
   organization's existing token-issuing process.
2. As an Admin, go to the "Claude Teams Tokens" admin page (or call
   `POST /admin/claude-teams-tokens` directly) and register each token with
   a display name. The token string is written to Secrets Manager
   (`claude-teams-token/{token_id}`) and is never shown again by any UI or
   API response.
3. Register as many tokens as you have available Teams/Pro/Max seats
   willing to share their quota. Requests round-robin across all enabled,
   not-cooling-down tokens.
4. Users select a "(Teams Plan)" model from the model picker to route their
   chat through this pool.

## Behavior

- **Token selection**: round robin across enabled, non-cooling-down
  tokens (least-recently-used first).
- **Rate limit hit** (`rate_limit`/`billing_error`): the token is put in a
  5-minute cooldown and the next token is tried.
- **Invalid/revoked token** (`authentication_failed`/`oauth_org_not_allowed`):
  the token is permanently disabled (visible in the admin list) and the
  next token is tried.
- **All tokens unavailable**: the chat request fails with an HTTP 429 and a
  user-facing error message. There is no automatic fallback to a
  Bedrock-backed model.
- **Bot instructions**: passed via a `CLAUDE.md` file in a fresh temporary
  working directory per chat turn (a documented Claude Code memory
  feature), not via a forged system prompt.
- **Bot tools** (built-in tools and configured external MCP servers,
  including OAuth-authenticated ones): bridged into an in-process MCP
  server the CLI subprocess can call. Local filesystem/shell tools
  (Bash, Read, Write, Edit, Glob, Grep, WebFetch, WebSearch) are always
  disallowed for the CLI subprocess.
- **Cost display**: these chats always show `$0.00` — they don't consume
  Bedrock on-demand budget. Token usage is still recorded to the usage
  ledger (for future reporting) but never counts against the existing
  5-hour/7-day USD rate limits.

## Known limitations

- Conversation history is replayed as a flattened prompt on every turn;
  the CLI's own session (`--resume`) is not used, since Lambda's execution
  environment doesn't persist local disk across invocations reliably.
- External MCP servers using OAuth are connected through bedrock-chat's
  existing strands-based MCP client for the duration of one chat turn, not
  natively by the CLI subprocess.
- Token string rotation is delete-and-re-register only; there is no
  in-place secret update via the admin UI.
```

- [ ] **Step 2: Commit**

```bash
git add docs/CLAUDE_TEAMS_OAUTH.md
git commit -m "docs: add Claude Teams OAuth token feature documentation"
```

---

## Task 22 (フロントエンド Admin UI, 任意の後続タスクとして分離可能): Admin画面でのトークン管理

このタスクはバックエンドAPI（Task 9）が完成していれば独立して実装できる。範囲が広いため、別セッション・別実装者に引き渡す場合は本タスクのみを渡しても成立する。

**Files:**
- Create: `frontend/src/pages/AdminClaudeTeamsTokensPage.tsx`
- Create: `frontend/src/hooks/useClaudeTeamsTokens.ts`
- Create: `frontend/src/@types/claude-teams.d.ts`
- Modify: `frontend/src/routes.tsx` (ルート追加)
- Modify: `frontend/src/i18n/en/index.ts`, `frontend/src/i18n/ja/index.ts` (admin.claudeTeamsTokens系キー追加)

**Interfaces:**
- Consumes: `POST/GET/PATCH/DELETE /admin/claude-teams-tokens*` (Task 9)
- Produces: 一覧表示＋新規登録フォーム＋有効/無効切替＋削除ボタンを持つAdminページ

- [ ] **Step 1: 型定義を作成**

`frontend/src/@types/claude-teams.d.ts`:

```typescript
export type ClaudeTeamsToken = {
  tokenId: string;
  displayName: string;
  enabled: boolean;
  isCoolingDown: boolean;
  createdAt: number;
  lastUsedAt: number | null;
};

export type CreateClaudeTeamsTokenRequest = {
  displayName: string;
  tokenValue: string;
};

export type UpdateClaudeTeamsTokenRequest = {
  enabled?: boolean;
};
```

- [ ] **Step 2: フックを作成**

`frontend/src/hooks/useClaudeTeamsTokens.ts`:

```typescript
import useSWR from 'swr';
import {
  ClaudeTeamsToken,
  CreateClaudeTeamsTokenRequest,
  UpdateClaudeTeamsTokenRequest,
} from '../@types/claude-teams';
import useHttp from './useHttp';

const useClaudeTeamsTokens = () => {
  const http = useHttp();
  const { data, isLoading, mutate } = http.get<ClaudeTeamsToken[]>(
    '/admin/claude-teams-tokens'
  );

  const createToken = async (req: CreateClaudeTeamsTokenRequest) => {
    await http.post<ClaudeTeamsToken>('/admin/claude-teams-tokens', req);
    await mutate();
  };

  const updateToken = async (
    tokenId: string,
    req: UpdateClaudeTeamsTokenRequest
  ) => {
    await http.patch<null>(`/admin/claude-teams-tokens/${tokenId}`, req);
    await mutate();
  };

  const deleteToken = async (tokenId: string) => {
    await http.delete<null>(`/admin/claude-teams-tokens/${tokenId}`);
    await mutate();
  };

  return {
    tokens: data,
    isLoading,
    createToken,
    updateToken,
    deleteToken,
  };
};

export default useClaudeTeamsTokens;
```

- [ ] **Step 3: ページコンポーネントを作成**

`frontend/src/pages/AdminClaudeTeamsTokensPage.tsx`:

```tsx
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import ListPageLayout from '../layouts/ListPageLayout';
import Button from '../components/Button';
import InputText from '../components/InputText';
import Switch from '../components/Switch';
import useClaudeTeamsTokens from '../hooks/useClaudeTeamsTokens';

const AdminClaudeTeamsTokensPage: React.FC = () => {
  const { t } = useTranslation();
  const { tokens, isLoading, createToken, updateToken, deleteToken } =
    useClaudeTeamsTokens();
  const [displayName, setDisplayName] = useState('');
  const [tokenValue, setTokenValue] = useState('');

  const onSubmit = async () => {
    if (!displayName || !tokenValue) {
      return;
    }
    await createToken({ displayName, tokenValue });
    setDisplayName('');
    setTokenValue('');
  };

  return (
    <ListPageLayout
      pageTitle={t('admin.claudeTeamsTokens.label.pageTitle')}
      isLoading={isLoading}
      isEmpty={tokens?.length === 0}
      emptyMessage={t('admin.claudeTeamsTokens.label.noTokens')}>
      <div className="mb-4 flex flex-col gap-2 rounded border p-4">
        <InputText
          label={t('admin.claudeTeamsTokens.label.displayName')}
          value={displayName}
          onChange={setDisplayName}
        />
        <InputText
          label={t('admin.claudeTeamsTokens.label.tokenValue')}
          value={tokenValue}
          onChange={setTokenValue}
          type="password"
        />
        <Button onClick={onSubmit}>
          {t('admin.claudeTeamsTokens.button.register')}
        </Button>
      </div>

      {tokens?.map((token) => (
        <div
          key={token.tokenId}
          className="flex items-center justify-between border-b p-2">
          <div>
            <div className="font-bold">{token.displayName}</div>
            <div className="text-xs">
              {token.isCoolingDown &&
                t('admin.claudeTeamsTokens.label.coolingDown')}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Switch
              checked={token.enabled}
              onSwitch={(checked) =>
                updateToken(token.tokenId, { enabled: checked })
              }
            />
            <Button
              outlined
              onClick={() => deleteToken(token.tokenId)}>
              {t('admin.claudeTeamsTokens.button.delete')}
            </Button>
          </div>
        </div>
      ))}
    </ListPageLayout>
  );
};

export default AdminClaudeTeamsTokensPage;
```

注: `InputText`/`Switch`/`Button`コンポーネントの実際のprops名は既存コンポーネント実装（`frontend/src/components/`）を確認し、この計画作成時点の推定と異なる場合は実際のシグネチャに合わせて調整すること。

- [ ] **Step 4: ルートを追加**

`frontend/src/routes.tsx`の既存admin routesパターンに合わせて`/admin/claude-teams-tokens`を追加する。既存の`/admin/api-management`等のルート定義箇所を確認し、同じ形式で追記する。

- [ ] **Step 5: i18nキーを追加**

`frontend/src/i18n/en/index.ts`の`admin`セクションに追記:

```typescript
      claudeTeamsTokens: {
        label: {
          pageTitle: 'Claude Teams Tokens',
          noTokens: 'No tokens registered.',
          displayName: 'Display Name',
          tokenValue: 'OAuth Token',
          coolingDown: 'Cooling down',
        },
        button: {
          register: 'Register',
          delete: 'Delete',
        },
      },
```

`frontend/src/i18n/ja/index.ts`の同位置に日本語版を追記:

```typescript
      claudeTeamsTokens: {
        label: {
          pageTitle: 'Claude Teamsトークン',
          noTokens: '登録されたトークンがありません。',
          displayName: '表示名',
          tokenValue: 'OAuthトークン',
          coolingDown: 'クールダウン中',
        },
        button: {
          register: '登録',
          delete: '削除',
        },
      },
```

- [ ] **Step 6: フロントエンドのビルドを確認**

```bash
cd frontend
npm run lint
npm run build
```

Expected: エラーなく完了する。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/AdminClaudeTeamsTokensPage.tsx frontend/src/hooks/useClaudeTeamsTokens.ts frontend/src/@types/claude-teams.d.ts frontend/src/routes.tsx frontend/src/i18n/en/index.ts frontend/src/i18n/ja/index.ts
git commit -m "feat: add admin UI for managing Claude Teams OAuth tokens"
```
