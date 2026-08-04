# ボット単位のデフォルトモデル指定 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ボット設定画面で「デフォルトモデル」を明示的に指定できるようにし、チャット画面上部のモデル切替UIの初期表示に反映させる。

**Architecture:** バックエンドは `BotModel`/`BotAliasModel`（および対応するAPIスキーマ）に `default_model` フィールドを追加し、`ActiveModelsModel` と突き合わせて「指定モデルが無効化されていれば有効モデルの先頭に自動補正する」ロジックを `resolve_default_model()` ヘルパーとして一箇所に実装する。このヘルパーは (1) `BotModel` の `model_validator(mode="after")` から呼ばれ、あらゆる構築経路（新規作成・DynamoDB読み込み・直接構築）で一貫して適用され、(2) ボット更新ユースケース（`modify_owned_bot`）からも呼ばれ、DB永続化とAPIレスポンスの整合性を保つ。フロントエンドは同じ優先順位ロジックを `resolveDefaultModel()` という純粋関数として切り出し、チャット画面の初期モデル選択ロジック（`useModel.ts`）とボット設定画面のUI（`BotKbEditPage.tsx`）の両方から使う。

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2（backend）、React / TypeScript / vitest（frontend）

## Global Constraints

- `default_model` は**常に必須**フィールドとする（バックエンド・フロントエンドとも nullable にしない）。
- 既存ボット（DynamoDBに `DefaultModel` 属性が無いもの）は、読み込み時にその場で「有効モデルの先頭」を補完する。事前の一括マイグレーションは行わない。
- `default_model` が指す値が `active_models` 上で無効化されている場合は、保存・読み込みいずれの経路でも「有効モデルのうち定義順で先頭のもの」に自動的に読み替える。エラーは返さない。
- ボット設定画面でデフォルトモデルをトグルOFFしようとした場合、保存をブロックせず、その場で自動的に他の有効モデルへ付け替える。
- i18n の翻訳キーは `en` と `ja` にのみ追加する（`fallbackLng: 'en'` により他言語は自動フォールバックされる）。
- 共有ボット（`BotAliasModel`）にも `active_models` と同様に `default_model` を持たせ、共有経由のチャットでも機能させる。

---

### Task 1: `BotModel` / `BotAliasModel` / APIスキーマに `default_model` を追加する

**Files:**
- Modify: `backend/app/routes/schemas/bot.py:275,311,481,508,547`
- Modify: `backend/app/repositories/models/custom_bot.py:34,56-70,515,588,656-749,751-827,829-874,876-900,918,920-938,940-961,963-998,1000-1025`
- Modify: `backend/tests/test_repositories/utils/bot_factory.py:31-166`（`BotModel` を直接構築しているため、必須フィールド追加で壊れる）
- Modify: `backend/tests/test_agent/test_tools/test_knowledge.py:19-66`（同上）
- Modify: `backend/tests/test_strands_integration/test_bedrock_agent.py:190-245`（同上）
- Modify: `backend/tests/test_strands_integration/test_mcp_tools.py:102-141`（同上）
- Test: `backend/tests/test_repositories/test_models/test_bot.py`

**Interfaces:**
- Produces: `resolve_default_model(default_model: str, active_models: ActiveModelsModel) -> str`（`app.repositories.models.custom_bot` からエクスポート、Task 3 が使用）。`BotModel.default_model: type_model_name`、`BotAliasModel.default_model: type_model_name`。スキーマ `BotInput.default_model` / `BotModifyInput.default_model` / `BotOutput.default_model` / `BotModifyOutput.default_model` / `BotSummaryOutput.default_model`（すべて `type_model_name`、必須）。
- Consumes: 既存の `type_model_name`（`app.routes.schemas.conversation`）、`ActiveModelsModel`、`get_args`。

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_repositories/test_models/test_bot.py` の先頭 import に `resolve_default_model` を追加し、ファイル末尾（`if __name__ == "__main__":` の直前）に以下のテストクラスを追加する:

```python
from app.repositories.models.custom_bot import (
    ActiveModelsModel,
    AgentModel,
    BotModel,
    ConversationQuickStarterModel,
    GenerationParamsModel,
    KnowledgeModel,
    PlainToolModel,
    ReasoningParamsModel,
    ToolModel,
    UsageStatsModel,
    resolve_default_model,
)
```

（既存の `from app.repositories.models.custom_bot import (...)` に `resolve_default_model` を追加するだけでよい）

```python
class TestResolveDefaultModel(unittest.TestCase):
    def test_returns_default_model_when_active(self):
        active_models = ActiveModelsModel(
            claude_v3_5_sonnet=True, claude_v3_haiku=False
        )
        self.assertEqual(
            resolve_default_model("claude-v3.5-sonnet", active_models),
            "claude-v3.5-sonnet",
        )

    def test_falls_back_to_active_model_when_default_is_inactive(self):
        fields = {name: False for name in ActiveModelsModel.model_fields}
        fields["amazon_nova_lite"] = True
        active_models = ActiveModelsModel.model_validate(fields)
        self.assertEqual(
            resolve_default_model("claude-v3-haiku", active_models),
            "amazon-nova-lite",
        )

    def test_falls_back_to_first_in_definition_order_when_multiple_active(self):
        fields = {name: False for name in ActiveModelsModel.model_fields}
        fields["claude_v3_haiku"] = True
        fields["amazon_nova_lite"] = True
        active_models = ActiveModelsModel.model_validate(fields)
        # claude-v3-haiku precedes amazon-nova-lite in type_model_name's definition order
        self.assertEqual(
            resolve_default_model("mistral-large", active_models),
            "claude-v3-haiku",
        )


class TestBotModelDefaultModel(unittest.TestCase):
    def _make_bot_kwargs(self, **overrides):
        base = dict(
            id="test",
            title="test",
            description="test",
            instruction="instruction",
            create_time=1627984879.9,
            last_used_time=1627984879.9,
            shared_scope="private",
            shared_status="unshared",
            allowed_cognito_groups=[],
            allowed_cognito_users=[],
            is_starred=False,
            owner_user_id="owner",
            generation_params=GenerationParamsModel(
                max_tokens=2000,
                top_k=250,
                top_p=0.999,
                temperature=0.6,
                stop_sequences=["Human: ", "Assistant: "],
                reasoning_params=ReasoningParamsModel(budget_tokens=1024),
            ),
            agent=AgentModel(tools=[]),
            knowledge=KnowledgeModel(
                source_urls=[], sitemap_urls=[], filenames=[], s3_urls=[]
            ),
            prompt_caching_enabled=False,
            sync_status="RUNNING",
            sync_status_reason="reason",
            sync_last_exec_id="",
            published_api_stack_name=None,
            published_api_datetime=None,
            published_api_codebuild_id=None,
            display_retrieved_chunks=True,
            conversation_quick_starters=[],
            bedrock_knowledge_base=None,
            bedrock_guardrails=None,
            usage_stats=UsageStatsModel(usage_count=0),
        )
        base.update(overrides)
        return base

    def test_default_model_is_kept_when_active(self):
        bot = BotModel(
            **self._make_bot_kwargs(
                active_models=ActiveModelsModel(),
                default_model="claude-v3.5-sonnet",
            )
        )
        self.assertEqual(bot.default_model, "claude-v3.5-sonnet")

    def test_default_model_is_corrected_when_inactive(self):
        fields = {name: False for name in ActiveModelsModel.model_fields}
        fields["amazon_nova_lite"] = True
        bot = BotModel(
            **self._make_bot_kwargs(
                active_models=ActiveModelsModel.model_validate(fields),
                default_model="claude-v3.5-sonnet",
            )
        )
        self.assertEqual(bot.default_model, "amazon-nova-lite")


if __name__ == "__main__":
    unittest.main()
```

（末尾の `if __name__ == "__main__":\n    unittest.main()` は既存のものを置き換える形になる。新規クラスはその直前に挿入する）

- [ ] **Step 2: テストを実行して失敗を確認する**

Run（`backend/` ディレクトリで実行）: `poetry run pytest tests/test_repositories/test_models/test_bot.py -v`

Expected: FAIL（`resolve_default_model` がまだ存在しないため、ファイル先頭の import 文で `ImportError` が発生し、ファイル全体が collection error になる）

- [ ] **Step 3: 最小限の実装を行う**

`backend/app/repositories/models/custom_bot.py:34` の import に `type_model_name` はすでにある。`_create_model_activate_model` の直後（56-70行目）に以下の2関数を追加する:

```python
def _create_model_activate_model(model_names: List[str]) -> Type[DynamicBaseModel]:
    fields: Dict[str, Any] = {
        name.replace("-", "_").replace(".", "_"): (bool, True) for name in model_names
    }
    return create_model("ActiveModelsModel", __base__=DynamicBaseModel, **fields)


ActiveModelsModel: Type[BaseModel] = _create_model_activate_model(
    list(get_args(type_model_name))
)


default_active_models = ActiveModelsModel.model_validate(
    {field_name: True for field_name in ActiveModelsModel.model_fields.keys()}
)


def _first_active_model_name(active_models: "ActiveModelsModel") -> str:  # type: ignore
    """Return the first model name (in `type_model_name` definition order) that is active."""
    model_names = get_args(type_model_name)
    for model_name in model_names:
        field_name = model_name.replace("-", "_").replace(".", "_")
        if getattr(active_models, field_name, False):
            return model_name
    # No active models at all; fall back to the first defined model as an ultimate safeguard.
    return model_names[0]


def resolve_default_model(default_model: str, active_models: "ActiveModelsModel") -> str:  # type: ignore
    """Return `default_model` if it's active, otherwise the first active model."""
    field_name = default_model.replace("-", "_").replace(".", "_")
    if getattr(active_models, field_name, False):
        return default_model
    return _first_active_model_name(active_models)
```

`BotModel` に `default_model` フィールドを追加する（515行目、`active_models` の直後）:

```python
    active_models: ActiveModelsModel  # type: ignore
    default_model: type_model_name
    usage_stats: UsageStatsModel
```

`validate_guardrails`（580-588行目）の直後に、自動補正用の `model_validator` を追加する:

```python
    @model_validator(mode="after")
    def validate_guardrails(self) -> Self:
        if self.bedrock_guardrails is not None:
            if not self.bedrock_guardrails.is_guardrail_enabled:
                # Clear Guardrail ARN if the Guardrail is not needed.
                self.bedrock_guardrails.guardrail_arn = ""
                self.bedrock_guardrails.guardrail_version = ""

        return self

    @model_validator(mode="after")
    def validate_default_model(self) -> Self:
        self.default_model = resolve_default_model(
            self.default_model, self.active_models  # type: ignore
        )
        return self
```

`from_input`（656-749行目）で `active_models` をローカル変数として計算してから使うよう変更する。`agent = AgentModel.from_agent_input(...)` の直後に追加:

```python
        agent = AgentModel.from_agent_input(
            agent_input=bot_input.agent if bot_input.agent else None,
            user_id=owner_user_id,
            bot_id=bot_input.id,
        )

        active_models = ActiveModelsModel.model_validate(
            bot_input.active_models.model_dump()  # type: ignore
        )

        sync_status: type_sync_status = (
```

そして `return cls(...)` 内の該当箇所（745-748行目）を以下に変更する:

```python
            active_models=active_models,
            default_model=bot_input.default_model,
            usage_stats=UsageStatsModel(usage_count=0),
        )
```

`from_dynamo_item`（751-827行目）を、`active_models` をローカル変数として先に計算するよう変更する。メソッド本体の冒頭（`return BotModel(` の直前）に追加:

```python
    @classmethod
    def from_dynamo_item(cls, item: dict) -> Self:
        active_models = (
            ActiveModelsModel.model_validate(item.get("ActiveModels"))
            if item.get("ActiveModels")
            else default_active_models  # for backward compatibility
        )
        return BotModel(
            id=item["BotId"],
```

そして、元の `active_models=(...)` ブロック（817-821行目）を以下に置き換える:

```python
            active_models=active_models,
            default_model=item.get("DefaultModel") or _first_active_model_name(active_models),
```

`to_output`（829-874行目）の `active_models=ActiveModelsOutput.model_validate(...)` の直後（873行目）に追加:

```python
            active_models=ActiveModelsOutput.model_validate(
                self.active_models.model_dump()  # type: ignore
            ),
            default_model=self.default_model,
        )
```

`to_summary_output`（876-900行目、`BotModel` 側）の末尾（897-899行目の直後）に追加:

```python
            active_models=ActiveModelsOutput.model_validate(
                self.active_models.model_dump()  # type: ignore
            ),
            default_model=self.default_model,
        )
```

`BotAliasModel`（903行目〜）に `default_model` フィールドを追加する（918行目、`active_models` の直後）:

```python
    conversation_quick_starters: list[ConversationQuickStarterModel]
    active_models: ActiveModelsModel  # type: ignore
    default_model: type_model_name
```

`from_bot_for_initial_alias`（920-938行目）の `active_models=bot.active_models,` の直後に追加:

```python
            conversation_quick_starters=bot.conversation_quick_starters,
            active_models=bot.active_models,
            default_model=bot.default_model,
        )
```

`from_existing_bot_and_alias`（940-961行目）も同様に、`active_models=bot.active_models,` の直後に追加:

```python
            conversation_quick_starters=bot.conversation_quick_starters,
            active_models=bot.active_models,
            default_model=bot.default_model,  # Update to the latest
        )
```

`BotAliasModel.from_dynamo_item`（963-998行目）の、既存の `active_models` 計算ブロックの直後に `default_model` の計算を追加する:

```python
        # Handle active models
        active_models_data = item.get("ActiveModels", {})
        active_models = (
            ActiveModelsModel.model_validate(active_models_data)
            if active_models_data
            else default_active_models
        )
        default_model = resolve_default_model(
            item.get("DefaultModel") or _first_active_model_name(active_models),
            active_models,
        )
```

そして `return cls(...)` 内の `active_models=active_models,` の直後に追加:

```python
            conversation_quick_starters=conversation_quick_starters,
            active_models=active_models,
            default_model=default_model,
        )
```

`BotAliasModel.to_summary_output`（1000-1025行目）の末尾（1022-1024行目の直後）に追加:

```python
            active_models=ActiveModelsOutput.model_validate(
                self.active_models.model_dump()  # type: ignore
            ),
            default_model=self.default_model,
        )
```

最後に、`backend/app/routes/schemas/bot.py` の5つのスキーマに `default_model: type_model_name` を追加する。`BotInput`（275行目、`active_models` の直後）:

```python
    bedrock_guardrails: BedrockGuardrailsInput | None = None
    active_models: ActiveModelsInput  # type: ignore
    default_model: type_model_name
```

`BotModifyInput`（311行目、同様）:

```python
    bedrock_guardrails: BedrockGuardrailsInput | None = None
    active_models: ActiveModelsInput  # type: ignore
    default_model: type_model_name
```

`BotModifyOutput`（481行目、同様）:

```python
    bedrock_guardrails: BedrockGuardrailsOutput | None
    active_models: ActiveModelsOutput  # type: ignore
    default_model: type_model_name
```

`BotOutput`（508行目、同様）:

```python
    bedrock_guardrails: BedrockGuardrailsOutput | None
    active_models: ActiveModelsOutput  # type: ignore
    default_model: type_model_name
```

`BotSummaryOutput`（547行目、同様）:

```python
    active_models: ActiveModelsOutput  # type: ignore
    default_model: type_model_name
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `poetry run pytest tests/test_repositories/test_models/test_bot.py -v`

Expected: `TestResolveDefaultModel` と `TestBotModelDefaultModel` の新規テストは PASS。既存の `TestBotModel` / `TestBotModelFromInput` は、`self.bot` と `self.bot_input` に `default_model`/`active_models` を必須で渡していないため FAIL する（次のステップで修正する）。

- [ ] **Step 5: 既存フィクスチャを修正する**

`TestBotModel.setUp`（73-164行目）の `active_models=ActiveModelsModel(...)` の直後に追加:

```python
            active_models=ActiveModelsModel(
                claude_v3_sonnet_v2=True,
            ),
            default_model="claude-v3.5-sonnet-v2",
            usage_stats=UsageStatsModel(usage_count=0),
        )
```

`TestBotModelFromInput.setUp`（186-209行目）の `self.bot_input = BotInput(...)` に `default_model` を追加:

```python
        self.bot_input = BotInput(
            id="test-bot",
            title="Test Bot",
            instruction="Test instruction",
            description="Test description",
            display_retrieved_chunks=True,
            active_models={field: True for field in DEFAULT_GENERATION_CONFIG},
            default_model="claude-v4-opus",
            # No generation_params provided initially
            generation_params=None,
            knowledge=Knowledge(
                source_urls=[], sitemap_urls=[], filenames=[], s3_urls=[]
            ),
            prompt_caching_enabled=False,
            conversation_quick_starters=[],
        )
```

- [ ] **Step 6: テストを再実行して全て成功することを確認する**

Run: `poetry run pytest tests/test_repositories/test_models/test_bot.py -v`

Expected: 全テスト PASS

- [ ] **Step 7: `BotModel` を直接構築している他のテストファイルを修正する**

`default_model` が `BotModel` の必須フィールドになったため、`BotModel(...)` を直接構築している以下のテストファイルは、Task 2 以降で確認するまでもなく、この時点で既に壊れている（`test_repositories/test_models/test_bot.py` 以外は本タスクではまだ実行確認していないため、次のステップで確認する）。それぞれに `default_model="claude-v4-opus",` を追加する。

`backend/tests/test_repositories/utils/bot_factory.py` の `_create_test_bot_model`（31-166行目）の関数シグネチャに `default_model` 引数を追加する。`usage_count=0,` の直後（56行目）に追加:

```python
    usage_count=0,
    default_model="claude-v4-opus",
    **kwargs
):
```

そして `return BotModel(...)` 内の `active_models=ActiveModelsModel(),` の直後（164行目）に追加:

```python
        active_models=ActiveModelsModel(),
        default_model=default_model,
        usage_stats=UsageStatsModel(usage_count=usage_count),
```

`backend/tests/test_agent/test_tools/test_knowledge.py` の `BotModel(...)` 呼び出し（19-66行目）の `active_models=ActiveModelsModel(),`（61行目）の直後に追加:

```python
            active_models=ActiveModelsModel(),
            default_model="claude-v4-opus",
            shared_scope="private",
```

`backend/tests/test_strands_integration/test_bedrock_agent.py` の `BotModel(...)` 呼び出し（190-245行目）の `active_models=ActiveModelsModel(),`（243行目）の直後に追加:

```python
            active_models=ActiveModelsModel(),
            default_model="claude-v4-opus",
            usage_stats=UsageStatsModel(usage_count=0),
        )
```

`backend/tests/test_strands_integration/test_mcp_tools.py` の `_make_bot`（102-141行目）の `active_models=ActiveModelsModel(),`（139行目）の直後に追加:

```python
        active_models=ActiveModelsModel(),
        default_model="claude-v4-opus",
        usage_stats=UsageStatsModel(usage_count=0),
    )
```

- [ ] **Step 8: 影響を受けるテストファイルを実行し、壊れていないことを確認する**

Run（`backend/` ディレクトリで実行）:
```bash
poetry run pytest tests/test_repositories/test_models/test_bot.py -v
poetry run pytest tests/test_agent/test_tools/test_knowledge.py -v
poetry run pytest tests/test_strands_integration/test_bedrock_agent.py -v
poetry run pytest tests/test_strands_integration/test_mcp_tools.py -v
```

Expected: 全テスト PASS（`bot_factory.py` はこの時点でまだ他のテストファイルから使われている箇所が多いため、`test_repositories/test_custom_bot.py` 等の実行確認は Task 2 で行う）

- [ ] **Step 9: コミット**

```bash
git add backend/app/repositories/models/custom_bot.py backend/app/routes/schemas/bot.py backend/tests/test_repositories/test_models/test_bot.py backend/tests/test_repositories/utils/bot_factory.py backend/tests/test_agent/test_tools/test_knowledge.py backend/tests/test_strands_integration/test_bedrock_agent.py backend/tests/test_strands_integration/test_mcp_tools.py
git commit -m "$(cat <<'EOF'
feat: add default_model field to BotModel and bot API schemas

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: リポジトリ層に `DefaultModel` の永続化を追加し、`update_bot()` 呼び出しを修正する

**Files:**
- Modify: `backend/app/repositories/custom_bot.py:1-34,49-99,102-202,205-234`
- Test: `backend/tests/test_repositories/test_custom_bot.py`

**Interfaces:**
- Consumes: `BotModel.default_model`（Task 1）、`BotAliasModel.default_model`（Task 1）。
- Produces: `store_bot()` が `DefaultModel` 属性を書き込む。`update_bot(..., default_model: str, ...)` が `DefaultModel` 属性を更新する（値の妥当性検証はしない。呼び出し側で解決済みの値を渡す前提）。`store_alias()` が `DefaultModel` 属性を書き込む。

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_repositories/test_custom_bot.py` の `test_update_bot`（305-384行目付近）内、`update_bot(...)` 呼び出しの `conversation_quick_starters=[...]` の直後に `active_models`/`default_model` を追加する:

```python
            conversation_quick_starters=[
                ConversationQuickStarterModel(title="QS title", example="QS example")
            ],
            bedrock_knowledge_base=BedrockKnowledgeBaseModel(
```

（この部分は変更しない。その少し下、既存の `active_models=ActiveModelsModel(),` を以下に置き換える）

```python
            active_models=ActiveModelsModel(),
            default_model="amazon-nova-lite",
        )

        bot = find_bot_by_id("1")
        self.assertEqual(bot.title, "Updated Title")
```

（`bot = find_bot_by_id("1")` 以降の既存アサーションはそのまま残す）

さらに、同じテストメソッドの末尾（既存アサーションの最後、`delete_bot_by_id` の直前）に以下を追加する:

```python
        self.assertEqual(bot.default_model, "amazon-nova-lite")
```

加えて、ファイル内の `test_update_bot` の直後に新規テストメソッドを追加する（自動整合ロジックの確認）:

```python
    def test_update_bot_corrects_inactive_default_model(self):
        bot = create_test_private_bot("1b", False, "user1")
        store_bot(bot)

        inactive_amazon_nova_lite = ActiveModelsModel.model_validate(
            {**ActiveModelsModel().model_dump(), "amazon_nova_lite": False}
        )
        update_bot(
            "user1",
            "1b",
            title=bot.title,
            description=bot.description,
            instruction=bot.instruction,
            generation_params=bot.generation_params,
            agent=bot.agent,
            knowledge=bot.knowledge,
            prompt_caching_enabled=bot.prompt_caching_enabled,
            sync_status=bot.sync_status,
            sync_status_reason=bot.sync_status_reason,
            display_retrieved_chunks=bot.display_retrieved_chunks,
            conversation_quick_starters=bot.conversation_quick_starters,
            active_models=inactive_amazon_nova_lite,
            default_model="amazon-nova-lite",
        )

        updated_bot = find_bot_by_id("1b")
        self.assertEqual(updated_bot.default_model, "amazon-nova-lite")
        delete_bot_by_id("user1", "1b")
```

（このテストは `update_bot()` リポジトリ関数が `default_model` をそのまま保存し、読み込み時に `BotModel` の `model_validator`（Task 1）が自動補正することを確認する。リポジトリ関数自体は補正しない設計であるため、この時点ではまだ動作しない）

- [ ] **Step 2: テストを実行して失敗を確認する**

Run（`backend/` ディレクトリで実行）: `poetry run pytest tests/test_repositories/test_custom_bot.py::TestCustomBotRepository::test_update_bot tests/test_repositories/test_custom_bot.py::TestCustomBotRepository::test_update_bot_corrects_inactive_default_model -v`

Expected: FAIL（`update_bot()` が `default_model` キーワード引数を受け付けず `TypeError`）

- [ ] **Step 3: 最小限の実装を行う**

`backend/app/repositories/custom_bot.py:27` の import に `type_model_name` を追加する:

```python
from app.routes.schemas.bot import type_shared_scope, type_sync_status
```

を

```python
from app.routes.schemas.bot import type_model_name, type_shared_scope, type_sync_status
```

に変更する。

`store_bot`（49-99行目）の item 辞書に `DefaultModel` を追加する（79行目の直後）:

```python
        "ActiveModels": custom_bot.active_models.model_dump(),  # type: ignore[attr-defined]
        "DefaultModel": custom_bot.default_model,
        "UsageStats": custom_bot.usage_stats.model_dump(),
    }
```

`update_bot`（102-202行目）の関数シグネチャに `default_model` パラメータを追加する（115行目の直後）:

```python
    active_models: ActiveModelsModel,  # type: ignore
    default_model: type_model_name,
    conversation_quick_starters: list[ConversationQuickStarterModel],
```

`update_expression`（126-139行目）に `DefaultModel` を追加する:

```python
    update_expression = (
        "SET Title = :title, "
        "Description = :description, "
        "Instruction = :instruction, "
        "AgentData = :agent_data, "
        "Knowledge = :knowledge, "
        "PromptCachingEnabled = :prompt_caching_enabled, "
        "SyncStatus = :sync_status, "
        "SyncStatusReason = :sync_status_reason, "
        "GenerationParams = :generation_params, "
        "DisplayRetrievedChunks = :display_retrieved_chunks, "
        "ConversationQuickStarters = :conversation_quick_starters, "
        "ActiveModels = :active_models, "
        "DefaultModel = :default_model"
    )
```

`expression_attribute_values`（141-156行目）に `:default_model` を追加する:

```python
        ":active_models": active_models.model_dump(),  # type: ignore[attr-defined]
        ":default_model": default_model,
    }
```

`store_alias`（205-234行目）の item 辞書に `DefaultModel` を追加する（226行目の直後）:

```python
        "ActiveModels": alias.active_models.model_dump(),  # type: ignore[attr-defined]
        "DefaultModel": alias.default_model,
    }
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `poetry run pytest tests/test_repositories/test_custom_bot.py::TestCustomBotRepository::test_update_bot tests/test_repositories/test_custom_bot.py::TestCustomBotRepository::test_update_bot_corrects_inactive_default_model -v`

Expected: PASS

- [ ] **Step 5: 影響を受ける既存テストファイルを実行し、壊れていないことを確認する**

Run: `poetry run pytest tests/test_repositories/test_custom_bot.py -v`

Expected: 全テスト PASS

- [ ] **Step 6: コミット**

```bash
git add backend/app/repositories/custom_bot.py backend/tests/test_repositories/test_custom_bot.py
git commit -m "$(cat <<'EOF'
feat: persist default_model attribute for bots and bot aliases

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `modify_owned_bot` ユースケースで自動整合ロジックを配線する

**Files:**
- Modify: `backend/app/usecases/bot.py:32-40,247-287,302-348`
- Test: `backend/tests/test_usecases/test_bot.py`（無ければ新規作成）

**Interfaces:**
- Consumes: `resolve_default_model`（Task 1）、`ActiveModelsModel`（Task 1）、`update_bot(..., default_model: str, ...)`（Task 2）。
- Produces: `modify_owned_bot()` が返す `BotModifyOutput.default_model` と、実際にDynamoDBへ永続化される値が常に一致することを保証する。

- [ ] **Step 1: 既存のテストファイルを確認する**

`backend/tests/test_usecases/test_bot.py` が存在するか確認する:

Run（`backend/` ディレクトリで実行）: `ls tests/test_usecases/test_bot.py`

存在しなければ Step 2 で新規作成する。存在する場合は、既存のテストクラス構成に合わせて以下のテストメソッドを追加する。

- [ ] **Step 2: 失敗するテストを書く**

`backend/tests/test_usecases/test_bot.py` に以下の内容を追加する（ファイルが存在しない場合は新規作成する）:

```python
import sys
import unittest

sys.path.append(".")

from app.repositories.custom_bot import find_bot_by_id, store_bot
from app.routes.schemas.bot import (
    ActiveModelsInput,
    BotModifyInput,
    GenerationParams,
    KnowledgeDiffInput,
    ReasoningParams,
)
from app.usecases.bot import modify_owned_bot
from tests.test_repositories.utils.bot_factory import create_test_private_bot
from tests.test_usecases.utils.user_factory import create_test_user


class TestModifyOwnedBotDefaultModel(unittest.TestCase):
    def setUp(self):
        self.user = create_test_user("test-modify-default-model-user")
        self.bot = create_test_private_bot(
            "test-modify-default-model-bot", False, self.user.id
        )
        store_bot(self.bot)

    def _make_modify_input(self, active_models: dict, default_model: str):
        return BotModifyInput(
            title="Updated Title",
            instruction="Updated Instruction",
            description="Updated Description",
            generation_params=GenerationParams(
                max_tokens=2000,
                top_k=250,
                top_p=0.999,
                temperature=0.6,
                stop_sequences=["Human: ", "Assistant: "],
                reasoning_params=ReasoningParams(budget_tokens=1024),
            ),
            knowledge=KnowledgeDiffInput(
                source_urls=[],
                sitemap_urls=[],
                s3_urls=[],
                added_filenames=[],
                deleted_filenames=[],
                unchanged_filenames=[],
            ),
            display_retrieved_chunks=True,
            prompt_caching_enabled=False,
            conversation_quick_starters=[],
            active_models=ActiveModelsInput.model_validate(active_models),
            default_model=default_model,
        )

    def test_default_model_is_kept_when_active(self):
        modify_input = self._make_modify_input(
            {"amazon_nova_lite": True}, "amazon-nova-lite"
        )
        output = modify_owned_bot(
            self.user, "test-modify-default-model-bot", modify_input
        )
        self.assertEqual(output.default_model, "amazon-nova-lite")

        persisted = find_bot_by_id("test-modify-default-model-bot")
        self.assertEqual(persisted.default_model, "amazon-nova-lite")

    def test_default_model_is_corrected_when_inactive(self):
        modify_input = self._make_modify_input(
            {"amazon_nova_lite": False, "claude_v3_5_sonnet": True},
            "amazon-nova-lite",
        )
        output = modify_owned_bot(
            self.user, "test-modify-default-model-bot", modify_input
        )
        # amazon-nova-lite is inactive, so it must fall back to an active model
        self.assertNotEqual(output.default_model, "amazon-nova-lite")

        persisted = find_bot_by_id("test-modify-default-model-bot")
        # The API response and the persisted value must always agree.
        self.assertEqual(persisted.default_model, output.default_model)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: テストを実行して失敗を確認する**

Run（`backend/` ディレクトリで実行）: `poetry run pytest tests/test_usecases/test_bot.py -v`

Expected: FAIL（`modify_owned_bot()` 内部の `update_bot()` 呼び出しに `default_model` 引数が渡されていないため `TypeError: update_bot() missing 1 required keyword-only argument: 'default_model'`）

- [ ] **Step 4: 最小限の実装を行う**

`backend/app/usecases/bot.py:32-40` の import に `ActiveModelsModel` と `resolve_default_model` を追加する:

```python
from app.repositories.models.custom_bot import (
    ActiveModelsModel,
    AgentModel,
    BotAliasModel,
    BotModel,
    ConversationQuickStarterModel,
    GenerationParamsModel,
    KnowledgeModel,
    ReasoningParamsModel,
    resolve_default_model,
)
```

`modify_owned_bot`（163行目〜）内、`update_bot(...)` 呼び出しの直前（246行目付近、`updated_kb = current_bot_kb` の直後）に、解決済みの `default_model` を一度だけ計算するコードを追加する:

```python
    else:
        updated_kb = current_bot_kb

    resolved_default_model = resolve_default_model(
        modify_input.default_model,
        ActiveModelsModel.model_validate(
            modify_input.active_models.model_dump()  # type: ignore
        ),
    )

    update_bot(
        bot.owner_user_id,
        bot_id,
        title=modify_input.title,
```

`update_bot(...)` 呼び出しの末尾（284-286行目）を以下に変更する:

```python
        active_models=ActiveModelsOutput.model_validate(
            modify_input.active_models.model_dump()  # type: ignore
        ),
        default_model=resolved_default_model,
    )
```

`BotModifyOutput(...)` の構築（302-348行目）の末尾（345-347行目）も同様に変更する:

```python
        active_models=ActiveModelsOutput.model_validate(
            modify_input.active_models.model_dump()  # type: ignore
        ),
        default_model=resolved_default_model,
    )
```

- [ ] **Step 5: テストを実行して成功を確認する**

Run: `poetry run pytest tests/test_usecases/test_bot.py -v`

Expected: PASS

- [ ] **Step 6: コミット**

```bash
git add backend/app/usecases/bot.py backend/tests/test_usecases/test_bot.py
git commit -m "$(cat <<'EOF'
feat: resolve default_model consistently in modify_owned_bot usecase

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: フロントエンドの型定義に `defaultModel` を追加する

**Files:**
- Modify: `frontend/src/@types/bot.d.ts:82-98,100-105,114-128,132-145,147-159`

**Interfaces:**
- Produces: `BotDetails.defaultModel: Model`、`BotSummary.defaultModel: Model`、`RegisterBotRequest.defaultModel: Model`、`UpdateBotRequest.defaultModel: Model`、`UpdateBotResponse.defaultModel: Model`。Task 6/7 が使用する。

- [ ] **Step 1: 型定義を変更する**

`BotDetails`（82-98行目）の `activeModels: ActiveModels;` の直後に追加:

```typescript
export type BotDetails = Omit<BotMeta, 'isStarred' | 'owned'> & {
  instruction: string;
  allowedCognitoGroups: string[];
  allowedCognitoUsers: string[];
  ownerUserId: string;
  isPublication: boolean;
  generationParams: GenerationParams;
  agent: Agent;
  knowledge: BotKnowledge;
  promptCachingEnabled: boolean;
  syncStatusReason: string;
  displayRetrievedChunks: boolean;
  conversationQuickStarters: ConversationQuickStarter[];
  bedrockGuardrails: GuardrailsParams;
  bedrockKnowledgeBase: BedrockKnowledgeBase;
  activeModels: ActiveModels;
  defaultModel: Model;
};
```

`BotSummary`（100-105行目）:

```typescript
export type BotSummary = BotMeta & {
  hasKnowledge: boolean;
  hasAgent: boolean;
  conversationQuickStarters: ConversationQuickStarter[];
  activeModels: ActiveModels;
  defaultModel: Model;
};
```

`RegisterBotRequest`（114-128行目）:

```typescript
export type RegisterBotRequest = {
  id: string;
  title: string;
  instruction: string;
  agent: AgentInput;
  description?: string;
  generationParams?: GenerationParams;
  knowledge?: BotKnowledge;
  displayRetrievedChunks: boolean;
  promptCachingEnabled: boolean;
  conversationQuickStarters: ConversationQuickStarter[];
  bedrockGuardrails?: GuardrailsParams;
  bedrockKnowledgeBase?: BedrockKnowledgeBase;
  activeModels: ActiveModels;
  defaultModel: Model;
};
```

`UpdateBotRequest`（132-145行目）:

```typescript
export type UpdateBotRequest = {
  title: string;
  instruction: string;
  description?: string;
  agent: AgentInput;
  generationParams?: GenerationParams;
  knowledge?: BotKnowledgeDiff;
  displayRetrievedChunks: boolean;
  promptCachingEnabled: boolean;
  conversationQuickStarters: ConversationQuickStarter[];
  bedrockGuardrails?: GuardrailsParams;
  bedrockKnowledgeBase?: BedrockKnowledgeBase;
  activeModels: ActiveModels;
  defaultModel: Model;
};
```

`UpdateBotResponse`（147-159行目）:

```typescript
export type UpdateBotResponse = {
  id: string;
  title: string;
  instruction: string;
  description: string;
  generationParams: GenerationParams;
  knowledge?: BotKnowledge;
  displayRetrievedChunks: boolean;
  promptCachingEnabled: boolean;
  conversationQuickStarters: ConversationQuickStarter[];
  bedrockKnowledgeBase: BedrockKnowledgeBase;
  activeModels: ActiveModels;
  defaultModel: Model;
};
```

- [ ] **Step 2: 型チェックを実行する**

Run（`frontend/` ディレクトリで実行）: `npx tsc --noEmit`

Expected: FAIL（`RegisterBotRequest`/`UpdateBotRequest` を渡している `BotKbEditPage.tsx` の呼び出し箇所が `defaultModel` を含まず型エラーになる。Task 7 で解消する）

この時点でのエラーは想定内であるため、そのままコミットして次のタスクに進む。

- [ ] **Step 3: コミット**

```bash
git add frontend/src/@types/bot.d.ts
git commit -m "$(cat <<'EOF'
feat: add defaultModel field to bot frontend types

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: フロントエンドのデフォルトモデル優先順位ロジックを純粋関数として切り出す

**Files:**
- Create: `frontend/src/utils/ModelUtils.ts`
- Test: `frontend/src/utils/__tests__/ModelUtils.test.ts`

**Interfaces:**
- Produces: `resolveDefaultModel(candidates: (Model | undefined)[], filteredModels: ModelItem[]): Model | undefined`。Task 6 が `useModel.ts` から使用する。

- [ ] **Step 1: 失敗するテストを書く**

`frontend/src/utils/__tests__/ModelUtils.test.ts` を新規作成する:

```typescript
import { describe, expect, it } from 'vitest';
import { resolveDefaultModel } from '../ModelUtils';
import { ModelItem } from '../../@types/global-config';
import { Model } from '../../@types/conversation';

const modelItem = (modelId: Model): ModelItem => ({
  modelId,
  label: modelId,
  supportMediaType: [],
  supportReasoning: false,
});

describe('resolveDefaultModel', () => {
  const filteredModels = [
    modelItem('claude-v3.5-sonnet'),
    modelItem('amazon-nova-lite'),
  ];

  it('returns the first candidate that is in filteredModels', () => {
    expect(
      resolveDefaultModel(
        ['amazon-nova-lite', 'claude-v3.5-sonnet'],
        filteredModels
      )
    ).toBe('amazon-nova-lite');
  });

  it('falls through to the next candidate when the first is not available', () => {
    expect(
      resolveDefaultModel(['claude-v3-opus', 'claude-v3.5-sonnet'], filteredModels)
    ).toBe('claude-v3.5-sonnet');
  });

  it('falls back to the first filtered model when no candidate is available', () => {
    expect(resolveDefaultModel(['claude-v3-opus'], filteredModels)).toBe(
      'claude-v3.5-sonnet'
    );
  });

  it('returns undefined when there are no filtered models and no candidates', () => {
    expect(resolveDefaultModel([], [])).toBeUndefined();
  });
});
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run（`frontend/` ディレクトリで実行）: `npx vitest run src/utils/__tests__/ModelUtils.test.ts`

Expected: FAIL（`../ModelUtils` モジュールが存在しない）

- [ ] **Step 3: 最小限の実装を行う**

`frontend/src/utils/ModelUtils.ts` を新規作成する:

```typescript
import { Model } from '../@types/conversation';
import { ModelItem } from '../@types/global-config';

export const resolveDefaultModel = (
  candidates: (Model | undefined)[],
  filteredModels: ModelItem[]
): Model | undefined => {
  for (const candidate of candidates) {
    if (candidate && filteredModels.some((m) => m.modelId === candidate)) {
      return candidate;
    }
  }
  return filteredModels[0]?.modelId;
};
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `npx vitest run src/utils/__tests__/ModelUtils.test.ts`

Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add frontend/src/utils/ModelUtils.ts frontend/src/utils/__tests__/ModelUtils.test.ts
git commit -m "$(cat <<'EOF'
feat: extract default model resolution into a pure ModelUtils function

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: チャット画面でボットの `defaultModel` を優先させる

**Files:**
- Modify: `frontend/src/hooks/useModel.ts:1-11,56-72,355-372`
- Modify: `frontend/src/components/SwitchBedrockModel.tsx:1-21`
- Modify: `frontend/src/pages/ChatPage.tsx:588-592`

**Interfaces:**
- Consumes: `resolveDefaultModel`（Task 5）、`BotSummary.defaultModel`（Task 4）。
- Produces: `useModel(botId?, activeModels?, botDefaultModel?)` の第3引数。`SwitchBedrockModel` の `botDefaultModel?: Model` プロパティ。

- [ ] **Step 1: `useModel.ts` を変更する**

`frontend/src/hooks/useModel.ts:1-11` の import に `resolveDefaultModel` を追加する:

```typescript
import { create } from 'zustand';
import { Model } from '../@types/conversation';
import { ModelItem } from '../@types/global-config';
import { AVAILABLE_MODEL_KEYS } from '../constants/index';
import { useEffect, useMemo, useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import useLocalStorage from './useLocalStorage';
import useGlobalConfig from './useGlobalConfig';
import { ActiveModels } from '../@types/bot';
import { toCamelCase } from '../utils/StringUtils';
import { resolveDefaultModel } from '../utils/ModelUtils';
```

`useModel` 関数のシグネチャ（56行目）を変更する:

```typescript
const useModel = (
  botId?: string | null,
  activeModels?: ActiveModels,
  botDefaultModel?: Model
) => {
```

`getDefaultModel`（355-372行目）を以下に置き換える:

```typescript
  const getDefaultModel = useCallback((): Model => {
    return (
      resolveDefaultModel(
        [botDefaultModel, globalConfig?.defaultModel as Model | undefined],
        filteredModels
      ) ?? 'amazon-nova-lite'
    );
  }, [filteredModels, botDefaultModel, globalConfig?.defaultModel]);
```

- [ ] **Step 2: `SwitchBedrockModel.tsx` を変更する**

`frontend/src/components/SwitchBedrockModel.tsx:1-21` を以下に変更する:

```typescript
import { BaseProps } from '../@types/common';
import useModel from '../hooks/useModel';
import { Popover, Transition } from '@headlessui/react';
import { Fragment } from 'react/jsx-runtime';
import { useMemo, useEffect } from 'react';
import { PiCaretDown, PiCheck } from 'react-icons/pi';
import { ActiveModels } from '../@types/bot';
import { Model } from '../@types/conversation';
import { toCamelCase } from '../utils/StringUtils';

interface Props extends BaseProps {
  activeModels: ActiveModels;
  botId?: string | null;
  botDefaultModel?: Model;
}

const SwitchBedrockModel: React.FC<Props> = (props) => {
  const {
    availableModels: allModels,
    modelId,
    setModelId,
    getDefaultModel,
  } = useModel(props.botId, props.activeModels, props.botDefaultModel);
```

- [ ] **Step 3: `ChatPage.tsx` を変更する**

`frontend/src/pages/ChatPage.tsx:588-592` を以下に変更する:

```typescript
                    <SwitchBedrockModel
                      className="mb-6 mt-3 w-min"
                      activeModels={activeModels}
                      botId={botId}
                      botDefaultModel={bot?.defaultModel}
                    />
```

- [ ] **Step 4: 型チェックを実行する**

Run（`frontend/` ディレクトリで実行）: `npx tsc --noEmit`

Expected: Task 4 の Step 2 で確認した `BotKbEditPage.tsx` の型エラーのみが残り、それ以外の新規エラーは発生しない

- [ ] **Step 5: コミット**

```bash
git add frontend/src/hooks/useModel.ts frontend/src/components/SwitchBedrockModel.tsx frontend/src/pages/ChatPage.tsx
git commit -m "$(cat <<'EOF'
feat: prioritize bot's default model when selecting the initial chat model

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: ボット設定画面にデフォルトモデル選択UIを追加する

**Files:**
- Modify: `frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx:204-213,655,670-677,1399,1407-1449,1525,1534-1582,2694-2724`
- Modify: `frontend/src/i18n/en/index.ts:597-600`
- Modify: `frontend/src/i18n/ja/index.ts:603-606`

**Interfaces:**
- Consumes: `RegisterBotRequest.defaultModel`/`UpdateBotRequest.defaultModel`（Task 4）、`BotDetails.defaultModel`（Task 4）。

- [ ] **Step 1: i18n キーを追加する**

`frontend/src/i18n/ja/index.ts:603-606` の `activeModels` の直後に追加:

```typescript
      activeModels: {
        title: '利用可能なモデル設定',
        description: 'このボットで使用可能なモデルを設定します。',
      },
      defaultModel: {
        title: 'デフォルトモデル',
        description: 'チャット画面を開いたときに最初に選択されるモデルです。',
      },
```

`frontend/src/i18n/en/index.ts:597-600` の `activeModels` の直後に追加:

```typescript
      activeModels: {
        title: 'Model Activation',
        description: 'Configure which AI models can be used with this bot.',
      },
      defaultModel: {
        title: 'Default Model',
        description: 'The model selected by default when the chat screen is opened.',
      },
```

- [ ] **Step 2: `defaultModel` の state を追加する**

`frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx:204-213` の `activeModels` state定義の直後に追加する:

```typescript
  const [activeModels, setActiveModels] = useState<ActiveModels>(() => {
    const initialState = AVAILABLE_MODEL_KEYS.reduce(
      (acc: ActiveModels, key: Model) => {
        acc[toCamelCase(key) as keyof ActiveModels] = true;
        return acc;
      },
      {} as ActiveModels
    );
    return initialState;
  });

  const [defaultModel, setDefaultModel] = useState<Model>(
    AVAILABLE_MODEL_KEYS[0]
  );
```

- [ ] **Step 3: 既存ボット読み込み時に `defaultModel` を反映する**

`frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx:655` の `setActiveModels(bot.activeModels);` の直後に追加する:

```typescript
          setActiveModels(bot.activeModels);
          setDefaultModel(bot.defaultModel);
        })
```

- [ ] **Step 4: `onChangeActiveModels` に自動付け替えロジックを追加する**

`frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx:670-677` を以下に置き換える:

```typescript
  const onChangeActiveModels = useCallback(
    (key: string, value: boolean) => {
      const camelKey = toCamelCase(key) as keyof ActiveModels;
      const newActiveModels = { ...activeModels, [camelKey]: value };
      setActiveModels(newActiveModels);

      if (!value && toCamelCase(defaultModel) === camelKey) {
        const fallback = activeModelsOptions.find(
          ({ key: optionKey }) =>
            newActiveModels[toCamelCase(optionKey) as keyof ActiveModels] !==
            false
        );
        if (fallback) {
          setDefaultModel(fallback.key);
        }
      }
    },
    [activeModels, defaultModel, activeModelsOptions]
  );
```

- [ ] **Step 5: 送信ペイロードに `defaultModel` を追加する（新規作成）**

`frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx` の `onClickCreate` 内、`registerBot({...})` の `activeModels,`（1399行目）の直後に追加する:

```typescript
      activeModels,
      defaultModel,
    })
```

同じ `useCallback` の依存配列（1407-1449行目）の末尾 `activeModels,`（1448行目）の直後に追加する:

```typescript
    activeModels,
    defaultModel,
  ]);
```

- [ ] **Step 6: 送信ペイロードに `defaultModel` を追加する（更新）**

`onClickEdit` 内、`updateBot(botId, {...})` の `activeModels,`（1525行目）の直後に追加する:

```typescript
        activeModels,
        defaultModel,
      })
```

同じ `useCallback` の依存配列（1534-1582行目）の末尾 `activeModels,`（1581行目）の直後に追加する:

```typescript
    activeModels,
    defaultModel,
  ]);
```

- [ ] **Step 7: デフォルトモデル選択UIを追加する**

`frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx:2694` の `<ExpandableDrawerGroup isDefaultShow={false} label={t('bot.activeModels.title')} ...>` の直前に、以下のブロックを追加する:

```tsx
              <div className="mt-3">
                <Select
                  label={t('bot.defaultModel.title')}
                  value={defaultModel}
                  options={activeModelsOptions
                    .filter(
                      ({ key }) =>
                        activeModels[toCamelCase(key) as keyof ActiveModels] !==
                        false
                    )
                    .map(({ key, label }) => ({
                      value: key,
                      label,
                    }))}
                  onChange={(val) => {
                    setDefaultModel(val as Model);
                  }}
                />
                <div className="text-sm text-aws-font-color-light/50 dark:text-aws-font-color-dark">
                  {t('bot.defaultModel.description')}
                </div>
              </div>

              <ExpandableDrawerGroup
                isDefaultShow={false}
                label={t('bot.activeModels.title')}
                className="py-2">
```

- [ ] **Step 8: 型チェックを実行する**

Run（`frontend/` ディレクトリで実行）: `npx tsc --noEmit`

Expected: エラー無し（Task 4/6 で残っていた型エラーも含めて解消される）

- [ ] **Step 9: 開発サーバーで動作確認する**

Run: `npm run dev`（`frontend/` ディレクトリで実行）

ブラウザで以下を手動確認する:
- ボット作成画面で「デフォルトモデル」ドロップダウンが表示され、初期値が有効モデルの先頭になっていること
- 「利用可能なモデル設定」で現在のデフォルトモデルをOFFにすると、ドロップダウンの選択値が自動的に他の有効モデルに切り替わること
- ボットを保存し、チャット画面を開いたときに指定したデフォルトモデルが選択されていること
- 既存ボット（本機能リリース前に作成したもの）を開いても、デフォルトモデルドロップダウンが有効モデルのいずれかを指した状態で表示されること（エラーにならないこと）

- [ ] **Step 10: コミット**

```bash
git add frontend/src/features/knowledgeBase/pages/BotKbEditPage.tsx frontend/src/i18n/en/index.ts frontend/src/i18n/ja/index.ts
git commit -m "$(cat <<'EOF'
feat: add default model selection UI to the bot settings page

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
