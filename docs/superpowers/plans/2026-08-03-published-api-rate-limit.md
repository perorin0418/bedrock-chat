# 公開API APIキー単位レートリミット Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 公開bot APIのAPIキー作成時にユーザーとの紐づけを必須にし、そのAPIキー経由の利用コストを紐づけユーザーの既存レートリミット台帳(直近5時間$10 / 直近7日間$336)にそのまま合算する。結果として、あるユーザーが自分の公開APIキーを使いすぎると、そのユーザー自身の通常チャット(WebSocket / REST)もブロックされる。

**Architecture:** 新規DynamoDBテーブル`ApiKeyOwnerTable`(PK=ApiKeyId)にAPIキー作成時のuser_idを記録する。公開API(`routes/published_api.py`)はLambda Web Adapterが付与する`x-amzn-request-context`ヘッダーからAPI Gateway側のAPIキーIDを取得し、`ApiKeyOwnerTable`を引いて紐づけユーザーを特定、既存の`check_rate_limit`(シグネチャをuser_idの文字列受け取りに変更)でそのユーザーの既存閾値をそのまま判定する。判定結果(owner user_id)はSQSメッセージ(`ChatInput.rate_limit_user_id`)に積んで`sqs_consumer.py`に伝搬し、コスト確定時の`record_usage`を紐づけユーザーの台帳に書き込む(`User.billing_user_id` / `rate_limit_id`経由)。導入前に作成済みの未紐づけAPIキーはレート制限対象外として動作を継続する。

**Tech Stack:** Python (FastAPI, boto3, pydantic), TypeScript (AWS CDK)。既存の行レベルセキュリティ(DynamoDB + STS AssumeRoleセッションポリシー)パターンを新規テーブルにもそのまま適用する。

## Global Constraints

- 「紐づけの強制」は新規作成キーのみが対象。導入前に作成済みの未紐づけキーはレート制限の対象外のまま動作を継続し、警告ログのみ出す(仕様書で合意済み)。
- APIキー毎の独立した追加上限は設けない。紐づけユーザーの既存閾値(5時間$10 / 7日間$336)にそのまま合算する。
- `check_rate_limit`のシグネチャを`check_rate_limit(user: User)`から`check_rate_limit(user_id: str)`に変更する(中身が`user.id`しか使っていなかったため)。
- `ApiKeyOwnerTable`はTTLなし(キー削除時に明示的に行を削除する)。行レベルセキュリティ(LeadingKeys)は不要(ユーザーのCognito IDでスコープされるデータではなく、サーバー側の参照データのため)。`get_bot_table_client()`と同じ「user_idなし」のテーブルクライアント取得パターンを使う。
- 時刻取得は`app.utils.get_current_time()`(epoch milliseconds, int)を使う。
- このリポジトリに`moto`は使われていない。DynamoDBのテストは`unittest.TestCase` + `patch("boto3.resource")` + `MagicMock`で行う。
- ルート(FastAPI)ハンドラそのもの(`routes/published_api.py`の`post_message`本体、`routes/conversation.py`, `websocket.py`)を対象にした自動テストは追加しない(既存の慣習に合わせる)。ヘルパー関数単体(`_get_request_api_key_id`)は例外的にテストする(純粋関数で、入出力が明確なため)。
- CDK側で新設する`ApiPublishmentStack`(`cdk/bin/api-publish.ts`経由でデプロイされる独立スタック)自体への新規CDK自動テストは追加しない。`cdk/test/cdk.test.ts`のルートスタックテストにのみアサーションを追加する(前回のレートリミット機能追加時と同じ方針)。
- SSMパラメータ名はアカウント・リージョンから決定的に導出できるため、`ApiPublishmentStack`向けにcross-stack exportは追加しない。`cdk/lib/utils/parameter-models.ts`に共通関数`rateLimitParamName()`を切り出し、ルートスタックと`ApiPublishmentStack`の両方から使う。
- DynamoDBテーブル名(自動生成される物理名)は決定的でないため、既存の`conversationTable`/`botTable`と同じ「CfnOutputでexport → `Fn.importValue`でimport」パターンを`usageLedgerTable`・新規`apiKeyOwnerTable`にも適用する。
- CI(`.github/workflows/backend.yml`)はmypy/black/uvicorn起動/docker buildのみでpytestは実行されない。

---

### Task 1: `ApiKeyOwnerTable`アクセス基盤とリポジトリ関数を作成

**Files:**
- Modify: `backend/app/repositories/common.py`
- Create: `backend/app/repositories/api_key_owner.py`
- Test: `backend/tests/test_repositories/test_api_key_owner.py`

**Interfaces:**
- Produces: `bind_api_key_owner(api_key_id: str, user_id: str) -> None`, `find_api_key_owner(api_key_id: str) -> str | None`, `delete_api_key_owner(api_key_id: str) -> None`(Task 4, Task 6で使用)

- [ ] **Step 1: `common.py`に`API_KEY_OWNER_TABLE_NAME`とテーブルクライアント取得関数を追加**

`backend/app/repositories/common.py`の既存の:

```python
CONVERSATION_TABLE_NAME = os.environ.get("CONVERSATION_TABLE_NAME", "")
BOT_TABLE_NAME = os.environ.get("BOT_TABLE_NAME", "")
USAGE_LEDGER_TABLE_NAME = os.environ.get("USAGE_LEDGER_TABLE_NAME", "")
ACCOUNT = os.environ.get("ACCOUNT", "")
```

を次のように変更する:

```python
CONVERSATION_TABLE_NAME = os.environ.get("CONVERSATION_TABLE_NAME", "")
BOT_TABLE_NAME = os.environ.get("BOT_TABLE_NAME", "")
USAGE_LEDGER_TABLE_NAME = os.environ.get("USAGE_LEDGER_TABLE_NAME", "")
API_KEY_OWNER_TABLE_NAME = os.environ.get("API_KEY_OWNER_TABLE_NAME", "")
ACCOUNT = os.environ.get("ACCOUNT", "")
```

既存の:

```python
type_table = Literal["conversation", "bot", "usage_ledger"]
_table_name_map = {
    "conversation": CONVERSATION_TABLE_NAME,
    "bot": BOT_TABLE_NAME,
    "usage_ledger": USAGE_LEDGER_TABLE_NAME,
}
```

を次のように変更する:

```python
type_table = Literal["conversation", "bot", "usage_ledger", "api_key_owner"]
_table_name_map = {
    "conversation": CONVERSATION_TABLE_NAME,
    "bot": BOT_TABLE_NAME,
    "usage_ledger": USAGE_LEDGER_TABLE_NAME,
    "api_key_owner": API_KEY_OWNER_TABLE_NAME,
}
```

`get_usage_ledger_table_client`関数の直後に追加:

```python
def get_api_key_owner_table_client():
    """Get a DynamoDB table client for the api key owner table.
    Note: No row-level access control (server-side reference data, not scoped
    per Cognito user, same as `get_bot_table_client`).
    """
    return _get_aws_resource(
        "dynamodb", table_name=API_KEY_OWNER_TABLE_NAME
    ).Table(API_KEY_OWNER_TABLE_NAME)
```

- [ ] **Step 2: 失敗するテストを書く**

`backend/tests/test_repositories/test_api_key_owner.py`を新規作成:

```python
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.repositories.api_key_owner import (
    bind_api_key_owner,
    delete_api_key_owner,
    find_api_key_owner,
)


class TestApiKeyOwnerRepository(unittest.TestCase):
    def setUp(self):
        self.patcher1 = patch("boto3.resource")
        self.mock_boto3_resource = self.patcher1.start()

        self.mock_table = MagicMock()
        self.mock_boto3_resource.return_value.Table.return_value = self.mock_table

        os.environ["API_KEY_OWNER_TABLE_NAME"] = "test-api-key-owner-table"
        os.environ["BEDROCK_REGION"] = "us-east-1"

    def tearDown(self):
        self.patcher1.stop()
        os.environ.pop("API_KEY_OWNER_TABLE_NAME", None)
        os.environ.pop("BEDROCK_REGION", None)

    @patch(
        "app.repositories.api_key_owner.get_current_time",
        return_value=1_700_000_000_000,
    )
    def test_bind_api_key_owner_puts_expected_item(self, mock_get_current_time):
        bind_api_key_owner(api_key_id="key-1", user_id="user-1")

        self.mock_table.put_item.assert_called_once()
        item = self.mock_table.put_item.call_args.kwargs["Item"]
        self.assertEqual(item["ApiKeyId"], "key-1")
        self.assertEqual(item["UserId"], "user-1")
        self.assertEqual(item["CreateTime"], 1_700_000_000_000)

    def test_find_api_key_owner_returns_user_id_when_bound(self):
        self.mock_table.get_item.return_value = {
            "Item": {"ApiKeyId": "key-1", "UserId": "user-1"}
        }

        owner = find_api_key_owner("key-1")

        self.assertEqual(owner, "user-1")
        self.mock_table.get_item.assert_called_once_with(Key={"ApiKeyId": "key-1"})

    def test_find_api_key_owner_returns_none_when_unbound(self):
        self.mock_table.get_item.return_value = {}

        owner = find_api_key_owner("key-legacy")

        self.assertIsNone(owner)

    def test_delete_api_key_owner_deletes_item(self):
        delete_api_key_owner("key-1")

        self.mock_table.delete_item.assert_called_once_with(Key={"ApiKeyId": "key-1"})

    def test_delete_api_key_owner_swallows_exceptions(self):
        self.mock_table.delete_item.side_effect = Exception("boom")

        delete_api_key_owner("key-1")  # must not raise


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: テストが失敗することを確認**

Run: `cd backend && poetry run pytest tests/test_repositories/test_api_key_owner.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'app.repositories.api_key_owner'`)

- [ ] **Step 4: 実装を書く**

`backend/app/repositories/api_key_owner.py`を新規作成:

```python
import logging

from app.repositories.common import get_api_key_owner_table_client
from app.utils import get_current_time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def bind_api_key_owner(api_key_id: str, user_id: str) -> None:
    """Bind an API key to the user who created it.

    Not best-effort: an API key without a bound owner cannot be rate-limited,
    so callers must treat a failure here as API key creation failure.
    """
    table = get_api_key_owner_table_client()
    table.put_item(
        Item={
            "ApiKeyId": api_key_id,
            "UserId": user_id,
            "CreateTime": get_current_time(),
        }
    )


def find_api_key_owner(api_key_id: str) -> str | None:
    """Return the bound user_id for `api_key_id`, or None if unbound
    (e.g. a key created before this feature existed)."""
    table = get_api_key_owner_table_client()
    response = table.get_item(Key={"ApiKeyId": api_key_id})
    item = response.get("Item")
    return item["UserId"] if item else None


def delete_api_key_owner(api_key_id: str) -> None:
    """Best-effort delete: cleanup must never block API key/bot deletion."""
    try:
        table = get_api_key_owner_table_client()
        table.delete_item(Key={"ApiKeyId": api_key_id})
    except Exception:
        logger.exception(f"Failed to delete api key owner binding for {api_key_id}.")
```

- [ ] **Step 5: テストが通ることを確認**

Run: `cd backend && poetry run pytest tests/test_repositories/test_api_key_owner.py -v`
Expected: PASS(5 tests)

- [ ] **Step 6: mypy/black確認**

Run: `cd backend && poetry run black --check app/repositories/common.py app/repositories/api_key_owner.py tests/test_repositories/test_api_key_owner.py && poetry run mypy --config-file mypy.ini app/repositories/common.py app/repositories/api_key_owner.py`
Expected: 成功

- [ ] **Step 7: Commit**

```bash
git add backend/app/repositories/common.py backend/app/repositories/api_key_owner.py backend/tests/test_repositories/test_api_key_owner.py
git commit -m "feat: add api key owner table repository for published-API rate limiting"
```

---

### Task 2: `User`に`billing_user_id`/`rate_limit_id`を追加

**Files:**
- Modify: `backend/app/user.py`
- Test: `backend/tests/test_user.py`

**Interfaces:**
- Produces: `User.billing_user_id: str | None`, `User.rate_limit_id: str`(property), `User.from_published_api_id(bot_id, billing_user_id=None)`(Task 5, Task 6で使用)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_user.py`を新規作成:

```python
import sys
import unittest

sys.path.insert(0, ".")

from app.user import User


class TestUserRateLimitId(unittest.TestCase):
    def test_rate_limit_id_defaults_to_own_id(self):
        user = User(id="user-1", name="user-1", email="user-1@example.com", groups=[])
        self.assertIsNone(user.billing_user_id)
        self.assertEqual(user.rate_limit_id, "user-1")

    def test_rate_limit_id_uses_billing_user_id_when_set(self):
        user = User(
            id="PUBLISHED_API#bot-1",
            name="PUBLISHED_API#bot-1",
            email="PUBLISHED_API#bot-1",
            groups=["Admin"],
            billing_user_id="owner-1",
        )
        self.assertEqual(user.rate_limit_id, "owner-1")

    def test_from_published_api_id_without_billing_user_id(self):
        user = User.from_published_api_id("bot-1")
        self.assertEqual(user.id, "PUBLISHED_API#bot-1")
        self.assertIsNone(user.billing_user_id)
        self.assertEqual(user.rate_limit_id, "PUBLISHED_API#bot-1")

    def test_from_published_api_id_with_billing_user_id(self):
        user = User.from_published_api_id("bot-1", billing_user_id="owner-1")
        self.assertEqual(user.id, "PUBLISHED_API#bot-1")
        self.assertEqual(user.billing_user_id, "owner-1")
        self.assertEqual(user.rate_limit_id, "owner-1")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && poetry run pytest tests/test_user.py -v`
Expected: FAIL(`billing_user_id`未定義のため`TypeError`、または`rate_limit_id`属性なしで`AttributeError`)

- [ ] **Step 3: 実装を書く**

`backend/app/user.py`の既存の:

```python
class User(UserWithoutGroups):
    groups: list[str]

    def is_admin(self) -> bool:
```

を次のように変更する:

```python
class User(UserWithoutGroups):
    groups: list[str]
    billing_user_id: str | None = None

    @property
    def rate_limit_id(self) -> str:
        """The user_id whose rate-limit ledger this user's usage should be
        recorded against. Equal to `id` except for published-API requests
        made with a key bound to a different user."""
        return self.billing_user_id or self.id

    def is_admin(self) -> bool:
```

既存の:

```python
    @classmethod
    def from_published_api_id(cls, bot_id: str) -> Self:
        api_bot_id = f"PUBLISHED_API#{bot_id}"
        return cls(
            id=api_bot_id,
            name=api_bot_id,
            email=api_bot_id,  # dummy email
            # Note: Publish API is allowed to access all bot resources.
            # It should be refactored to have a more fine-grained permission.
            groups=["Admin"],
        )
```

を次のように変更する:

```python
    @classmethod
    def from_published_api_id(
        cls, bot_id: str, billing_user_id: str | None = None
    ) -> Self:
        api_bot_id = f"PUBLISHED_API#{bot_id}"
        return cls(
            id=api_bot_id,
            name=api_bot_id,
            email=api_bot_id,  # dummy email
            # Note: Publish API is allowed to access all bot resources.
            # It should be refactored to have a more fine-grained permission.
            groups=["Admin"],
            billing_user_id=billing_user_id,
        )
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && poetry run pytest tests/test_user.py -v`
Expected: PASS(4 tests)

- [ ] **Step 5: mypy/black確認**

Run: `cd backend && poetry run black --check app/user.py tests/test_user.py && poetry run mypy --config-file mypy.ini app/user.py`
Expected: 成功

- [ ] **Step 6: Commit**

```bash
git add backend/app/user.py backend/tests/test_user.py
git commit -m "feat: add User.billing_user_id and rate_limit_id for published-API attribution"
```

---

### Task 3: `check_rate_limit`のシグネチャをuser_id受け取りに変更

**Files:**
- Modify: `backend/app/usecases/rate_limit.py`
- Modify: `backend/app/routes/conversation.py`
- Modify: `backend/app/websocket.py`
- Modify (test): `backend/tests/test_usecases/test_rate_limit.py`

**Interfaces:**
- Produces: `check_rate_limit(user_id: str) -> None`(Task 6で使用)

- [ ] **Step 1: 既存テストをシグネチャ変更に合わせて書き換える**

`backend/tests/test_usecases/test_rate_limit.py`を次の内容で置き換える(`create_test_user(...)`呼び出しを素の文字列に置き換え、不要なimportを削除するのみで、テストケース自体は変更しない):

```python
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")

from app.repositories.common import RateLimitExceededError
from app.usecases import rate_limit


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
            rate_limit.check_rate_limit("user-1")

    def test_raises_when_five_hour_sum_exceeds_limit(self):
        with patch.object(
            rate_limit, "get_current_time", return_value=1_700_000_000_000
        ), patch.object(
            rate_limit, "_get_limit", side_effect=[10.0, 336.0]
        ), patch.object(
            rate_limit, "get_usage_since", side_effect=[10.01, 100.0]
        ):
            with self.assertRaises(RateLimitExceededError):
                rate_limit.check_rate_limit("user-1")

    def test_does_not_raise_when_five_hour_sum_equals_limit(self):
        # "超過" is strictly `>`, not `>=`
        with patch.object(
            rate_limit, "get_current_time", return_value=1_700_000_000_000
        ), patch.object(
            rate_limit, "_get_limit", side_effect=[10.0, 336.0]
        ), patch.object(
            rate_limit, "get_usage_since", side_effect=[10.0, 100.0]
        ):
            rate_limit.check_rate_limit("user-1")

    def test_raises_when_seven_day_sum_exceeds_limit(self):
        with patch.object(
            rate_limit, "get_current_time", return_value=1_700_000_000_000
        ), patch.object(
            rate_limit, "_get_limit", side_effect=[10.0, 336.0]
        ), patch.object(
            rate_limit, "get_usage_since", side_effect=[5.0, 336.01]
        ):
            with self.assertRaises(RateLimitExceededError):
                rate_limit.check_rate_limit("user-1")

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
Expected: FAIL(`check_rate_limit("user-1")`が内部で`"user-1".id`にアクセスしようとして`AttributeError`)

- [ ] **Step 3: `rate_limit.py`のシグネチャを変更する**

`backend/app/usecases/rate_limit.py`の既存の:

```python
import logging
import os
import time

import boto3
from app.repositories.common import RateLimitExceededError
from app.repositories.usage_limit import get_usage_since
from app.user import User
from app.utils import get_current_time
```

を次のように変更する(`User`のimportが不要になる):

```python
import logging
import os
import time

import boto3
from app.repositories.common import RateLimitExceededError
from app.repositories.usage_limit import get_usage_since
from app.utils import get_current_time
```

既存の:

```python
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

を次のように変更する:

```python
def check_rate_limit(user_id: str) -> None:
    """Raise `RateLimitExceededError` if `user_id`'s recorded cost exceeds
    either the trailing 5-hour or trailing 7-day USD limit (values read from
    SSM)."""
    now_ms = get_current_time()

    five_hour_limit = _get_limit(FIVE_HOUR_PARAM_NAME)
    five_hour_sum = get_usage_since(user_id, now_ms - FIVE_HOUR_WINDOW_MS)
    if five_hour_sum > five_hour_limit:
        raise RateLimitExceededError(
            f"Rate limit exceeded: ${five_hour_sum:.2f} spent in the last 5 hours "
            f"(limit ${five_hour_limit:.2f})."
        )

    seven_day_limit = _get_limit(SEVEN_DAY_PARAM_NAME)
    seven_day_sum = get_usage_since(user_id, now_ms - SEVEN_DAY_WINDOW_MS)
    if seven_day_sum > seven_day_limit:
        raise RateLimitExceededError(
            f"Rate limit exceeded: ${seven_day_sum:.2f} spent in the last 7 days "
            f"(limit ${seven_day_limit:.2f})."
        )
```

- [ ] **Step 4: 呼び出し元を修正する**

`backend/app/routes/conversation.py`の既存の:

```python
    check_rate_limit(current_user)
```

を次のように変更する:

```python
    check_rate_limit(current_user.id)
```

`backend/app/websocket.py`の既存の:

```python
        check_rate_limit(user)
```

を次のように変更する:

```python
        check_rate_limit(user.id)
```

- [ ] **Step 5: テストが通ることを確認**

Run: `cd backend && poetry run pytest tests/test_usecases/test_rate_limit.py -v`
Expected: PASS(6 tests)

- [ ] **Step 6: mypy/black確認**

Run: `cd backend && poetry run black --check app/usecases/rate_limit.py app/routes/conversation.py app/websocket.py tests/test_usecases/test_rate_limit.py && poetry run mypy --config-file mypy.ini app/usecases/rate_limit.py app/routes/conversation.py app/websocket.py`
Expected: 成功

- [ ] **Step 7: Commit**

```bash
git add backend/app/usecases/rate_limit.py backend/app/routes/conversation.py backend/app/websocket.py backend/tests/test_usecases/test_rate_limit.py
git commit -m "refactor: check_rate_limit takes a user_id string instead of a User"
```

---

### Task 4: APIキー作成/削除にオーナー紐づけの作成・削除を配線(既存タイポも修正)

**Files:**
- Modify: `backend/app/usecases/publication.py`
- Test: `backend/tests/test_usecases/test_publication.py`

**Interfaces:**
- Consumes: `bind_api_key_owner`, `delete_api_key_owner`(Task 1)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_usecases/test_publication.py`を新規作成:

```python
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, ".")

from app.repositories.models.api_publication import (
    ApiKeyModel,
    ApiUsagePlanModel,
    ApiUsagePlanQuotaModel,
    ApiUsagePlanThrottleModel,
    PublishedApiStackModel,
)
from app.routes.schemas.api_publication import ApiKeyInput
from app.usecases.publication import (
    create_new_api_key,
    remove_api_key,
    remove_bot_publication,
)
from tests.test_usecases.utils.user_factory import create_test_user

BOT_ID = "bot-1"

STACK = PublishedApiStackModel(
    stack_id="stack-id",
    stack_name=f"ApiPublishmentStack{BOT_ID}",
    stack_status="CREATE_COMPLETE",
    api_id="api-id",
    api_name="api-name",
    api_usage_plan_id="usage-plan-id",
    api_allowed_origins=["*"],
    api_stage="api",
    create_time=1700000000000,
)


def _usage_plan(key_ids):
    return ApiUsagePlanModel(
        id="usage-plan-id",
        name="usage-plan",
        quota=ApiUsagePlanQuotaModel(limit=None, offset=None, period=None),
        throttle=ApiUsagePlanThrottleModel(rate_limit=None, burst_limit=None),
        key_ids=key_ids,
    )


class TestCreateNewApiKey(unittest.TestCase):
    @patch("app.usecases.publication.bind_api_key_owner")
    @patch("app.usecases.publication.create_api_key")
    @patch("app.usecases.publication.find_usage_plan_by_id")
    @patch("app.usecases.publication.find_stack_by_bot_id", return_value=STACK)
    @patch("app.usecases.publication._fetch_bot_with_permission_check")
    def test_binds_new_key_to_creating_user(
        self,
        mock_fetch_bot,
        mock_find_stack,
        mock_find_usage_plan,
        mock_create_api_key,
        mock_bind_api_key_owner,
    ):
        mock_find_usage_plan.return_value = _usage_plan([])
        mock_create_api_key.return_value = ApiKeyModel(
            id="key-1",
            description="d",
            value="",
            enabled=True,
            created_date=1700000000000,
        )

        create_new_api_key(
            create_test_user("owner-1"), BOT_ID, ApiKeyInput(description="d")
        )

        mock_bind_api_key_owner.assert_called_once_with("key-1", "owner-1")

    @patch("app.usecases.publication.delete_api_key")
    @patch(
        "app.usecases.publication.bind_api_key_owner", side_effect=Exception("boom")
    )
    @patch("app.usecases.publication.create_api_key")
    @patch("app.usecases.publication.find_usage_plan_by_id")
    @patch("app.usecases.publication.find_stack_by_bot_id", return_value=STACK)
    @patch("app.usecases.publication._fetch_bot_with_permission_check")
    def test_rolls_back_key_when_binding_fails(
        self,
        mock_fetch_bot,
        mock_find_stack,
        mock_find_usage_plan,
        mock_create_api_key,
        mock_bind_api_key_owner,
        mock_delete_api_key,
    ):
        mock_find_usage_plan.return_value = _usage_plan([])
        mock_create_api_key.return_value = ApiKeyModel(
            id="key-1",
            description="d",
            value="",
            enabled=True,
            created_date=1700000000000,
        )

        with self.assertRaises(Exception):
            create_new_api_key(
                create_test_user("owner-1"), BOT_ID, ApiKeyInput(description="d")
            )

        mock_delete_api_key.assert_called_once_with("key-1")


class TestRemoveApiKey(unittest.TestCase):
    @patch("app.usecases.publication.delete_api_key_owner")
    @patch("app.usecases.publication.delete_api_key")
    @patch("app.usecases.publication.find_usage_plan_by_id")
    @patch("app.usecases.publication.find_stack_by_bot_id", return_value=STACK)
    @patch("app.usecases.publication._fetch_bot_with_permission_check")
    def test_deletes_owner_binding_with_key(
        self,
        mock_fetch_bot,
        mock_find_stack,
        mock_find_usage_plan,
        mock_delete_api_key,
        mock_delete_api_key_owner,
    ):
        mock_find_usage_plan.return_value = _usage_plan(["key-1"])

        remove_api_key(create_test_user("owner-1"), BOT_ID, "key-1")

        mock_delete_api_key.assert_called_once_with("key-1")
        mock_delete_api_key_owner.assert_called_once_with("key-1")


class TestRemoveBotPublication(unittest.TestCase):
    @patch("app.usecases.publication.delete_bot_publication")
    @patch("app.usecases.publication.delete_stack_by_bot_id")
    @patch("app.usecases.publication.delete_api_key_owner")
    @patch("app.usecases.publication.delete_api_key")
    @patch("app.usecases.publication.find_usage_plan_by_id")
    @patch("app.usecases.publication.find_stack_by_bot_id", return_value=STACK)
    @patch("app.usecases.publication.find_build_status_by_build_id", return_value="SUCCEEDED")
    @patch("app.usecases.publication._fetch_bot_with_permission_check")
    def test_deletes_owner_binding_for_every_key(
        self,
        mock_fetch_bot,
        mock_find_build_status,
        mock_find_stack,
        mock_find_usage_plan,
        mock_delete_api_key,
        mock_delete_api_key_owner,
        mock_delete_stack,
        mock_delete_bot_publication,
    ):
        mock_fetch_bot.return_value.published_api_codebuild_id = "build-1"
        mock_fetch_bot.return_value.owner_user_id = "owner-1"
        mock_find_usage_plan.return_value = _usage_plan(["key-1", "key-2"])

        remove_bot_publication(create_test_user("owner-1"), BOT_ID)

        self.assertEqual(mock_delete_api_key.call_count, 2)
        self.assertEqual(mock_delete_api_key_owner.call_count, 2)
        mock_delete_api_key_owner.assert_any_call("key-1")
        mock_delete_api_key_owner.assert_any_call("key-2")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && poetry run pytest tests/test_usecases/test_publication.py -v`
Expected: FAIL — `test_binds_new_key_to_creating_user`/`test_rolls_back_key_when_binding_fails`は`bind_api_key_owner`が未importで`AttributeError`。`test_deletes_owner_binding_with_key`/`test_deletes_owner_binding_for_every_key`は`delete_api_key_owner`が未importで`AttributeError`。`test_deletes_owner_binding_for_every_key`は既存タイポ(`"CREATE_COMPLETED"`)のため、修正前は`mock_delete_api_key.call_count`が0のままでも失敗する。

- [ ] **Step 3: `publication.py`に配線する**

`backend/app/usecases/publication.py`の既存の:

```python
from app.repositories.api_publication import (
    create_api_key,
    delete_api_key,
    delete_stack_by_bot_id,
    find_api_key_by_id,
    find_build_status_by_build_id,
    find_stack_by_bot_id,
    find_usage_plan_by_id,
)
```

を次のように変更する:

```python
from app.repositories.api_key_owner import bind_api_key_owner, delete_api_key_owner
from app.repositories.api_publication import (
    create_api_key,
    delete_api_key,
    delete_stack_by_bot_id,
    find_api_key_by_id,
    find_build_status_by_build_id,
    find_stack_by_bot_id,
    find_usage_plan_by_id,
)
```

既存の`remove_bot_publication`内の(既存タイポ`"CREATE_COMPLETED"`の修正):

```python
    if stack.stack_status == "CREATE_COMPLETED":
        usage_plan = find_usage_plan_by_id(stack.api_usage_plan_id)  # type: ignore
        for key_id in usage_plan.key_ids:
            delete_api_key(key_id)
```

を次のように変更する:

```python
    if stack.stack_status == "CREATE_COMPLETE":
        usage_plan = find_usage_plan_by_id(stack.api_usage_plan_id)  # type: ignore
        for key_id in usage_plan.key_ids:
            delete_api_key(key_id)
            delete_api_key_owner(key_id)
```

既存の`create_new_api_key`:

```python
def create_new_api_key(
    user: User, bot_id: str, api_key_input: ApiKeyInput
) -> ApiKeyOutput:
    bot = _fetch_bot_with_permission_check(user, bot_id)

    stack = find_stack_by_bot_id(bot_id)
    assert (
        stack.stack_status == "CREATE_COMPLETE"
    ), f"Bot {bot_id} stack creation is not completed."
    usage_plan = find_usage_plan_by_id(stack.api_usage_plan_id)  # type: ignore

    # Create API Key
    key = create_api_key(usage_plan.id, api_key_input.description)
    return ApiKeyOutput(
        id=key.id,
        value="",
        description=key.description,
        enabled=key.enabled,
        created_date=key.created_date,
    )
```

を次のように変更する:

```python
def create_new_api_key(
    user: User, bot_id: str, api_key_input: ApiKeyInput
) -> ApiKeyOutput:
    bot = _fetch_bot_with_permission_check(user, bot_id)

    stack = find_stack_by_bot_id(bot_id)
    assert (
        stack.stack_status == "CREATE_COMPLETE"
    ), f"Bot {bot_id} stack creation is not completed."
    usage_plan = find_usage_plan_by_id(stack.api_usage_plan_id)  # type: ignore

    # Create API Key
    key = create_api_key(usage_plan.id, api_key_input.description)

    # Binding to the creating user is mandatory: an API key without an owner
    # cannot be rate-limited. Roll back key creation if binding fails.
    try:
        bind_api_key_owner(key.id, user.id)
    except Exception:
        delete_api_key(key.id)
        raise

    return ApiKeyOutput(
        id=key.id,
        value="",
        description=key.description,
        enabled=key.enabled,
        created_date=key.created_date,
    )
```

既存の`remove_api_key`:

```python
    # Delete API Key
    delete_api_key(api_key_id)
    return
```

を次のように変更する:

```python
    # Delete API Key
    delete_api_key(api_key_id)
    delete_api_key_owner(api_key_id)
    return
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && poetry run pytest tests/test_usecases/test_publication.py -v`
Expected: PASS(4 tests)

- [ ] **Step 5: mypy/black確認**

Run: `cd backend && poetry run black --check app/usecases/publication.py tests/test_usecases/test_publication.py && poetry run mypy --config-file mypy.ini app/usecases/publication.py`
Expected: 成功

- [ ] **Step 6: Commit**

```bash
git add backend/app/usecases/publication.py backend/tests/test_usecases/test_publication.py
git commit -m "feat: bind new API keys to their creating user; fix dead cleanup path"
```

---

### Task 5: `ChatInput`に内部フィールドを追加し、コスト記録を`rate_limit_id`に切り替え

**Files:**
- Modify: `backend/app/routes/schemas/conversation.py`
- Modify: `backend/app/usecases/chat.py`

**Interfaces:**
- Produces: `ChatInput.rate_limit_user_id: str | None`(Task 6で使用)
- Consumes: `User.rate_limit_id`(Task 2)

- [ ] **Step 1: `ChatInput`にフィールドを追加**

`backend/app/routes/schemas/conversation.py`の既存の:

```python
class ChatInput(BaseSchema):
    conversation_id: str
    message: MessageInput
    bot_id: str | None = Field(None)
    continue_generate: bool = Field(False)
    enable_reasoning: bool = Field(False)
```

を次のように変更する:

```python
class ChatInput(BaseSchema):
    conversation_id: str
    message: MessageInput
    bot_id: str | None = Field(None)
    continue_generate: bool = Field(False)
    enable_reasoning: bool = Field(False)
    # Internal only: set server-side by `routes/published_api.py` after
    # resolving the API key's bound owner, then read by `sqs_consumer.py`.
    # Normal chat routes never set or read this field.
    rate_limit_user_id: str | None = Field(None)
```

- [ ] **Step 2: `chat.py`のコスト記録先を変更**

`backend/app/usecases/chat.py`の既存の:

```python
    record_usage(user.id, result["price"])
```

を次のように変更する:

```python
    record_usage(user.rate_limit_id, result["price"])
```

- [ ] **Step 3: 既存テストが壊れていないことを確認**

`test_post_process_result_price.py`の既存テストは`create_test_user(...)`(`billing_user_id`未設定 → `rate_limit_id == id`)を使っているため、変更なしでそのままPASSする想定。

Run: `cd backend && poetry run pytest tests/test_usecases/test_post_process_result_price.py -v`
Expected: PASS(2 tests、変更なし)

- [ ] **Step 4: mypy/black確認**

Run: `cd backend && poetry run black --check app/routes/schemas/conversation.py app/usecases/chat.py && poetry run mypy --config-file mypy.ini app/routes/schemas/conversation.py app/usecases/chat.py`
Expected: 成功

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/schemas/conversation.py backend/app/usecases/chat.py
git commit -m "feat: record published-API usage against the bound owner's rate-limit ledger"
```

---

### Task 6: 公開APIリクエストでのオーナー解決・レート制限判定・伝搬

**Files:**
- Modify: `backend/app/routes/published_api.py`
- Modify: `backend/app/sqs_consumer.py`
- Test: `backend/tests/test_routes/test_published_api.py`

**Interfaces:**
- Consumes: `find_api_key_owner`(Task 1), `check_rate_limit(user_id: str)`(Task 3), `ChatInput.rate_limit_user_id`(Task 5), `User.from_published_api_id(bot_id, billing_user_id=...)`(Task 2)

- [ ] **Step 1: 失敗するテストを書く(ヘッダー解析ヘルパーのみ)**

`backend/tests/test_routes/test_published_api.py`を新規作成:

```python
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, ".")

from app.routes.published_api import _get_request_api_key_id


def _request_with_header(value):
    request = MagicMock()
    request.headers = {"x-amzn-request-context": value} if value is not None else {}
    return request


class TestGetRequestApiKeyId(unittest.TestCase):
    def test_returns_api_key_id_when_present(self):
        request = _request_with_header('{"identity": {"apiKeyId": "key-1"}}')
        self.assertEqual(_get_request_api_key_id(request), "key-1")

    def test_returns_none_when_header_missing(self):
        request = _request_with_header(None)
        self.assertIsNone(_get_request_api_key_id(request))

    def test_returns_none_when_header_is_malformed_json(self):
        request = _request_with_header("not-json")
        self.assertIsNone(_get_request_api_key_id(request))

    def test_returns_none_when_identity_missing(self):
        request = _request_with_header("{}")
        self.assertIsNone(_get_request_api_key_id(request))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && poetry run pytest tests/test_routes/test_published_api.py -v`
Expected: FAIL(`ImportError: cannot import name '_get_request_api_key_id'`)

- [ ] **Step 3: `published_api.py`に実装する**

`backend/app/routes/published_api.py`の既存の:

```python
import json
import os
from time import sleep

import boto3
from app.routes.schemas.conversation import ChatInput, Conversation, MessageInput
from app.routes.schemas.published_api import (
    ChatInputWithoutBotId,
    ChatOutputWithoutBotId,
    MessageRequestedResponse,
)
from app.usecases.chat import chat, fetch_conversation
from app.user import User
from fastapi import APIRouter, HTTPException, Request
from ulid import ULID

router = APIRouter(tags=["published_api"])

sqs_client = boto3.client("sqs")
QUEUE_URL = os.environ.get("QUEUE_URL", "")
```

を次のように変更する:

```python
import json
import logging
import os
from time import sleep

import boto3
from app.repositories.api_key_owner import find_api_key_owner
from app.routes.schemas.conversation import ChatInput, Conversation, MessageInput
from app.routes.schemas.published_api import (
    ChatInputWithoutBotId,
    ChatOutputWithoutBotId,
    MessageRequestedResponse,
)
from app.usecases.chat import chat, fetch_conversation
from app.usecases.rate_limit import check_rate_limit
from app.user import User
from fastapi import APIRouter, HTTPException, Request
from ulid import ULID

logger = logging.getLogger(__name__)

router = APIRouter(tags=["published_api"])

sqs_client = boto3.client("sqs")
QUEUE_URL = os.environ.get("QUEUE_URL", "")


def _get_request_api_key_id(request: Request) -> str | None:
    """Extract the API Gateway API key id used for this request.

    Populated by the Lambda Web Adapter, which forwards the original Lambda
    event's `requestContext` as this header. Returns None if the header is
    absent or unparseable (e.g. local development), in which case the caller
    should skip rate-limit attribution rather than fail the request.
    """
    raw_context = request.headers.get("x-amzn-request-context")
    if not raw_context:
        return None
    try:
        return json.loads(raw_context).get("identity", {}).get("apiKeyId")
    except (json.JSONDecodeError, AttributeError):
        return None
```

既存の`post_message`:

```python
@router.post("/conversation", response_model=MessageRequestedResponse)
def post_message(request: Request, message_input: ChatInputWithoutBotId):
    """Send chat message"""
    current_user: User = request.state.current_user

    # Extract bot_id from `current_user.id`
    # NOTE: user_id naming rule is implemented on `add_current_user_to_request` method
    bot_id = (
        current_user.id.split("#")[1] if "#" in current_user.id else current_user.id
    )

    # Generate conversation id if not provided
    conversation_id = (
        str(ULID())
        if message_input.conversation_id is None
        else message_input.conversation_id
    )
    # Issue id for the response message
    response_message_id = str(ULID())

    chat_input = ChatInput(
        conversation_id=conversation_id,
        message=MessageInput(
            role="user",
            content=message_input.message.content,
            model=message_input.message.model,
            parent_message_id=None,  # Use the latest message as the parent
            message_id=response_message_id,
        ),
        bot_id=bot_id,
        continue_generate=message_input.continue_generate,
        enable_reasoning=message_input.enable_reasoning,
    )

    try:
        _ = sqs_client.send_message(
            QueueUrl=QUEUE_URL, MessageBody=chat_input.model_dump_json()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return MessageRequestedResponse(
        conversation_id=conversation_id, message_id=response_message_id
    )
```

を次のように変更する:

```python
@router.post("/conversation", response_model=MessageRequestedResponse)
def post_message(request: Request, message_input: ChatInputWithoutBotId):
    """Send chat message"""
    current_user: User = request.state.current_user

    # Extract bot_id from `current_user.id`
    # NOTE: user_id naming rule is implemented on `add_current_user_to_request` method
    bot_id = (
        current_user.id.split("#")[1] if "#" in current_user.id else current_user.id
    )

    api_key_id = _get_request_api_key_id(request)
    rate_limit_user_id = find_api_key_owner(api_key_id) if api_key_id else None
    if rate_limit_user_id is not None:
        check_rate_limit(rate_limit_user_id)
    else:
        logger.warning(
            f"Published API key {api_key_id} has no bound owner; skipping "
            "rate-limit check for this request."
        )

    # Generate conversation id if not provided
    conversation_id = (
        str(ULID())
        if message_input.conversation_id is None
        else message_input.conversation_id
    )
    # Issue id for the response message
    response_message_id = str(ULID())

    chat_input = ChatInput(
        conversation_id=conversation_id,
        message=MessageInput(
            role="user",
            content=message_input.message.content,
            model=message_input.message.model,
            parent_message_id=None,  # Use the latest message as the parent
            message_id=response_message_id,
        ),
        bot_id=bot_id,
        continue_generate=message_input.continue_generate,
        enable_reasoning=message_input.enable_reasoning,
        rate_limit_user_id=rate_limit_user_id,
    )

    try:
        _ = sqs_client.send_message(
            QueueUrl=QUEUE_URL, MessageBody=chat_input.model_dump_json()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return MessageRequestedResponse(
        conversation_id=conversation_id, message_id=response_message_id
    )
```

- [ ] **Step 4: `sqs_consumer.py`に伝搬する**

`backend/app/sqs_consumer.py`の既存の:

```python
        user = User.from_published_api_id(chat_input.bot_id)
```

を次のように変更する:

```python
        user = User.from_published_api_id(
            chat_input.bot_id, billing_user_id=chat_input.rate_limit_user_id
        )
```

- [ ] **Step 5: テストが通ることを確認**

Run: `cd backend && poetry run pytest tests/test_routes/test_published_api.py -v`
Expected: PASS(4 tests)

- [ ] **Step 6: mypy/black確認**

Run: `cd backend && poetry run black --check app/routes/published_api.py app/sqs_consumer.py tests/test_routes/test_published_api.py && poetry run mypy --config-file mypy.ini app/routes/published_api.py app/sqs_consumer.py`
Expected: 成功

- [ ] **Step 7: Commit**

```bash
git add backend/app/routes/published_api.py backend/app/sqs_consumer.py backend/tests/test_routes/test_published_api.py
git commit -m "feat: resolve published-API key owner and enforce rate limit before enqueueing"
```

---

### Task 7: CDK — `ApiKeyOwnerTable`とレートリミットパラメータ名の共通化

**Files:**
- Modify: `cdk/lib/utils/parameter-models.ts`
- Modify: `cdk/lib/constructs/database.ts`
- Modify: `cdk/lib/bedrock-chat-stack.ts`
- Modify (test): `cdk/test/cdk.test.ts`

**Interfaces:**
- Produces: `rateLimitParamName(envPrefix, window)`(Task 8で使用), `Database.apiKeyOwnerTable: Table`(Task 8で使用), exported CfnOutputs `...BedrockClaudeChatUsageLedgerTableName` / `...BedrockClaudeChatApiKeyOwnerTableName`(Task 8で使用)

- [ ] **Step 1: 失敗するテストを書く**

`cdk/test/cdk.test.ts`の`"default stack"`テスト内、既存の:

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

の直後に追加:

```typescript
    template.hasResourceProperties("AWS::DynamoDB::Table", {
      AttributeDefinitions: Match.arrayWith([
        Match.objectLike({ AttributeName: "ApiKeyId", AttributeType: "S" }),
      ]),
    });
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd cdk && npx jest cdk.test.ts -t "default stack"`
Expected: FAIL(`ApiKeyId`属性を持つ`AWS::DynamoDB::Table`が存在しない)

- [ ] **Step 3: `parameter-models.ts`に共通関数を追加**

`cdk/lib/utils/parameter-models.ts`の末尾に追加:

```typescript
/**
 * Build the SSM parameter name for a rate-limit threshold. Shared between
 * the main stack (where the parameter is created) and the API-publishment
 * stack (where it's read, in a separate CDK app/deploy), since the name is
 * fully deterministic from `envPrefix`.
 */
export function rateLimitParamName(
  envPrefix: string,
  window: "five-hour" | "seven-day"
): string {
  const prefix = envPrefix ? `/${envPrefix}` : "";
  return `${prefix}/rate-limit/${window}-usd-limit`;
}
```

- [ ] **Step 4: `database.ts`に`ApiKeyOwnerTable`を追加**

`cdk/lib/constructs/database.ts`の既存の:

```typescript
  readonly websocketSessionTable: Table;
  readonly usageLedgerTable: Table;
```

を次のように変更する:

```typescript
  readonly websocketSessionTable: Table;
  readonly usageLedgerTable: Table;
  readonly apiKeyOwnerTable: Table;
```

既存の:

```typescript
    const tableAccessRole = new Role(this, "TableAccessRole", {
      assumedBy: new AccountPrincipal(Stack.of(this).account),
    });
    conversationTable.grantReadWriteData(tableAccessRole);
    botTable.grantReadWriteData(tableAccessRole);
    usageLedgerTable.grantReadWriteData(tableAccessRole);
```

の直前に追加:

```typescript
    // Binds a published-API key to the user who created it, for rate-limit
    // attribution. PK: ApiKeyId. No TTL — rows are deleted explicitly when
    // the key (or its bot's publication) is deleted.
    const apiKeyOwnerTable = new Table(this, "ApiKeyOwnerTable", {
      partitionKey: { name: "ApiKeyId", type: AttributeType.STRING },
      billingMode: BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.DESTROY,
      encryption: TableEncryption.AWS_MANAGED,
    });

```

そして既存の:

```typescript
    conversationTable.grantReadWriteData(tableAccessRole);
    botTable.grantReadWriteData(tableAccessRole);
    usageLedgerTable.grantReadWriteData(tableAccessRole);
```

を次のように変更する:

```typescript
    conversationTable.grantReadWriteData(tableAccessRole);
    botTable.grantReadWriteData(tableAccessRole);
    usageLedgerTable.grantReadWriteData(tableAccessRole);
    apiKeyOwnerTable.grantReadWriteData(tableAccessRole);
```

既存の:

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

を次のように変更する:

```typescript
    this.conversationTable = conversationTable;
    this.botTable = botTable;
    this.tableAccessRole = tableAccessRole;
    this.websocketSessionTable = websocketSessionTable;
    this.usageLedgerTable = usageLedgerTable;
    this.apiKeyOwnerTable = apiKeyOwnerTable;

    new CfnOutput(this, "ConversationTableName", {
      value: conversationTable.tableName,
    });
    new CfnOutput(this, "BotTableName", {
      value: botTable.tableName,
    });
    new CfnOutput(this, "UsageLedgerTableName", {
      value: usageLedgerTable.tableName,
    });
    new CfnOutput(this, "ApiKeyOwnerTableName", {
      value: apiKeyOwnerTable.tableName,
    });
```

- [ ] **Step 5: `bedrock-chat-stack.ts`でSSMパラメータ名生成を共通関数に置き換え、新規exportを追加**

`cdk/lib/bedrock-chat-stack.ts`の先頭importに追加(既存の`import { TIdentityProvider, identityProvider } from "./utils/identity-provider";`と同じ相対パス形式):

```typescript
import { rateLimitParamName } from "./utils/parameter-models";
```

既存の:

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

を次のように変更する:

```typescript
    const rateLimitFiveHourParam = new ssm.StringParameter(
      this,
      "RateLimitFiveHourUsdLimitParam",
      {
        parameterName: rateLimitParamName(props.envPrefix, "five-hour"),
        stringValue: "10",
        description:
          "USD cost limit per user over the trailing 5 hours before chat is blocked.",
      }
    );
    const rateLimitSevenDayParam = new ssm.StringParameter(
      this,
      "RateLimitSevenDayUsdLimitParam",
      {
        parameterName: rateLimitParamName(props.envPrefix, "seven-day"),
        stringValue: "336",
        description:
          "USD cost limit per user over the trailing 7 days before chat is blocked.",
      }
    );
```

既存の:

```typescript
    new CfnOutput(this, "LargeMessageBucketName", {
      value: largeMessageBucket.bucketName,
      exportName: `${props.envPrefix}${sepHyphen}BedrockClaudeChatLargeMessageBucketName`,
    });
```

の直後に追加:

```typescript
    new CfnOutput(this, "UsageLedgerTableNameExport", {
      value: database.usageLedgerTable.tableName,
      exportName: `${props.envPrefix}${sepHyphen}BedrockClaudeChatUsageLedgerTableName`,
    });
    new CfnOutput(this, "ApiKeyOwnerTableNameExport", {
      value: database.apiKeyOwnerTable.tableName,
      exportName: `${props.envPrefix}${sepHyphen}BedrockClaudeChatApiKeyOwnerTableName`,
    });
```

- [ ] **Step 6: テストが通ることを確認**

Run: `cd cdk && npx jest cdk.test.ts -t "default stack"`
Expected: PASS

- [ ] **Step 7: 全CDKテストと型チェックを実行**

Run: `cd cdk && npx jest && npx tsc --noEmit`
Expected: 全テストPASS、型エラーなし

- [ ] **Step 8: Commit**

```bash
git add cdk/lib/utils/parameter-models.ts cdk/lib/constructs/database.ts cdk/lib/bedrock-chat-stack.ts cdk/test/cdk.test.ts
git commit -m "feat: add ApiKeyOwnerTable and export it plus UsageLedgerTable for cross-stack use"
```

---

### Task 8: CDK — `ApiPublishmentStack`にレートリミット判定基盤を配線

**Files:**
- Modify: `cdk/lib/api-publishment-stack.ts`
- Modify: `cdk/bin/api-publish.ts`
- Modify: `cdk/lib/constructs/api.ts`

**Interfaces:**
- Consumes: `rateLimitParamName`, `Database.apiKeyOwnerTable`, exported table names(Task 7)

- [ ] **Step 1: `ApiPublishmentStackProps`に新規propsを追加**

`cdk/lib/api-publishment-stack.ts`の既存の:

```typescript
interface ApiPublishmentStackProps extends StackProps {
  readonly bedrockRegion: string;
  readonly enableBedrockCrossRegionInference: boolean;
  readonly conversationTableName: string;
  readonly botTableName: string;
  readonly tableAccessRoleArn: string;
  readonly webAclArn: string;
  readonly usagePlan: apigateway.UsagePlanProps;
  readonly deploymentStage?: string;
  readonly largeMessageBucketName: string;
  readonly corsOptions?: apigateway.CorsOptions;
}
```

を次のように変更する:

```typescript
interface ApiPublishmentStackProps extends StackProps {
  readonly bedrockRegion: string;
  readonly enableBedrockCrossRegionInference: boolean;
  readonly conversationTableName: string;
  readonly botTableName: string;
  readonly usageLedgerTableName: string;
  readonly apiKeyOwnerTableName: string;
  readonly tableAccessRoleArn: string;
  readonly webAclArn: string;
  readonly usagePlan: apigateway.UsagePlanProps;
  readonly deploymentStage?: string;
  readonly largeMessageBucketName: string;
  readonly corsOptions?: apigateway.CorsOptions;
  readonly envPrefix: string;
}
```

- [ ] **Step 2: レートリミットパラメータ名・ARNの導出とIAM権限を追加**

`cdk/lib/api-publishment-stack.ts`の先頭importに追加:

```typescript
import { rateLimitParamName } from "./utils/parameter-models";
```

既存の:

```typescript
    console.log(`usagePlan: ${JSON.stringify(props.usagePlan)}`); // DEBUG

    const deploymentStage = props.deploymentStage ?? "dev";
```

を次のように変更する:

```typescript
    console.log(`usagePlan: ${JSON.stringify(props.usagePlan)}`); // DEBUG

    const deploymentStage = props.deploymentStage ?? "dev";

    // SSM parameter names/ARNs are fully deterministic from envPrefix +
    // account + region, so no cross-stack export/import is needed for them.
    const rateLimitFiveHourParamName = rateLimitParamName(
      props.envPrefix,
      "five-hour"
    );
    const rateLimitSevenDayParamName = rateLimitParamName(
      props.envPrefix,
      "seven-day"
    );
    const rateLimitFiveHourParamArn = `arn:aws:ssm:${Stack.of(this).region}:${
      Stack.of(this).account
    }:parameter${rateLimitFiveHourParamName}`;
    const rateLimitSevenDayParamArn = `arn:aws:ssm:${Stack.of(this).region}:${
      Stack.of(this).account
    }:parameter${rateLimitSevenDayParamName}`;
```

既存の:

```typescript
    handlerRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:*"],
        resources: ["*"],
      })
    );
```

の直後に追加:

```typescript
    handlerRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["ssm:GetParameter"],
        resources: [rateLimitFiveHourParamArn, rateLimitSevenDayParamArn],
      })
    );
```

- [ ] **Step 3: 両ハンドラの環境変数に追加**

`cdk/lib/api-publishment-stack.ts`の`apiHandler`環境変数ブロック内、既存の:

```typescript
        TABLE_ACCESS_ROLE_ARN: props.tableAccessRoleArn,
      },
      role: handlerRole,
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });

    // Handler for SQS consumer
```

を次のように変更する:

```typescript
        TABLE_ACCESS_ROLE_ARN: props.tableAccessRoleArn,
        USAGE_LEDGER_TABLE_NAME: props.usageLedgerTableName,
        API_KEY_OWNER_TABLE_NAME: props.apiKeyOwnerTableName,
        RATE_LIMIT_FIVE_HOUR_PARAM_NAME: rateLimitFiveHourParamName,
        RATE_LIMIT_SEVEN_DAY_PARAM_NAME: rateLimitSevenDayParamName,
      },
      role: handlerRole,
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });

    // Handler for SQS consumer
```

`sqsConsumeHandler`環境変数ブロック内、既存の:

```typescript
          TABLE_ACCESS_ROLE_ARN: props.tableAccessRoleArn,
        },
        role: handlerRole,
        logRetention: logs.RetentionDays.THREE_MONTHS,
      }
    );
```

を次のように変更する:

```typescript
          TABLE_ACCESS_ROLE_ARN: props.tableAccessRoleArn,
          USAGE_LEDGER_TABLE_NAME: props.usageLedgerTableName,
          API_KEY_OWNER_TABLE_NAME: props.apiKeyOwnerTableName,
          RATE_LIMIT_FIVE_HOUR_PARAM_NAME: rateLimitFiveHourParamName,
          RATE_LIMIT_SEVEN_DAY_PARAM_NAME: rateLimitSevenDayParamName,
        },
        role: handlerRole,
        logRetention: logs.RetentionDays.THREE_MONTHS,
      }
    );
```

- [ ] **Step 4: `api-publish.ts`で新規テーブル名をimportし、propsを渡す**

`cdk/bin/api-publish.ts`の既存の:

```typescript
const largeMessageBucketName = cdk.Fn.importValue(
  `${params.envPrefix}${sepHyphen}BedrockClaudeChatLargeMessageBucketName`
);
```

の直後に追加:

```typescript
const usageLedgerTableName = cdk.Fn.importValue(
  `${params.envPrefix}${sepHyphen}BedrockClaudeChatUsageLedgerTableName`
);
const apiKeyOwnerTableName = cdk.Fn.importValue(
  `${params.envPrefix}${sepHyphen}BedrockClaudeChatApiKeyOwnerTableName`
);
```

既存の:

```typescript
new ApiPublishmentStack(app, `ApiPublishmentStack${params.publishedApiId}`, {
  env: {
    region: process.env.CDK_DEFAULT_REGION,
  },
  bedrockRegion: params.bedrockRegion,
  enableBedrockCrossRegionInference: params.enableBedrockCrossRegionInference,
  conversationTableName: conversationTableName,
  botTableName: botTableName,
  tableAccessRoleArn: tableAccessRoleArn,
  webAclArn: webAclArn,
  largeMessageBucketName: largeMessageBucketName,
```

を次のように変更する:

```typescript
new ApiPublishmentStack(app, `ApiPublishmentStack${params.publishedApiId}`, {
  env: {
    region: process.env.CDK_DEFAULT_REGION,
  },
  bedrockRegion: params.bedrockRegion,
  enableBedrockCrossRegionInference: params.enableBedrockCrossRegionInference,
  conversationTableName: conversationTableName,
  botTableName: botTableName,
  usageLedgerTableName: usageLedgerTableName,
  apiKeyOwnerTableName: apiKeyOwnerTableName,
  tableAccessRoleArn: tableAccessRoleArn,
  webAclArn: webAclArn,
  largeMessageBucketName: largeMessageBucketName,
  envPrefix: params.envPrefix,
```

- [ ] **Step 5: 本体バックエンド(`Api`コンストラクト)にも`API_KEY_OWNER_TABLE_NAME`を追加**

`cdk/lib/constructs/api.ts`の既存の:

```typescript
        USAGE_LEDGER_TABLE_NAME: database.usageLedgerTable.tableName,
        RATE_LIMIT_FIVE_HOUR_PARAM_NAME: props.rateLimitFiveHourParam.parameterName,
        RATE_LIMIT_SEVEN_DAY_PARAM_NAME: props.rateLimitSevenDayParam.parameterName,
```

を次のように変更する:

```typescript
        USAGE_LEDGER_TABLE_NAME: database.usageLedgerTable.tableName,
        API_KEY_OWNER_TABLE_NAME: database.apiKeyOwnerTable.tableName,
        RATE_LIMIT_FIVE_HOUR_PARAM_NAME: props.rateLimitFiveHourParam.parameterName,
        RATE_LIMIT_SEVEN_DAY_PARAM_NAME: props.rateLimitSevenDayParam.parameterName,
```

(この変更は`database.apiKeyOwnerTable`への読み書き権限を新たに要求しない — `Database`コンストラクトが`tableAccessRole`に既に`grantReadWriteData`しており、`Api`のLambdaロールは既に`tableAccessRoleArn`への`sts:AssumeRole`を持っているため、環境変数の追加のみで済む。)

- [ ] **Step 6: 全CDKテストと型チェックを実行**

Run: `cd cdk && npx jest && npx tsc --noEmit`
Expected: 全テストPASS、型エラーなし(他のテストで`ApiPublishmentStackProps`/`ApiProps`の必須フィールド不足エラーが出ないか確認する)

- [ ] **Step 7: Commit**

```bash
git add cdk/lib/api-publishment-stack.ts cdk/bin/api-publish.ts cdk/lib/constructs/api.ts
git commit -m "feat: wire rate-limit accounting into the published-API stack"
```

---

### Task 9: バックエンド・CDK全体の最終確認

**Files:** なし(検証のみ)

- [ ] **Step 1: バックエンドの新規・変更ファイルのテストを一括実行**

Run:

```bash
cd backend && poetry run pytest \
  tests/test_repositories/test_api_key_owner.py \
  tests/test_user.py \
  tests/test_usecases/test_rate_limit.py \
  tests/test_usecases/test_publication.py \
  tests/test_usecases/test_post_process_result_price.py \
  tests/test_routes/test_published_api.py \
  -v
```

Expected: 全てPASS(5 + 4 + 6 + 4 + 2 + 4 = 25 tests)

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
Expected: Task 1〜8で意図的にcommitしたファイル以外に変更が残っていない
