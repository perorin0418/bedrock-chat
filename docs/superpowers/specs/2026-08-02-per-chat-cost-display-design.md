# チャットのコスト（ドル）表示 設計書

## 背景・目的

ユーザーがチャットした際に、そのやり取りにいくらコストがかかったのかをドルベースで画面上に表示できるようにする。

バックエンドには既にトークン数から料金を算出する仕組み（`calculate_price()` in `backend/app/bedrock.py`）が存在し、1ターンごとの料金（`price`）は `OnStopInput` として算出済みである。また、会話全体の累計コストも `ConversationModel.total_price` として DynamoDB に既に保存・蓄積されている。

これらの値は**どちらも API レスポンスに含まれておらず、フロントエンドには一切渡っていない**（WebSocket の `STREAMING_END` イベントでは `price` が送信されているが、`usePostMessageStreaming.ts` 側で受信後に破棄されている）。本機能は、既に計算済みのこれらの値を API・UI まで配線して露出させる作業が中心となる。

## 表示する内容

1. **メッセージ（発言）単位のコスト**: assistant の各返答ごとに、その返答生成にかかった料金を表示する
2. **会話全体の累計コスト**: その会話でこれまでにかかった料金の合計を表示する

どちらも DynamoDB に永続化し、会話をリロードしたり別端末で開いたりしても表示され続ける。

## バックエンド変更

### データモデル

- `MessageModel`（`backend/app/repositories/models/conversation.py`）に `price: float | None = None` を追加する。`used_chunks` と同様の後方互換パターン（デフォルト値ありのため、既存 DynamoDB アイテムに当該キーが無くても pydantic のデフォルトで補完される）。

### 料金の設定・蓄積

- `post_process_result()`（`backend/app/usecases/chat.py`）内で、conversation の累計に加算している箇所（`conversation.total_price += result["price"]`）の近くで、生成された assistant メッセージに `message.price = result["price"]` を設定する。
- `post_process_result()` は Strands 実装・legacy 実装、ストリーミング・非ストリーミングいずれの経路からも共通で呼び出される関数のため、この1箇所の変更で全経路に対応できる。

### API スキーマ

- `MessageOutput`（`backend/app/routes/schemas/conversation.py`）に `price: float | None` を追加する。
- `Conversation`（同ファイル）に `total_price: float` を追加する。
- `chat_output_from_message()` と `fetch_conversation()`（`backend/app/usecases/chat.py`）の両方で、上記フィールドをそれぞれ `message.price` / `conversation.total_price` から詰める。

## フロントエンド変更

### 型定義

- `MessageContent`（`frontend/src/@types/conversation.d.ts`）に `price?: number | null` を**オプショナル**で追加する。必須にすると `MessageUtils.test.ts` や `ChatMessage.stories.tsx` など既存のテストフィクスチャ・Storybook（約90箇所）の修正が必要になるため、オプショナルとする。バックエンド側のデフォルト `None` とも整合する。
- `Conversation`（同ファイル）に `totalPrice: number` を追加する。

### データの受け渡し

- `convertMessageMapToArray`（`frontend/src/utils/MessageUtils.ts`）内の2箇所（メッセージオブジェクト構築部分）で `price: messageContent.price` を転記する。
- 会話全体の `totalPrice` は `useChat.ts` 内で `conversationApi.getConversation()` から取得している `data`（`Conversation` 型）から直接参照できる。既存の `mutate()` 呼び出し（チャット送信後の再取得）により自動的に最新値が反映される。

### UI 表示

- **メッセージ単位**: `ChatMessage.tsx` の assistant 用アクション行（いいねボタン・コピーボタンが並んでいる箇所）に、小さいテキストで料金を追加表示する（例: `$0.0032`）。
- **会話全体累計**: `ChatPage.tsx` の入力欄（`InputChatContent`）直上に、`messages.length > 0` の場合のみ「合計コスト: $0.0123」のような表示を追加する。

### フォーマット

- 両方とも小数点以下4桁固定（`'$' + price.toFixed(4)`）とする。管理画面（`AdminSharedBotAnalyticsPage.tsx`）は2桁表示だが、1メッセージ単位のコストは $0.01 未満になることが多く、2桁では `$0.00` に丸められて情報が消えるケースが多いため、4桁に統一する。
- 共通の整形処理は小さなユーティリティ関数として切り出し、`ChatMessage.tsx` と `ChatPage.tsx` の2箇所から参照する。

### i18n

- `frontend/src/i18n/en/index.ts` と `frontend/src/i18n/ja/index.ts` にのみ表示用の翻訳キーを追加する。他言語ファイルは追加しない。`fallbackLng: 'en'`（`frontend/src/i18n/index.ts`）が設定されているため、未翻訳言語では自動的に英語表示にフォールバックされる。

## スコープ外

- 他言語（en/ja 以外）への翻訳キー追加
- 管理画面（bot/user 単位の集計コスト表示）の変更
- 過去に生成済みで `price` が保存されていない古いメッセージへの遡及的な料金計算・補完（`price: null` として扱い、金額は表示しない）

## テスト方針

- バックエンド: `MessageModel` / `ConversationModel` の新フィールドのデフォルト値・後方互換性、`post_process_result()` での `price` 設定、`chat_output_from_message()` / `fetch_conversation()` でのスキーマ変換を検証する。既存テスト（`test_chat.py` など）が新フィールド追加によって壊れないことを確認する。
- フロントエンド: `convertMessageMapToArray` の `price` 転記、金額フォーマット関数の単体テストを追加する。
