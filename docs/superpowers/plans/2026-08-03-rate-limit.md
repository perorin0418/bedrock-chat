# ユーザー毎コストレートリミット Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ユーザー毎に、直近5時間の利用コストが$10を、または直近7日間の利用コストが$336を超えたら、通常のチャットUI(WebSocket / REST)からのチャットをブロックする。両閾値はSSM Parameter Store経由で再デプロイなしに変更可能にする。

**Architecture:** 新規DynamoDBテーブル`UsageLedgerTable`(PK=UserId, SK=Timestamp[epoch ms])にメッセージ毎のコストを記録し、チャット開始直前にその直近ウィンドウの合計をQueryして閾値と比較する。閾値はSSM Parameter Storeの2つのStringParameterから読み取り、Lambdaプロセス内で60秒キャッシュする。判定は`chat()`本体ではなく、通常UIの2つの呼び出し元(`routes/conversation.py`, `websocket.py`)でのみ行う。

**Tech Stack:** Python (FastAPI, boto3, pydantic), TypeScript (AWS CDK), 既存の行レベルセキュリティ方式(DynamoDB + STS AssumeRoleセッションポリシー)。

## Global Constraints

- 閾値のデフォルト値: 5時間 = $10、7日間 = $336(いずれもSSM Parameter Store、型はString、値は数値の文字列表現)。
- 判定は「超過」= 厳密に `>`(`>=` ではない)。
- SSM値のLambda内キャッシュTTLは60秒。
- `UsageLedgerTable`の各レコードのTTLは記録時刻+8日(7日間の判定ウィンドウ+予備1日)。
- 対象は通常チャットUI経由(WebSocket / REST `POST /conversation`)のみ。公開bot API(`published_api.py` / `sqs_consumer.py`)・`chat()`本体は変更しない。
- 管理者(Admin)グループも例外にしない(全ユーザー適用)。
- このリポジトリに`moto`は実際には使われていない(pyproject.tomlに依存もない)。DynamoDBのテストは`unittest.TestCase` + `patch("boto3.resource")` + `MagicMock`で行う(`backend/tests/test_repositories/test_conversation.py`を参照)。
- 時刻取得は`app.utils.get_current_time()`(epoch milliseconds, int)を使う。既存の`prepare_conversation()`と同様、呼び出し元でパラメータとして受け渡さず、必要な関数の内部で呼び出す。
- このリポジトリにはルート(FastAPI)やWebSocketハンドラそのものを対象にした自動テストは存在しない(`backend/tests/test_routes/`にはスキーマテストのみ)。新しいテストハーネスを追加で作り込むことはせず、ロジック本体(`usage_limit.py` / `rate_limit.py`)のユニットテストで担保する。ルート/ハンドラへの1行の呼び出し追加自体は自動テスト対象外(既存コードベースの慣習に合わせる)。
- CI(`​.github/workflows/backend.yml`)はmypy/black/uvicorn起動/docker buildのみでpytestは実行されない。`backend/tests/test_usecases/test_chat.py`等は実AWSリソースに依存する手動実行の統合テストであり、本計画のユニットテストとは別物。
- CDKのテストは`cdk/test/cdk.test.ts`(jest, `Template.fromStack`)に追記する。

---

### Task 1: `RateLimitExceededError`とUsage Ledgerテーブルアクセスの基盤を`repositories/common.py`に追加

**Files:**
- Modify: `backend/app/repositories/common.py`

**Interfaces:**
- Produces: `RateLimitExceededError`(例外クラス)、`get_usage_ledger_table_client(user_id: str)`(Task 2で使用)

このタスクには単体テストを作らない(定数追加とテーブルクライアント取得関数のみで、Task 2のテストが間接的に検証する)。

- [ ] **Step 1: `USAGE_LEDGER_TABLE_NAME`定数を追加**

`backend/app/repositories/common.py`の10行目付近、`BOT_TABLE_NAME`の直後に追加:

```python
CONVERSATION_TABLE_NAME = os.environ.get("CONVERSATION_TABLE_NAME", "")
BOT_TABLE_NAME = os.environ.get("BOT_TABLE_NAME", "")
USAGE_LEDGER_TABLE_NAME = os.environ.get("USAGE_LEDGER_TABLE_NAME", "")
ACCOUNT = os.environ.get("ACCOUNT", "")
```

- [ ] **Step 2: `type_table`と`_table_name_map`を更新**

既存の以下の行を:

```python
type_table = Literal["conversation", "bot"]
_table_name_map = {"conversation": CONVERSATION_TABLE_NAME, "bot": BOT_TABLE_NAME}
```

次に置き換える:

```python
type_table = Literal["conversation", "bot", "usage_ledger"]
_table_name_map = {
    "conversation": CONVERSATION_TABLE_NAME,
    "bot": BOT_TABLE_NAME,
    "usage_ledger": USAGE_LEDGER_TABLE_NAME,
}
```

- [ ] **Step 3: `RateLimitExceededError`を追加**

既存の例外クラス群の直後に追加:

```python
class ResourceConflictError(Exception):
    pass


class RateLimitExceededError(Exception):
    pass
```

- [ ] **Step 4: `get_usage_ledger_table_client`を追加**

`get_bot_table_client`関数の直後に追加:

```python
def get_usage_ledger_table_client(user_id: str):
    """Get a DynamoDB table client for usage ledger table."""
    return _get_aws_resource(
        "dynamodb", user_id=user_id, table_name=USAGE_LEDGER_TABLE_NAME
    ).Table(USAGE_LEDGER_TABLE_NAME)
```

- [ ] **Step 5: mypy/blackで構文確認**

Run: `cd backend && poetry run black --check app/repositories/common.py && poetry run mypy --config-file mypy.ini app/repositories/common.py`
Expected: 両方とも成功(exit code 0)。

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/common.py
git commit -m "feat: add RateLimitExceededError and usage ledger table client"
```

---

### Task 2: `usage_limit`リポジトリ(記録・集計)を作成

**Files:**
- Create: `backend/app/repositories/usage_limit.py`
- Test: `backend/tests/test_repositories/test_usage_limit.py`

**Interfaces:**
- Consumes: `get_usage_ledger_table_client(user_id)`(Task 1)
- Produces: `record_usage(user_id: str, price: float) -> None`、`get_usage_since(user_id: str, since_ms: int) -> float`(Task 3, Task 4で使用)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_repositories/test_usage_limit.py`を新規作成:

```python
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.repositories.usage_limit import get_usage_since, record_usage


class TestUsageLimitRepository(unittest.TestCase):
    def setUp(self):
        self.patcher1 = patch("boto3.resource")
        self.mock_boto3_resource = self.patcher1.start()

        self.mock_table = MagicMock()
        self.mock_boto3_resource.return_value.Table.return_value = self.mock_table

        os.environ["USAGE_LEDGER_TABLE_NAME"] = "test-usage-ledger-table"
        os.environ["BEDROCK_REGION"] = "us-east-1"

    def tearDown(self):
        self.patcher1.stop()
        os.environ.pop("USAGE_LEDGER_TABLE_NAME", None)
        os.environ.pop("BEDROCK_REGION", None)

    @patch("app.repositories.usage_limit.get_current_time", return_value=1_700_000_000_000)
    def test_record_usage_puts_expected_item(self, mock_get_current_time):
        record_usage(user_id="user-1", price=0.0123)

        self.mock_table.put_item.assert_called_once()
        item = self.mock_table.put_item.call_args.kwargs["Item"]
        self.assertEqual(item["PK"], "user-1")
        self.assertEqual(item["SK"], 1_700_000_000_000)
        self.assertAlmostEqual(float(item["Price"]), 0.0123)
        # TTL = now(seconds) + 8日
        self.assertEqual(item["expire"], 1_700_000_000 + 8 * 24 * 60 * 60)

    def test_get_usage_since_sums_prices_in_window(self):
        self.mock_table.query.return_value = {
            "Items": [
                {"PK": "user-1", "SK": 1000, "Price": "1.5"},
                {"PK": "user-1", "SK": 2000, "Price": "2.25"},
            ]
        }

        total = get_usage_since(user_id="user-1", since_ms=500)

        self.assertAlmostEqual(total, 3.75)
        self.mock_table.query.assert_called_once()

    def test_get_usage_since_returns_zero_when_no_items(self):
        self.mock_table.query.return_value = {"Items": []}

        total = get_usage_since(user_id="user-1", since_ms=500)

        self.assertEqual(total, 0.0)

    def test_get_usage_since_paginates_with_last_evaluated_key(self):
        self.mock_table.query.side_effect = [
            {
                "Items": [{"PK": "user-1", "SK": 1000, "Price": "1.0"}],
                "LastEvaluatedKey": {"PK": "user-1", "SK": 1000},
            },
            {"Items": [{"PK": "user-1", "SK": 2000, "Price": "2.0"}]},
        ]

        total = get_usage_since(user_id="user-1", since_ms=500)

        self.assertAlmostEqual(total, 3.0)
        self.assertEqual(self.mock_table.query.call_count, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && poetry run pytest tests/test_repositories/test_usage_limit.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'app.repositories.usage_limit'`)

- [ ] **Step 3: 実装を書く**

`backend/app/repositories/usage_limit.py`を新規作成:

```python
import logging
from decimal import Decimal as decimal

from app.repositories.common import get_usage_ledger_table_client
from app.utils import get_current_time
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

USAGE_LEDGER_TTL_SECONDS = 8 * 24 * 60 * 60  # 8 days


def record_usage(user_id: str, price: float) -> None:
    """Record a single message's cost for rate-limit accounting."""
    now_ms = get_current_time()
    table = get_usage_ledger_table_client(user_id)
    table.put_item(
        Item={
            "PK": user_id,
            "SK": now_ms,
            "Price": decimal(str(price)),
            "expire": now_ms // 1000 + USAGE_LEDGER_TTL_SECONDS,
        }
    )


def get_usage_since(user_id: str, since_ms: int) -> float:
    """Sum the recorded cost for a user from `since_ms` (epoch milliseconds) to now."""
    table = get_usage_ledger_table_client(user_id)

    query_params = {
        "KeyConditionExpression": Key("PK").eq(user_id) & Key("SK").gte(since_ms),
    }
    response = table.query(**query_params)
    total = sum(float(item["Price"]) for item in response["Items"])

    query_count = 1
    MAX_QUERY_COUNT = 5
    while "LastEvaluatedKey" in response:
        query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = table.query(**query_params)
        total += sum(float(item["Price"]) for item in response["Items"])
        query_count += 1
        if query_count > MAX_QUERY_COUNT:
            logger.warning(f"Query count exceeded {MAX_QUERY_COUNT}")
            break

    return total
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && poetry run pytest tests/test_repositories/test_usage_limit.py -v`
Expected: PASS(4 tests)

- [ ] **Step 5: mypy/black確認**

Run: `cd backend && poetry run black --check app/repositories/usage_limit.py tests/test_repositories/test_usage_limit.py && poetry run mypy --config-file mypy.ini app/repositories/usage_limit.py`
Expected: 成功

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/usage_limit.py backend/tests/test_repositories/test_usage_limit.py
git commit -m "feat: add usage ledger repository for rate-limit accounting"
```

---

### Task 3: SSM閾値取得+キャッシュ+`check_rate_limit`を作成

**Files:**
- Create: `backend/app/usecases/rate_limit.py`
- Test: `backend/tests/test_usecases/test_rate_limit.py`

**Interfaces:**
- Consumes: `get_usage_since(user_id: str, since_ms: int) -> float`(Task 2)、`RateLimitExceededError`(Task 1)、`User`(`app.user.User`、`.id: str`属性)
- Produces: `check_rate_limit(user: User) -> None`(Task 5, Task 6で使用)、内部関数`_get_limit`と`ssm_client`(テストでpatch対象、モジュールレベル)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_usecases/test_rate_limit.py`を新規作成:

```python
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")

from app.repositories.common import RateLimitExceededError
from app.usecases import rate_limit
from tests.test_usecases.utils.user_factory import create_test_user


class TestCheckRateLimit(unittest.TestCase):
    def setUp(self):
        rate_limit._limit_cache.clear()

    def test_allows_when_under_both_limits(self):
        with patch.object(
            rate_limit, "get_current_time", return_value=1_700_000_000_000
        ), patch.object(
            rate_limit, "_get_limit", side_effect=[10.0, 336.0]
        ), patch.object(
            rate_limit, "get_usage_since", side_effect=[5.0, 100.0]
        ):
            rate_limit.check_rate_limit(create_test_user("user-1"))

    def test_raises_when_five_hour_sum_exceeds_limit(self):
        with patch.object(
            rate_limit, "get_current_time", return_value=1_700_000_000_000
        ), patch.object(
            rate_limit, "_get_limit", side_effect=[10.0, 336.0]
        ), patch.object(
            rate_limit, "get_usage_since", side_effect=[10.01, 100.0]
        ):
            with self.assertRaises(RateLimitExceededError):
                rate_limit.check_rate_limit(create_test_user("user-1"))

    def test_does_not_raise_when_five_hour_sum_equals_limit(self):
        # "超過" is strictly `>`, not `>=`
        with patch.object(
            rate_limit, "get_current_time", return_value=1_700_000_000_000
        ), patch.object(
            rate_limit, "_get_limit", side_effect=[10.0, 336.0]
        ), patch.object(
            rate_limit, "get_usage_since", side_effect=[10.0, 100.0]
        ):
            rate_limit.check_rate_limit(create_test_user("user-1"))

    def test_raises_when_seven_day_sum_exceeds_limit(self):
        with patch.object(
            rate_limit, "get_current_time", return_value=1_700_000_000_000
        ), patch.object(
            rate_limit, "_get_limit", side_effect=[10.0, 336.0]
        ), patch.object(
            rate_limit, "get_usage_since", side_effect=[5.0, 336.01]
        ):
            with self.assertRaises(RateLimitExceededError):
                rate_limit.check_rate_limit(create_test_user("user-1"))

    def test_get_limit_caches_within_ttl(self):
        with patch.object(rate_limit, "ssm_client") as mock_ssm, patch(
            "time.time", side_effect=[100.0, 130.0]
        ):
            mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "10"}}

            first = rate_limit._get_limit("/test/param")
            second = rate_limit._get_limit("/test/param")

            self.assertEqual(first, 10.0)
            self.assertEqual(second, 10.0)
            mock_ssm.get_parameter.assert_called_once()

    def test_get_limit_refetches_after_ttl_expires(self):
        with patch.object(rate_limit, "ssm_client") as mock_ssm, patch(
            "time.time", side_effect=[100.0, 161.0]
        ):
            mock_ssm.get_parameter.side_effect = [
                {"Parameter": {"Value": "10"}},
                {"Parameter": {"Value": "20"}},
            ]

            first = rate_limit._get_limit("/test/param")
            second = rate_limit._get_limit("/test/param")

            self.assertEqual(first, 10.0)
            self.assertEqual(second, 20.0)
            self.assertEqual(mock_ssm.get_parameter.call_count, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && poetry run pytest tests/test_usecases/test_rate_limit.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'app.usecases.rate_limit'`)

- [ ] **Step 3: 実装を書く**

`backend/app/usecases/rate_limit.py`を新規作成:

```python
import logging
import os
import time

import boto3
from app.repositories.common import RateLimitExceededError
from app.repositories.usage_limit import get_usage_since
from app.user import User
from app.utils import get_current_time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

FIVE_HOUR_PARAM_NAME = os.environ.get("RATE_LIMIT_FIVE_HOUR_PARAM_NAME", "")
SEVEN_DAY_PARAM_NAME = os.environ.get("RATE_LIMIT_SEVEN_DAY_PARAM_NAME", "")

FIVE_HOUR_WINDOW_MS = 5 * 60 * 60 * 1000
SEVEN_DAY_WINDOW_MS = 7 * 24 * 60 * 60 * 1000

CACHE_TTL_SECONDS = 60

ssm_client = boto3.client("ssm")

_limit_cache: dict[str, tuple[float, float]] = {}


def _get_limit(param_name: str) -> float:
    cached = _limit_cache.get(param_name)
    now = time.time()
    if cached is not None and now - cached[1] < CACHE_TTL_SECONDS:
        return cached[0]

    response = ssm_client.get_parameter(Name=param_name)
    value = float(response["Parameter"]["Value"])
    _limit_cache[param_name] = (value, now)
    return value


def check_rate_limit(user: User) -> None:
    """Raise `RateLimitExceededError` if `user`'s recorded cost exceeds either
    the trailing 5-hour or trailing 7-day USD limit (values read from SSM)."""
    now_ms = get_current_time()

    five_hour_limit = _get_limit(FIVE_HOUR_PARAM_NAME)
    five_hour_sum = get_usage_since(user.id, now_ms - FIVE_HOUR_WINDOW_MS)
    if five_hour_sum > five_hour_limit:
        raise RateLimitExceededError(
            f"Rate limit exceeded: ${five_hour_sum:.2f} spent in the last 5 hours "
            f"(limit ${five_hour_limit:.2f})."
        )

    seven_day_limit = _get_limit(SEVEN_DAY_PARAM_NAME)
    seven_day_sum = get_usage_since(user.id, now_ms - SEVEN_DAY_WINDOW_MS)
    if seven_day_sum > seven_day_limit:
        raise RateLimitExceededError(
            f"Rate limit exceeded: ${seven_day_sum:.2f} spent in the last 7 days "
            f"(limit ${seven_day_limit:.2f})."
        )
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && poetry run pytest tests/test_usecases/test_rate_limit.py -v`
Expected: PASS(6 tests)

- [ ] **Step 5: mypy/black確認**

Run: `cd backend && poetry run black --check app/usecases/rate_limit.py tests/test_usecases/test_rate_limit.py && poetry run mypy --config-file mypy.ini app/usecases/rate_limit.py`
Expected: 成功

- [ ] **Step 6: Commit**

```bash
git add backend/app/usecases/rate_limit.py backend/tests/test_usecases/test_rate_limit.py
git commit -m "feat: add check_rate_limit usecase with SSM-backed cached thresholds"
```

---

### Task 4: `chat.py`の`post_process_result`に使用量記録を配線

**Files:**
- Modify: `backend/app/usecases/chat.py:36`(import追加), `backend/app/usecases/chat.py:530-531`(呼び出し追加)
- Modify (test): `backend/tests/test_usecases/test_post_process_result_price.py`

**Interfaces:**
- Consumes: `record_usage(user_id: str, price: float) -> None`(Task 2)

- [ ] **Step 1: 既存テストに`record_usage`のパッチを追加し、失敗するテストを書く**

このタスクの実装後、`post_process_result`は無条件に`record_usage`を呼ぶようになる。既存の`test_post_process_result_sets_message_price_and_accumulates_total`は`store_conversation`しかpatchしていないため、そのままでは`record_usage`が実際のDynamoDBへ接続しようとして失敗する。まず既存テストのデコレータを次のように変更する(`backend/tests/test_usecases/test_post_process_result_price.py`):

```python
    @patch("app.usecases.chat.record_usage")
    @patch("app.usecases.chat.store_conversation")
    def test_post_process_result_sets_message_price_and_accumulates_total(
        self, mock_store_conversation, mock_record_usage
    ):
```

(既存のシグネチャ`self, mock_store_conversation`に`mock_record_usage`引数を追加するだけで、メソッド本体は変更しない。)

続けて、`TestPostProcessResultPrice`クラスに、このメソッドの直後に新しいテストメソッドを追加:

```python
    @patch("app.usecases.chat.record_usage")
    @patch("app.usecases.chat.store_conversation")
    def test_post_process_result_records_usage(
        self, mock_store_conversation, mock_record_usage
    ):
        conversation = ConversationModel(
            id="conv1",
            create_time=1700000000.0,
            title="Test Conversation",
            total_price=0.0,
            message_map={
                "user1": MessageModel(
                    role="user",
                    content=[TextContentModel(content_type="text", body="hello")],
                    model=MODEL,
                    children=[],
                    parent="system",
                    create_time=1700000000.0,
                )
            },
            last_message_id="user1",
            bot_id=None,
            should_continue=False,
        )

        assistant_message = MessageModel(
            role="assistant",
            content=[TextContentModel(content_type="text", body="hi there")],
            model=MODEL,
            children=[],
            parent=None,
            create_time=1700000000.0,
        )

        result: OnStopInput = OnStopInput(
            message=assistant_message,
            stop_reason="end_turn",
            input_token_count=10,
            output_token_count=20,
            cache_read_input_count=0,
            cache_write_input_count=0,
            price=0.0042,
        )

        chat_input = ChatInput(
            conversation_id="conv1",
            message=MessageInput(
                role="user",
                content=[TextContent(content_type="text", body="hello")],
                model=MODEL,
                parent_message_id="system",
                message_id=None,
            ),
            bot_id=None,
            continue_generate=False,
            enable_reasoning=False,
        )

        post_process_result(
            result=result,
            message_for_continue_generate=None,
            conversation=conversation,
            user_msg_id="user1",
            bot=None,
            user=create_test_user("test-user-usage"),
            chat_input=chat_input,
            search_results=[],
            related_documents=[],
            on_stop=None,
        )

        mock_record_usage.assert_called_once()
        call_args = mock_record_usage.call_args.args
        self.assertEqual(call_args[0], "test-user-usage")
        self.assertAlmostEqual(call_args[1], 0.0042)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && poetry run pytest tests/test_usecases/test_post_process_result_price.py -v`
Expected: FAIL(`AttributeError` または `mock_record_usage.assert_called_once()`が呼ばれていないことによる失敗。`record_usage`が`app.usecases.chat`にまだ存在しないため`patch`が`AttributeError`で失敗する)

- [ ] **Step 3: `chat.py`に配線する**

`backend/app/usecases/chat.py:32-36`の既存インポート:

```python
from app.repositories.models.custom_bot import (
    BotAliasModel,
    BotModel,
    GenerationParamsModel,
)
```

の直後に追加:

```python
from app.repositories.usage_limit import record_usage
```

次に、`post_process_result`関数内(`backend/app/usecases/chat.py`の`conversation.total_price += result["price"]`の行)を:

```python
    message.price = result["price"]
    conversation.total_price += result["price"]
    conversation.should_continue = stop_reason == "max_tokens" and is_prefill_supported(
```

次のように変更する:

```python
    message.price = result["price"]
    conversation.total_price += result["price"]
    record_usage(user.id, result["price"])
    conversation.should_continue = stop_reason == "max_tokens" and is_prefill_supported(
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && poetry run pytest tests/test_usecases/test_post_process_result_price.py -v`
Expected: PASS(2 tests)

- [ ] **Step 5: mypy/black確認**

Run: `cd backend && poetry run black --check app/usecases/chat.py tests/test_usecases/test_post_process_result_price.py && poetry run mypy --config-file mypy.ini app/usecases/chat.py`
Expected: 成功

- [ ] **Step 6: Commit**

```bash
git add backend/app/usecases/chat.py backend/tests/test_usecases/test_post_process_result_price.py
git commit -m "feat: record per-message usage into the rate-limit ledger"
```

---

### Task 5: REST経路(`routes/conversation.py`)にレートリミット判定を配線

**Files:**
- Modify: `backend/app/routes/conversation.py`

**Interfaces:**
- Consumes: `check_rate_limit(user: User) -> None`(Task 3)

このタスクには専用の自動テストを追加しない。このリポジトリには`routes/conversation.py`を対象にしたHTTPレベルのテスト(TestClient等)が一つも存在せず、新しいテストハーネスをこの1呼び出しのためだけに作り込むのはGlobal Constraintsに反する。判定ロジック自体は Task 3 で十分にテスト済み。

- [ ] **Step 1: importを追加**

`backend/app/routes/conversation.py:23-31`の既存インポート:

```python
from app.usecases.chat import (
    chat,
    chat_output_from_message,
    fetch_conversation,
    propose_conversation_title,
    search_conversations as search_conversations_usecase,
)
from app.user import User
from fastapi import APIRouter, Request
```

を次のように変更する(`check_rate_limit`のインポートを追加):

```python
from app.usecases.chat import (
    chat,
    chat_output_from_message,
    fetch_conversation,
    propose_conversation_title,
    search_conversations as search_conversations_usecase,
)
from app.usecases.rate_limit import check_rate_limit
from app.user import User
from fastapi import APIRouter, Request
```

- [ ] **Step 2: `post_message`にチェックを追加**

既存の:

```python
@router.post("/conversation", response_model=ChatOutput)
def post_message(request: Request, chat_input: ChatInput):
    """Send chat message"""
    current_user: User = request.state.current_user

    conversation, message = chat(user=current_user, chat_input=chat_input)
    output = chat_output_from_message(conversation=conversation, message=message)
    return output
```

を次のように変更する:

```python
@router.post("/conversation", response_model=ChatOutput)
def post_message(request: Request, chat_input: ChatInput):
    """Send chat message"""
    current_user: User = request.state.current_user

    check_rate_limit(current_user)
    conversation, message = chat(user=current_user, chat_input=chat_input)
    output = chat_output_from_message(conversation=conversation, message=message)
    return output
```

- [ ] **Step 3: mypy/black確認**

Run: `cd backend && poetry run black --check app/routes/conversation.py && poetry run mypy --config-file mypy.ini app/routes/conversation.py`
Expected: 成功

- [ ] **Step 4: Commit**

```bash
git add backend/app/routes/conversation.py
git commit -m "feat: enforce rate limit on REST chat endpoint"
```

---

### Task 6: WebSocket経路(`websocket.py`)にレートリミット判定とエラーハンドリングを配線

**Files:**
- Modify: `backend/app/websocket.py`

**Interfaces:**
- Consumes: `check_rate_limit(user: User) -> None`(Task 3)、`RateLimitExceededError`(Task 1)

このタスクにも専用の自動テストを追加しない(Task 5と同じ理由。`websocket.py`にも既存の自動テストが存在しない)。

- [ ] **Step 1: importを追加**

`backend/app/websocket.py:1-19`の既存インポート:

```python
import boto3
from app.agents.tools.agent_tool import ToolRunResult
from app.auth import verify_token
from app.repositories.conversation import RecordNotFoundError
from app.routes.schemas.conversation import ChatInput
from app.stream import OnStopInput, OnThinking
from app.usecases.chat import chat
from app.user import User
from boto3.dynamodb.conditions import Attr, Key
```

を次のように変更する:

```python
import boto3
from app.agents.tools.agent_tool import ToolRunResult
from app.auth import verify_token
from app.repositories.common import RateLimitExceededError
from app.repositories.conversation import RecordNotFoundError
from app.routes.schemas.conversation import ChatInput
from app.stream import OnStopInput, OnThinking
from app.usecases.chat import chat
from app.usecases.rate_limit import check_rate_limit
from app.user import User
from boto3.dynamodb.conditions import Attr, Key
```

- [ ] **Step 2: `process_chat_input`にチェックと例外処理を追加**

既存の:

```python
def process_chat_input(
    user: User,
    chat_input: ChatInput,
    notificator: NotificationSender,
) -> dict:
    """Process chat input and send the message to the client."""
    logger.info(f"Received chat input: {chat_input}")

    try:
        chat(
            user=user,
            chat_input=chat_input,
            on_stream=lambda token: notificator.on_stream(
                token=token,
            ),
            on_stop=lambda arg: notificator.on_stop(arg=arg),
            on_thinking=lambda tool_use: notificator.on_agent_thinking(
                tool_use=tool_use,
            ),
            on_tool_result=lambda run_result: notificator.on_agent_tool_result(
                run_result=run_result
            ),
            on_reasoning=lambda token: notificator.on_reasoning(
                token=token,
            ),
        )

        return {"statusCode": 200, "body": "Message sent."}

    except RecordNotFoundError:
```

を次のように変更する(`check_rate_limit`呼び出しの追加と、新しい`except`節の追加):

```python
def process_chat_input(
    user: User,
    chat_input: ChatInput,
    notificator: NotificationSender,
) -> dict:
    """Process chat input and send the message to the client."""
    logger.info(f"Received chat input: {chat_input}")

    try:
        check_rate_limit(user)

        chat(
            user=user,
            chat_input=chat_input,
            on_stream=lambda token: notificator.on_stream(
                token=token,
            ),
            on_stop=lambda arg: notificator.on_stop(arg=arg),
            on_thinking=lambda tool_use: notificator.on_agent_thinking(
                tool_use=tool_use,
            ),
            on_tool_result=lambda run_result: notificator.on_agent_tool_result(
                run_result=run_result
            ),
            on_reasoning=lambda token: notificator.on_reasoning(
                token=token,
            ),
        )

        return {"statusCode": 200, "body": "Message sent."}

    except RateLimitExceededError as e:
        return {
            "statusCode": 429,
            "body": json.dumps(
                dict(
                    status="ERROR",
                    reason=str(e),
                )
            ),
        }

    except RecordNotFoundError:
```

- [ ] **Step 3: mypy/black確認**

Run: `cd backend && poetry run black --check app/websocket.py && poetry run mypy --config-file mypy.ini app/websocket.py`
Expected: 成功

- [ ] **Step 4: Commit**

```bash
git add backend/app/websocket.py
git commit -m "feat: enforce rate limit on WebSocket chat endpoint"
```

---

### Task 7: `main.py`に429エラーハンドラを登録

**Files:**
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `RateLimitExceededError`(Task 1)

- [ ] **Step 1: importを追加**

既存の:

```python
from app.repositories.common import (
    RecordAccessNotAllowedError,
    RecordNotFoundError,
    ResourceConflictError,
)
```

を次のように変更する:

```python
from app.repositories.common import (
    RateLimitExceededError,
    RecordAccessNotAllowedError,
    RecordNotFoundError,
    ResourceConflictError,
)
```

- [ ] **Step 2: ハンドラを登録**

既存の:

```python
app.add_exception_handler(ResourceConflictError, error_handler_factory(409))
app.add_exception_handler(Exception, error_handler_factory(500))
```

を次のように変更する:

```python
app.add_exception_handler(ResourceConflictError, error_handler_factory(409))
app.add_exception_handler(RateLimitExceededError, error_handler_factory(429))
app.add_exception_handler(Exception, error_handler_factory(500))
```

- [ ] **Step 3: uvicornが正常起動することを確認**

Run: `cd backend && timeout 10 poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 & sleep 5 && curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health`
Expected: `200`(既存CIの`uvicorn-launch`ジョブと同じ確認方法)

- [ ] **Step 4: mypy/black確認**

Run: `cd backend && poetry run black --check app/main.py && poetry run mypy --config-file mypy.ini app/main.py`
Expected: 成功

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: return 429 for RateLimitExceededError"
```

---

### Task 8: CDK — `UsageLedgerTable`を`Database`コンストラクトに追加

**Files:**
- Modify: `cdk/lib/constructs/database.ts`
- Modify (test): `cdk/test/cdk.test.ts`

**Interfaces:**
- Produces: `Database.usageLedgerTable: Table`(Task 9, Task 10で使用)

- [ ] **Step 1: 失敗するテストを書く**

`cdk/test/cdk.test.ts`の`"default stack"`テスト(172行目付近)の末尾、`template.resourceCountIs("AWS::Cognito::UserPoolIdentityProvider", 0);`の直後に追加:

```typescript
    template.hasResourceProperties("AWS::DynamoDB::Table", {
      AttributeDefinitions: Match.arrayWith([
        Match.objectLike({ AttributeName: "SK", AttributeType: "N" }),
      ]),
      TimeToLiveSpecification: {
        AttributeName: "expire",
        Enabled: true,
      },
    });
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd cdk && npx jest cdk.test.ts -t "default stack"`
Expected: FAIL(該当プロパティを持つ`AWS::DynamoDB::Table`リソースが存在しない)

- [ ] **Step 3: `Database`コンストラクトに実装する**

`cdk/lib/constructs/database.ts`の`readonly websocketSessionTable: Table;`の直後(クラスフィールド定義)に追加:

```typescript
  readonly usageLedgerTable: Table;
```

`const tableAccessRole = new Role(...)`の直前に追加:

```typescript
    // Usage ledger table for cost-based rate limiting.
    // PK: UserId, SK: Timestamp (epoch milliseconds)
    const usageLedgerTable = new Table(this, "UsageLedgerTable", {
      partitionKey: { name: "PK", type: AttributeType.STRING },
      sortKey: { name: "SK", type: AttributeType.NUMBER },
      billingMode: BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.DESTROY,
      timeToLiveAttribute: "expire",
      encryption: TableEncryption.AWS_MANAGED,
    });

```

既存の:

```typescript
    conversationTable.grantReadWriteData(tableAccessRole);
    botTable.grantReadWriteData(tableAccessRole);
```

を次のように変更する:

```typescript
    conversationTable.grantReadWriteData(tableAccessRole);
    botTable.grantReadWriteData(tableAccessRole);
    usageLedgerTable.grantReadWriteData(tableAccessRole);
```

既存の:

```typescript
    this.conversationTable = conversationTable;
    this.botTable = botTable;
    this.tableAccessRole = tableAccessRole;
    this.websocketSessionTable = websocketSessionTable;

    new CfnOutput(this, "ConversationTableName", {
      value: conversationTable.tableName,
    });
    new CfnOutput(this, "BotTableName", {
      value: botTable.tableName,
    });
```

を次のように変更する:

```typescript
    this.conversationTable = conversationTable;
    this.botTable = botTable;
    this.tableAccessRole = tableAccessRole;
    this.websocketSessionTable = websocketSessionTable;
    this.usageLedgerTable = usageLedgerTable;

    new CfnOutput(this, "ConversationTableName", {
      value: conversationTable.tableName,
    });
    new CfnOutput(this, "BotTableName", {
      value: botTable.tableName,
    });
    new CfnOutput(this, "UsageLedgerTableName", {
      value: usageLedgerTable.tableName,
    });
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd cdk && npx jest cdk.test.ts -t "default stack"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cdk/lib/constructs/database.ts cdk/test/cdk.test.ts
git commit -m "feat: add UsageLedgerTable for rate-limit accounting"
```

---

### Task 9: CDK — SSM閾値パラメータの作成と`Api`コンストラクトへの配線

**Files:**
- Modify: `cdk/lib/bedrock-chat-stack.ts`
- Modify: `cdk/lib/constructs/api.ts`
- Modify (test): `cdk/test/cdk.test.ts`

**Interfaces:**
- Consumes: `Database.usageLedgerTable`(Task 8)
- Produces: `rateLimitFiveHourParam` / `rateLimitSevenDayParam`(Task 10でも使用するprops)

- [ ] **Step 1: 失敗するテストを書く**

`cdk/test/cdk.test.ts`の`"default stack"`テスト、Task 8で追加したアサーションの直後に追加:

```typescript
    template.hasResourceProperties("AWS::SSM::Parameter", {
      Name: "/test-/rate-limit/five-hour-usd-limit",
      Value: "10",
    });
    template.hasResourceProperties("AWS::SSM::Parameter", {
      Name: "/test-/rate-limit/seven-day-usd-limit",
      Value: "336",
    });
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd cdk && npx jest cdk.test.ts -t "default stack"`
Expected: FAIL(`AWS::SSM::Parameter`リソースが存在しない)

- [ ] **Step 3: `bedrock-chat-stack.ts`にSSMパラメータを作成する**

`cdk/lib/bedrock-chat-stack.ts`の先頭のimport群に追加:

```typescript
import * as ssm from "aws-cdk-lib/aws-ssm";
```

既存の:

```typescript
    const database = new Database(this, "Database", {
      // Enable PITR to export data to s3
      pointInTimeRecovery: true,
    });
```

の直後に追加:

```typescript

    const rateLimitParamPrefix = props.envPrefix ? `/${props.envPrefix}` : "";
    const rateLimitFiveHourParam = new ssm.StringParameter(
      this,
      "RateLimitFiveHourUsdLimitParam",
      {
        parameterName: `${rateLimitParamPrefix}/rate-limit/five-hour-usd-limit`,
        stringValue: "10",
        description:
          "USD cost limit per user over the trailing 5 hours before chat is blocked.",
      }
    );
    const rateLimitSevenDayParam = new ssm.StringParameter(
      this,
      "RateLimitSevenDayUsdLimitParam",
      {
        parameterName: `${rateLimitParamPrefix}/rate-limit/seven-day-usd-limit`,
        stringValue: "336",
        description:
          "USD cost limit per user over the trailing 7 days before chat is blocked.",
      }
    );
```

- [ ] **Step 4: `Api`コンストラクトのpropsに渡す**

`cdk/lib/bedrock-chat-stack.ts`内、既存の:

```typescript
    const backendApi = new Api(this, "BackendApi", {
      envName: props.envName,
      envPrefix: props.envPrefix,
      database,
```

を次のように変更する:

```typescript
    const backendApi = new Api(this, "BackendApi", {
      envName: props.envName,
      envPrefix: props.envPrefix,
      database,
      rateLimitFiveHourParam,
      rateLimitSevenDayParam,
```

- [ ] **Step 5: `Api`コンストラクト側を実装する**

`cdk/lib/constructs/api.ts`の先頭import群に追加:

```typescript
import * as ssm from "aws-cdk-lib/aws-ssm";
```

`ApiProps`インターフェースの`readonly database: Database;`の直後に追加:

```typescript
  readonly rateLimitFiveHourParam: ssm.IStringParameter;
  readonly rateLimitSevenDayParam: ssm.IStringParameter;
```

既存の:

```typescript
    props.largeMessageBucket.grantReadWrite(handlerRole);
```

の直後に追加:

```typescript
    props.rateLimitFiveHourParam.grantRead(handlerRole);
    props.rateLimitSevenDayParam.grantRead(handlerRole);
```

`environment: {...}`ブロック内の既存の:

```typescript
        LARGE_MESSAGE_BUCKET: props.largeMessageBucket.bucketName,
```

の直後に追加:

```typescript
        USAGE_LEDGER_TABLE_NAME: database.usageLedgerTable.tableName,
        RATE_LIMIT_FIVE_HOUR_PARAM_NAME: props.rateLimitFiveHourParam.parameterName,
        RATE_LIMIT_SEVEN_DAY_PARAM_NAME: props.rateLimitSevenDayParam.parameterName,
```

- [ ] **Step 6: テストが通ることを確認**

Run: `cd cdk && npx jest cdk.test.ts -t "default stack"`
Expected: PASS

- [ ] **Step 7: 全CDKテストとlintを実行**

Run: `cd cdk && npx jest && npx tsc --noEmit`
Expected: 全テストPASS、型エラーなし(他のテストで`Api`propsの必須フィールド不足エラーが出ないか確認するため)

- [ ] **Step 8: Commit**

```bash
git add cdk/lib/bedrock-chat-stack.ts cdk/lib/constructs/api.ts cdk/test/cdk.test.ts
git commit -m "feat: provision rate-limit SSM parameters and wire into REST API Lambda"
```

---

### Task 10: CDK — `WebSocket`コンストラクトへの配線

**Files:**
- Modify: `cdk/lib/bedrock-chat-stack.ts`
- Modify: `cdk/lib/constructs/websocket.ts`

**Interfaces:**
- Consumes: `rateLimitFiveHourParam` / `rateLimitSevenDayParam`(Task 9)、`Database.usageLedgerTable`(Task 8)

- [ ] **Step 1: `bedrock-chat-stack.ts`で`WebSocket`コンストラクトにpropsを渡す**

既存の:

```typescript
    const websocket = new WebSocket(this, "WebSocket", {
      accessLogBucket,
      database,
```

を次のように変更する:

```typescript
    const websocket = new WebSocket(this, "WebSocket", {
      accessLogBucket,
      database,
      rateLimitFiveHourParam,
      rateLimitSevenDayParam,
```

- [ ] **Step 2: `WebSocket`コンストラクト側を実装する**

`cdk/lib/constructs/websocket.ts`の先頭import群に追加:

```typescript
import * as ssm from "aws-cdk-lib/aws-ssm";
```

`WebSocketProps`インターフェースの`readonly database: Database;`の直後に追加:

```typescript
  readonly rateLimitFiveHourParam: ssm.IStringParameter;
  readonly rateLimitSevenDayParam: ssm.IStringParameter;
```

既存の:

```typescript
    largePayloadSupportBucket.grantRead(handlerRole);
    database.websocketSessionTable.grantReadWriteData(handlerRole);
    props.largeMessageBucket.grantReadWrite(handlerRole);
    props.documentBucket.grantRead(handlerRole);
```

を次のように変更する:

```typescript
    largePayloadSupportBucket.grantRead(handlerRole);
    database.websocketSessionTable.grantReadWriteData(handlerRole);
    props.largeMessageBucket.grantReadWrite(handlerRole);
    props.documentBucket.grantRead(handlerRole);
    props.rateLimitFiveHourParam.grantRead(handlerRole);
    props.rateLimitSevenDayParam.grantRead(handlerRole);
```

`environment: {...}`ブロック内の既存の:

```typescript
        WEBSOCKET_SESSION_TABLE_NAME: database.websocketSessionTable.tableName,
```

の直後に追加:

```typescript
        USAGE_LEDGER_TABLE_NAME: database.usageLedgerTable.tableName,
        RATE_LIMIT_FIVE_HOUR_PARAM_NAME: props.rateLimitFiveHourParam.parameterName,
        RATE_LIMIT_SEVEN_DAY_PARAM_NAME: props.rateLimitSevenDayParam.parameterName,
```

- [ ] **Step 3: 全CDKテストと型チェックを実行**

Run: `cd cdk && npx jest && npx tsc --noEmit`
Expected: 全テストPASS、型エラーなし

- [ ] **Step 4: Commit**

```bash
git add cdk/lib/bedrock-chat-stack.ts cdk/lib/constructs/websocket.ts
git commit -m "feat: wire rate-limit SSM parameters and usage ledger table into WebSocket Lambda"
```

---

### Task 11: バックエンド全体の最終確認

**Files:** なし(検証のみ)

- [ ] **Step 1: バックエンドの新規・変更ファイルのテストを一括実行**

Run:

```bash
cd backend && poetry run pytest \
  tests/test_repositories/test_usage_limit.py \
  tests/test_usecases/test_rate_limit.py \
  tests/test_usecases/test_post_process_result_price.py \
  -v
```

Expected: 全てPASS(12 tests: usage_limit 4件 + rate_limit 6件 + post_process_result_price 2件)

- [ ] **Step 2: mypy/blackをリポジトリ全体に対して実行**

Run: `cd backend && poetry run black --check . && poetry run mypy --config-file mypy.ini .`
Expected: 成功(今回変更した以外のファイルに影響がないことを確認)

- [ ] **Step 3: uvicorn起動確認(CIと同じ手順)**

Run: `cd backend && timeout 10 poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 & sleep 5 && curl -s http://localhost:8000/health`
Expected: `{"status":"ok"}`

- [ ] **Step 4: CDK全体のテストと型チェック**

Run: `cd cdk && npx jest && npx tsc --noEmit`
Expected: 全テストPASS、型エラーなし

- [ ] **Step 5: `git status`で意図しない変更がないことを確認**

Run: `git status --short`
Expected: Task 1〜10で意図的にcommitしたファイル以外に変更が残っていない
