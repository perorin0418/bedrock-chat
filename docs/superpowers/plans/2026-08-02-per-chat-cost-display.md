# チャットのコスト（ドル）表示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** チャットの各返答（発言単位）とその会話全体でこれまでにかかった料金（ドル）を、DynamoDBに永続化した上でチャット画面に表示する。

**Architecture:** バックエンドは既に `calculate_price()`（`backend/app/bedrock.py`）でトークン数から料金を算出しており、1ターンごとの `price` は `OnStopInput` として、会話全体の累計は `ConversationModel.total_price` として既に計算・蓄積されている。今回の作業は、この既存の値を (1) `MessageModel` に保存し、(2) API スキーマ（`MessageOutput` / `Conversation`）に載せ、(3) フロントエンドの型・データ変換・表示コンポーネントまで配線する、という3層の配線作業が中心。ビジネスロジックの新規実装はほぼ無い。

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2（backend）、React / TypeScript / vitest（frontend）

## Global Constraints

- `MessageContent`（frontend の型）に追加する `price` フィールドは **オプショナル**にする。既存の `MessageUtils.test.ts`（約90箇所のフィクスチャ）や `ChatMessage.stories.tsx` を変更しないため。
- 金額表示のフォーマットは両方（メッセージ単位・会話累計）とも `'$' + price.toFixed(4)`（小数点以下4桁固定）で統一する。
- i18n の翻訳キーは `en` と `ja` にのみ追加する。他言語は `fallbackLng: 'en'`（`frontend/src/i18n/index.ts`）により自動フォールバックされるため対応不要。
- 過去に生成済みで `price` が保存されていないメッセージ・会話は `null`/`0` として扱い、金額を表示しない（後方互換）。
- バックエンドのテストは実際の AWS 認証情報が無くても実行できるユニットテスト（モック使用）のみを追加する。`backend/tests/test_usecases/test_chat.py` のような実際に Bedrock を呼び出す既存の統合テストは、本計画では実行せず変更もしない。

---

### Task 1: `MessageModel` に `price` フィールドを追加する

**Files:**
- Modify: `backend/app/repositories/models/conversation.py:708-720`
- Test: `backend/tests/test_repositories/test_conversation.py`

**Interfaces:**
- Produces: `MessageModel.price: float | None`（デフォルト `None`）。Task 2, Task 3 がこのフィールドに書き込み・読み出しを行う。

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/test_repositories/test_conversation.py` の `test_store_and_find_conversation` 内、`MessageModel(...)` の呼び出し（`thinking_log=[...]` の直後）に `price=0.0123,` を追加する。

対象箇所（`role="user"` の `MessageModel` 呼び出し、108行目付近の `thinking_log=[...]` の直後）を以下のように変更:

```python
                    thinking_log=[
                        SimpleMessageModel(
                            role="agent",
                            content=[
                                ToolUseContentModel(
                                    content_type="toolUse",
                                    body=ToolUseContentModelBody(
                                        tool_use_id="xyz1234",
                                        name="internet_search",
                                        input={
                                            "query": "Google news",
                                            "country": "us-en",
                                            "time_limit": "d",
                                        },
                                    ),
                                )
                            ],
                        )
                    ],
                    price=0.0123,
                )
            },
```

さらに、同じテスト内の `self.assertEqual(len(message_map["a"].used_chunks), 1)  # type: ignore` の直後に以下のアサーションを追加する:

```python
        self.assertEqual(message_map["a"].price, 0.0123)
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run（`backend/` ディレクトリで実行）: `poetry run pytest tests/test_repositories/test_conversation.py::TestConversationRepository::test_store_and_find_conversation -v`

Expected: FAIL（`MessageModel` に `price` という引数/フィールドが無いため、Pydantic の `ValidationError`、または属性アクセス時の `AttributeError` のいずれかで失敗する）

- [ ] **Step 3: 最小限の実装を行う**

`backend/app/repositories/models/conversation.py:708-720` の `MessageModel` クラス定義を以下のように変更する（`thinking_log` フィールドの直後に `price` を追加):

```python
class MessageModel(BaseModel):
    role: str
    content: list[ContentModel]
    model: type_model_name
    children: list[str]
    parent: str | None
    create_time: float
    feedback: FeedbackModel | None = None
    used_chunks: list[ChunkModel] | None = None
    thinking_log: list[SimpleMessageModel] | None = Field(
        default=None, description="Only available for agent."
    )
    price: float | None = Field(
        default=None, description="Cost of generating this message, in USD."
    )
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `poetry run pytest tests/test_repositories/test_conversation.py::TestConversationRepository::test_store_and_find_conversation -v`

Expected: PASS

- [ ] **Step 5: 既存テストが壊れていないことを確認する**

Run: `poetry run pytest tests/test_repositories/test_conversation.py -v`

Expected: 全テスト PASS（`test_store_and_find_large_conversation` など、`price` を指定していない既存の `MessageModel` 呼び出しも、デフォルト値 `None` により引き続き動作すること）

- [ ] **Step 6: コミット**

```bash
git add backend/app/repositories/models/conversation.py backend/tests/test_repositories/test_conversation.py
git commit -m "feat: add price field to MessageModel for per-message cost tracking"
```

---

### Task 2: `post_process_result` で assistant メッセージに `price` を設定する

**Files:**
- Modify: `backend/app/usecases/chat.py:527-530`
- Test: Create `backend/tests/test_usecases/test_post_process_result_price.py`

**Interfaces:**
- Consumes: `MessageModel.price`（Task 1）
- Produces: `post_process_result()` が返す `message.price` に、その返答生成にかかった料金がセットされている。Task 3 の `chat_output_from_message` / `fetch_conversation` はこの値を読み出す。

- [ ] **Step 1: 失敗するテストを書く**

Create `backend/tests/test_usecases/test_post_process_result_price.py`:

```python
import os
import sys
import unittest
from unittest.mock import patch

os.environ["BEDROCK_REGION"] = "us-east-1"

sys.path.insert(0, ".")

from app.repositories.models.conversation import (
    ConversationModel,
    MessageModel,
    TextContentModel,
)
from app.routes.schemas.conversation import ChatInput, MessageInput, TextContent
from app.stream import OnStopInput
from app.usecases.chat import post_process_result
from tests.test_usecases.utils.user_factory import create_test_user

MODEL = "claude-v3.5-sonnet"


class TestPostProcessResultPrice(unittest.TestCase):
    @patch("app.usecases.chat.store_conversation")
    def test_post_process_result_sets_message_price_and_accumulates_total(
        self, mock_store_conversation
    ):
        conversation = ConversationModel(
            id="conv1",
            create_time=1700000000.0,
            title="Test Conversation",
            total_price=0.01,
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

        updated_conversation, updated_message = post_process_result(
            result=result,
            message_for_continue_generate=None,
            conversation=conversation,
            user_msg_id="user1",
            bot=None,
            user=create_test_user("test-user-price"),
            chat_input=chat_input,
            search_results=[],
            related_documents=[],
            on_stop=None,
        )

        self.assertEqual(updated_message.price, 0.0042)
        # 0.01 (既存の累計) + 0.0042 (今回の料金)
        self.assertAlmostEqual(updated_conversation.total_price, 0.0142)
        mock_store_conversation.assert_called_once()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `poetry run pytest tests/test_usecases/test_post_process_result_price.py -v`

Expected: FAIL（`updated_message.price` が `None` のままで `0.0042` と一致しない）

- [ ] **Step 3: 最小限の実装を行う**

`backend/app/usecases/chat.py` の `post_process_result` 内、527行目付近の以下の部分:

```python
    message = result["message"]
    stop_reason = result["stop_reason"]

    conversation.total_price += result["price"]
```

を次のように変更する:

```python
    message = result["message"]
    stop_reason = result["stop_reason"]

    message.price = result["price"]
    conversation.total_price += result["price"]
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `poetry run pytest tests/test_usecases/test_post_process_result_price.py -v`

Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add backend/app/usecases/chat.py backend/tests/test_usecases/test_post_process_result_price.py
git commit -m "feat: set price on assistant message in post_process_result"
```

---

### Task 3: API スキーマに `price` / `total_price` を追加し配線する

**Files:**
- Modify: `backend/app/routes/schemas/conversation.py:191-199` (`MessageOutput`)
- Modify: `backend/app/routes/schemas/conversation.py:248-256` (`Conversation`)
- Modify: `backend/app/usecases/chat.py:600-634` (`chat_output_from_message`)
- Modify: `backend/app/usecases/chat.py:699-756` (`fetch_conversation`)
- Test: Create `backend/tests/test_usecases/test_chat_output_price.py`

**Interfaces:**
- Consumes: `MessageModel.price`（Task 1, 2）, `ConversationModel.total_price`（既存）
- Produces: `MessageOutput.price: float | None`, `Conversation.total_price: float` — フロントエンドの GET `/conversation/{id}` レスポンスに含まれる。

- [ ] **Step 1: 失敗するテストを書く**

Create `backend/tests/test_usecases/test_chat_output_price.py`:

```python
import os
import sys
import unittest
from unittest.mock import patch

os.environ["BEDROCK_REGION"] = "us-east-1"

sys.path.insert(0, ".")

from app.repositories.models.conversation import (
    ConversationModel,
    MessageModel,
    TextContentModel,
)
from app.usecases.chat import chat_output_from_message, fetch_conversation

MODEL = "claude-v3.5-sonnet"


class TestChatOutputFromMessagePrice(unittest.TestCase):
    def test_chat_output_from_message_includes_price(self):
        conversation = ConversationModel(
            id="conv1",
            create_time=1700000000.0,
            title="Test Conversation",
            total_price=0.0055,
            message_map={},
            last_message_id="assistant1",
            bot_id=None,
            should_continue=False,
        )
        message = MessageModel(
            role="assistant",
            content=[TextContentModel(content_type="text", body="hi there")],
            model=MODEL,
            children=[],
            parent="user1",
            create_time=1700000000.0,
            price=0.0055,
        )

        output = chat_output_from_message(conversation=conversation, message=message)

        self.assertEqual(output.message.price, 0.0055)


class TestFetchConversationTotalPrice(unittest.TestCase):
    @patch("app.usecases.chat.find_conversation_by_id")
    def test_fetch_conversation_includes_total_price(
        self, mock_find_conversation_by_id
    ):
        mock_find_conversation_by_id.return_value = ConversationModel(
            id="conv1",
            create_time=1700000000.0,
            title="Test Conversation",
            total_price=0.0512,
            message_map={
                "system": MessageModel(
                    role="system",
                    content=[TextContentModel(content_type="text", body="")],
                    model=MODEL,
                    children=["user1"],
                    parent=None,
                    create_time=1700000000.0,
                ),
                "user1": MessageModel(
                    role="user",
                    content=[TextContentModel(content_type="text", body="hi")],
                    model=MODEL,
                    children=[],
                    parent="system",
                    create_time=1700000000.0,
                ),
            },
            last_message_id="user1",
            bot_id=None,
            should_continue=False,
        )

        conv = fetch_conversation("user1", "conv1")

        self.assertEqual(conv.total_price, 0.0512)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `poetry run pytest tests/test_usecases/test_chat_output_price.py -v`

Expected: FAIL（`MessageOutput` / `Conversation` に `price` / `total_price` フィールドが無いため `AttributeError`）

- [ ] **Step 3: 最小限の実装を行う**

`backend/app/routes/schemas/conversation.py:191-199` の `MessageOutput` を変更:

```python
class MessageOutput(BaseSchema):
    role: str = Field(..., description="Role of the message. Either `user` or `bot`.")
    content: list[Content]
    model: type_model_name
    children: list[str]
    feedback: FeedbackOutput | None
    used_chunks: list[Chunk] | None
    parent: str | None
    thinking_log: list[SimpleMessage] | None
    price: float | None = Field(
        default=None, description="Cost of generating this message, in USD."
    )
```

`backend/app/routes/schemas/conversation.py:248-256` の `Conversation` を変更:

```python
class Conversation(BaseSchema):
    id: str
    title: str
    create_time: float
    message_map: dict[str, MessageOutput]
    last_message_id: str
    bot_id: str | None
    should_continue: bool
    total_price: float = Field(
        default=0.0, description="Total cost of this conversation so far, in USD."
    )
```

`backend/app/usecases/chat.py` の `chat_output_from_message`（600行目付近）の `MessageOutput(...)` 呼び出しに `price=message.price,` を追加:

```python
def chat_output_from_message(
    conversation: ConversationModel,
    message: MessageModel,
) -> ChatOutput:
    return ChatOutput(
        conversation_id=conversation.id,
        create_time=conversation.create_time,
        message=MessageOutput(
            role=message.role,
            content=[c.to_content() for c in message.content],
            model=message.model,
            children=message.children,
            parent=message.parent,
            feedback=None,
            used_chunks=(
                [
                    Chunk(
                        content=c.content,
                        content_type=c.content_type,
                        source=c.source,
                        rank=c.rank,
                    )
                    for c in message.used_chunks
                ]
                if message.used_chunks
                else None
            ),
            thinking_log=(
                [m.to_schema() for m in message.thinking_log]
                if message.thinking_log
                else None
            ),
            price=message.price,
        ),
        bot_id=conversation.bot_id,
    )
```

`backend/app/usecases/chat.py` の `fetch_conversation`（699行目付近）内、`message_map` を構築する `MessageOutput(...)` に `price=message.price,` を追加し、末尾の `Conversation(...)` 呼び出しに `total_price=conversation.total_price,` を追加:

```python
def fetch_conversation(user_id: str, conversation_id: str) -> Conversation:
    conversation = find_conversation_by_id(user_id, conversation_id)

    message_map = {
        message_id: MessageOutput(
            role=message.role,
            content=[c.to_content() for c in message.content],
            model=message.model,
            children=message.children,
            parent=message.parent,
            feedback=(
                FeedbackOutput(
                    thumbs_up=message.feedback.thumbs_up,
                    category=message.feedback.category,
                    comment=message.feedback.comment,
                )
                if message.feedback
                else None
            ),
            used_chunks=(
                [
                    Chunk(
                        content=c.content,
                        content_type=c.content_type,
                        source=c.source,
                        rank=c.rank,
                    )
                    for c in message.used_chunks
                ]
                if message.used_chunks
                else None
            ),
            thinking_log=(
                [m.to_schema() for m in message.thinking_log]
                if message.thinking_log
                else None
            ),
            price=message.price,
        )
        for message_id, message in conversation.message_map.items()
    }
    # Omit instruction
    if "instruction" in message_map:
        for c in message_map["instruction"].children:
            message_map[c].parent = "system"
        message_map["system"].children = message_map["instruction"].children

        del message_map["instruction"]

    output = Conversation(
        id=conversation_id,
        title=conversation.title,
        create_time=conversation.create_time,
        last_message_id=conversation.last_message_id,
        message_map=message_map,
        bot_id=conversation.bot_id,
        should_continue=conversation.should_continue,
        total_price=conversation.total_price,
    )
    return output
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `poetry run pytest tests/test_usecases/test_chat_output_price.py -v`

Expected: PASS

- [ ] **Step 5: コミット**

```bash
git add backend/app/routes/schemas/conversation.py backend/app/usecases/chat.py backend/tests/test_usecases/test_chat_output_price.py
git commit -m "feat: expose per-message price and conversation total_price via API"
```

---

### Task 4: フロントエンドの型定義と金額フォーマットユーティリティを追加する

**Files:**
- Modify: `frontend/src/@types/conversation.d.ts:90-97` (`MessageContent`)
- Modify: `frontend/src/@types/conversation.d.ts:160-163` (`Conversation`)
- Create: `frontend/src/utils/PriceUtils.ts`
- Test: Create `frontend/src/utils/__tests__/PriceUtils.test.ts`

**Interfaces:**
- Produces: `MessageContent.price?: number | null`, `Conversation.totalPrice: number`, `formatPrice(price: number): string`（例: `formatPrice(0.0032)` → `"$0.0032"`）。Task 5〜9 がこれらを利用する。

- [ ] **Step 1: 失敗するテストを書く**

Create `frontend/src/utils/__tests__/PriceUtils.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import { formatPrice } from '../PriceUtils';

describe('formatPrice', () => {
  it('formats a sub-cent price with 4 decimal places', () => {
    expect(formatPrice(0.0032)).toBe('$0.0032');
  });

  it('formats zero', () => {
    expect(formatPrice(0)).toBe('$0.0000');
  });

  it('rounds to 4 decimal places', () => {
    expect(formatPrice(1.23456)).toBe('$1.2346');
  });
});
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run（`frontend/` ディレクトリで実行）: `npx vitest run src/utils/__tests__/PriceUtils.test.ts`

Expected: FAIL（`../PriceUtils` モジュールが存在しない）

- [ ] **Step 3: 最小限の実装を行う**

Create `frontend/src/utils/PriceUtils.ts`:

```typescript
export const formatPrice = (price: number): string => {
  return `$${price.toFixed(4)}`;
};
```

`frontend/src/@types/conversation.d.ts:90-97` の `MessageContent` を変更:

```typescript
export type MessageContent = {
  role: Role;
  content: Content[];
  model: Model;
  feedback: null | Feedback;
  usedChunks: null | UsedChunk[];
  thinkingLog: null | SimpleMessage[];
  price?: number | null;
};
```

`frontend/src/@types/conversation.d.ts:160-163` の `Conversation` を変更:

```typescript
export type Conversation = ConversationMeta & {
  messageMap: MessageMap;
  shouldContinue: boolean;
  totalPrice: number;
};
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `npx vitest run src/utils/__tests__/PriceUtils.test.ts`

Expected: PASS

- [ ] **Step 5: 型チェックを実行する**

Run: `npx tsc --noEmit`

Expected: 既存のエラーが増えていないこと（`price` はオプショナルフィールドのため、既存のテストフィクスチャや `MessageContent` リテラルはそのままコンパイルが通る）

- [ ] **Step 6: コミット**

```bash
git add frontend/src/@types/conversation.d.ts frontend/src/utils/PriceUtils.ts frontend/src/utils/__tests__/PriceUtils.test.ts
git commit -m "feat: add price types and USD formatting utility"
```

---

### Task 5: `convertMessageMapToArray` で `price` を転記する

**Files:**
- Modify: `frontend/src/utils/MessageUtils.ts:42-53,84-95`
- Test: Modify `frontend/src/utils/__tests__/MessageUtils.test.ts`

**Interfaces:**
- Consumes: `MessageContent.price?: number | null`（Task 4）
- Produces: `DisplayMessageContent.price?: number | null` が実際に値を持つようになる（型自体は `MessageContent` を継承しているため Task 4 で既に定義済み）。Task 7, 8 がこの値を表示に使う。

- [ ] **Step 1: 失敗するテストを書く**

`frontend/src/utils/__tests__/MessageUtils.test.ts` の先頭の `describe('convertMessageMapToArray', () => {` の直後に、以下のテストケースを追加する:

```typescript
  it('price を転記する', () => {
    const data: MessageMap = {
      '1': {
        role: 'assistant',
        model: 'claude-v3-haiku',
        content: [
          {
            contentType: 'text',
            body: 'message-1',
          },
        ],
        parent: null,
        children: [],
        feedback: null,
        usedChunks: null,
        thinkingLog: null,
        price: 0.0042,
      },
    };
    const expected: DisplayMessageContent[] = [
      {
        id: '1',
        role: 'assistant',
        model: 'claude-v3-haiku',
        content: [
          {
            body: 'message-1',
            contentType: 'text',
          },
        ],
        parent: null,
        children: [],
        sibling: ['1'],
        feedback: null,
        usedChunks: null,
        thinkingLog: null,
        price: 0.0042,
      },
    ];
    const actual = convertMessageMapToArray(data, '1');
    expect(actual).toEqual(expected);
  });

```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `npx vitest run src/utils/__tests__/MessageUtils.test.ts`

Expected: FAIL（`convertMessageMapToArray` が `price` を転記していないため、`actual[0].price` が `undefined` になり `expected` と一致しない）

- [ ] **Step 3: 最小限の実装を行う**

`frontend/src/utils/MessageUtils.ts` 内、2箇所ある `messageArray.unshift({...})` / `messageArray.push({...})` の各オブジェクトリテラルに `price: messageContent.price,` を追加する（`thinkingLog: messageContent.thinkingLog,` の直後）。

1箇所目（42-53行目付近、`unshift`）:

```typescript
      messageArray.unshift({
        id: key,
        model: messageContent.model,
        role: messageContent.role,
        content: messageContent.content,
        parent: messageContent.parent,
        children: messageContent.children,
        sibling: [],
        feedback: messageContent.feedback,
        usedChunks: messageContent.usedChunks,
        thinkingLog: messageContent.thinkingLog,
        price: messageContent.price,
      });
```

2箇所目（84-95行目付近、`push`）:

```typescript
      messageArray.push({
        id: key,
        model: messageContent.model,
        role: messageContent.role,
        content: messageContent.content,
        parent: messageContent.parent,
        children: messageContent.children,
        sibling: [],
        feedback: messageContent.feedback,
        usedChunks: messageContent.usedChunks,
        thinkingLog: messageContent.thinkingLog,
        price: messageContent.price,
      });
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `npx vitest run src/utils/__tests__/MessageUtils.test.ts`

Expected: PASS（新規テストに加え、既存の90件近いテストケースも `price` を指定していないため `undefined` になるが、`toEqual` は `undefined` のプロパティを無視するため引き続き PASS する）

- [ ] **Step 5: コミット**

```bash
git add frontend/src/utils/MessageUtils.ts frontend/src/utils/__tests__/MessageUtils.test.ts
git commit -m "feat: propagate message price through convertMessageMapToArray"
```

---

### Task 6: `useChat.ts` で会話全体の `totalPrice` を公開する

**Files:**
- Modify: `frontend/src/hooks/useChat.ts:691` (return オブジェクト)

**Interfaces:**
- Consumes: `Conversation.totalPrice`（Task 4）。既存の `data`（`conversationApi.getConversation(conversationId)` の結果、`useChat.ts:271-275`）から取得。
- Produces: `useChat()` フックの戻り値に `totalPrice: number | null` が追加される。Task 9 が利用する。

この変更は既存の `useChat` フックの戻り値オブジェクトに1行追加するのみ。`useChat.ts` 自体に専用のユニットテストは既存コードにも存在しないため、TDD のテスト先行サイクルは適用せず、実装後に型チェックで検証する（実際の表示確認は Task 9 で行う）。

- [ ] **Step 1: 実装を行う**

`frontend/src/hooks/useChat.ts:671-691` の `return` オブジェクト内、`supportReasoning,` の直後に以下を追加する:

```typescript
    supportReasoning,
    totalPrice: data?.totalPrice ?? null,
```

- [ ] **Step 2: 型チェックを実行して確認する**

Run（`frontend/` ディレクトリで実行）: `npx tsc --noEmit`

Expected: エラーなし（`data` は `Conversation | undefined` 型であり、`data?.totalPrice` は Task 4 で追加した `totalPrice: number` フィールドを正しく参照できる）

- [ ] **Step 3: コミット**

```bash
git add frontend/src/hooks/useChat.ts
git commit -m "feat: expose conversation totalPrice from useChat"
```

---

### Task 7: `en` / `ja` に翻訳キーを追加する

**Files:**
- Modify: `frontend/src/i18n/en/index.ts:672`
- Modify: `frontend/src/i18n/ja/index.ts:678`

**Interfaces:**
- Produces: `t('chat.label.messageCost', { price })`, `t('chat.label.totalCost', { price })`。Task 8, 9 が利用する。

- [ ] **Step 1: 実装を行う**

`frontend/src/i18n/en/index.ts:672` の `conversationHistory: {` の直前に以下を追加する:

```typescript
    chat: {
      label: {
        messageCost: 'Cost: {{price}}',
        totalCost: 'Total cost: {{price}}',
      },
    },
    conversationHistory: {
```

`frontend/src/i18n/ja/index.ts:678` の `conversationHistory: {` の直前に以下を追加する:

```typescript
    chat: {
      label: {
        messageCost: 'コスト: {{price}}',
        totalCost: '合計コスト: {{price}}',
      },
    },
    conversationHistory: {
```

- [ ] **Step 2: 動作確認**

Run: `npx tsc --noEmit`（i18n ファイルも TypeScript として型チェックされるため、構文ミスがあればここで検出される）

Expected: エラーなし

- [ ] **Step 3: コミット**

```bash
git add frontend/src/i18n/en/index.ts frontend/src/i18n/ja/index.ts
git commit -m "feat: add i18n keys for chat cost display (en/ja)"
```

---

### Task 8: `ChatMessage.tsx` にメッセージ単位のコストを表示する

**Files:**
- Modify: `frontend/src/components/ChatMessage.tsx:1-32` (imports), `:363-387` (assistant アクション行)

**Interfaces:**
- Consumes: `DisplayMessageContent.price?: number | null`（Task 5 経由で値が入る）, `formatPrice`（Task 4）, `t('chat.label.messageCost', ...)`（Task 7）

- [ ] **Step 1: 実装を行う**

`frontend/src/components/ChatMessage.tsx:31` の import 群に以下を追加する（`import { convertUsedChunkToRelatedDocument } from '../utils/MessageUtils';` の直後):

```typescript
import { convertUsedChunkToRelatedDocument } from '../utils/MessageUtils';
import { formatPrice } from '../utils/PriceUtils';
```

`frontend/src/components/ChatMessage.tsx:363-387` の assistant 用アクション行を以下のように変更する:

```typescript
          {chatContent?.role === 'assistant' && (
            <div className="flex items-center">
              {chatContent.price != null && (
                <span className="mr-1 text-xs text-dark-gray dark:text-light-gray">
                  {t('chat.label.messageCost', {
                    price: formatPrice(chatContent.price),
                  })}
                </span>
              )}
              <ButtonIcon
                className="text-dark-gray dark:text-light-gray"
                onClick={() => setIsFeedbackOpen(true)}>
                {chatContent.feedback && !chatContent.feedback.thumbsUp ? (
                  <PiThumbsDownFill />
                ) : (
                  <PiThumbsDown />
                )}
              </ButtonIcon>
              <ButtonCopy
                className="text-dark-gray dark:text-light-gray"
                text={
                  chatContent.content.find((c) => c.contentType === 'text')
                    ? (
                        chatContent.content.find(
                          (c) => c.contentType === 'text'
                        ) as TextContent
                      ).body
                    : ''
                }
              />
            </div>
          )}
```

- [ ] **Step 2: 型チェックを実行する**

Run: `npx tsc --noEmit`

Expected: エラーなし

- [ ] **Step 3: Storybook で表示を目視確認する**

Run（`frontend/` ディレクトリで実行）: `npm run storybook`

Expected: `ChatMessage` の Storybook（`frontend/src/components/ChatMessage.stories.tsx`）を開き、既存のストーリー（`price` 未設定）ではコスト表示が出ないこと、および開発者ツール等で `price` を持つ props を渡した場合に `コスト: $0.0032` のような表示が出ることを確認する。

- [ ] **Step 4: コミット**

```bash
git add frontend/src/components/ChatMessage.tsx
git commit -m "feat: display per-message cost in ChatMessage"
```

---

### Task 9: `ChatPage.tsx` に会話全体の累計コストを表示する

**Files:**
- Modify: `frontend/src/pages/ChatPage.tsx:164-186` (useChat の destructure), `:686` (入力欄直上への表示追加), imports

**Interfaces:**
- Consumes: `totalPrice: number | null`（Task 6）, `formatPrice`（Task 4）, `t('chat.label.totalCost', ...)`（Task 7）

- [ ] **Step 1: 実装を行う**

`frontend/src/pages/ChatPage.tsx` の import 群に以下を追加する:

```typescript
import { formatPrice } from '../utils/PriceUtils';
```

`frontend/src/pages/ChatPage.tsx:164-186` の `useChat()` の destructure に `totalPrice` を追加する:

```typescript
  const {
    streamingState,
    conversationError,
    postingMessage,
    newChat,
    postChat,
    messages,
    conversationId,
    setConversationId,
    hasError,
    retryPostChat,
    setCurrentMessageId,
    regenerate,
    continueGenerate,
    getPostedModel,
    loadingConversation,
    getShouldContinue,
    relatedDocuments,
    giveFeedback,
    reasoningEnabled,
    setReasoningEnabled,
    supportReasoning,
    totalPrice,
  } = useChat();
```

`frontend/src/pages/ChatPage.tsx:686` の `<InputChatContent` の直前に以下を追加する:

```typescript
        {messages.length > 0 && totalPrice != null && (
          <div className="mb-1 text-xs text-dark-gray dark:text-light-gray">
            {t('chat.label.totalCost', { price: formatPrice(totalPrice) })}
          </div>
        )}
        <InputChatContent
```

- [ ] **Step 2: 型チェックを実行する**

Run: `npx tsc --noEmit`

Expected: エラーなし

- [ ] **Step 3: 実際にチャット画面で動作確認する**

Run（`frontend/` ディレクトリで実行）: `npm run dev`

手順:
1. 開発サーバーにアクセスし、任意のボット（またはデフォルトチャット）でメッセージを送信する
2. 返答が完了した後、返答の下（いいね・コピーボタンの並び）に `コスト: $0.00XX` のような表示が出ることを確認する
3. 入力欄の直上に `合計コスト: $0.00XX` のような表示が出ることを確認する
4. ページをリロードし、両方の表示が引き続き残っていることを確認する（DynamoDB からの永続化を確認）
5. 2通目のメッセージを送信し、合計コストが加算されて増えることを確認する

- [ ] **Step 4: コミット**

```bash
git add frontend/src/pages/ChatPage.tsx
git commit -m "feat: display conversation total cost above chat input"
```
