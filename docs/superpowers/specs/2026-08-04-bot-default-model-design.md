# ボット単位のデフォルトモデル指定 設計書

## 背景・目的

チャット画面上部にはボットが使用可能な複数モデルを切り替えるUIがあるが、初期表示にどのモデルを選ぶかはボット側で指定できず、暗黙的に「有効化されているモデルのうち、システム全体で固定されているモデル定義順（`AVAILABLE_MODEL_KEYS` / `type_model_name`）で最初に出てくるもの」が使われている（`frontend/src/hooks/useModel.ts:355-372` の `getDefaultModel()`）。

ボットの「使用可能なモデル一覧」（`active_models`）自体が、順序付きリストではなくモデルIDをキーにした有効/無効の辞書として保持されているため、ボット側には元々「順序」や「デフォルト」という概念が存在しない。

なお、システム全体のデフォルトモデルは環境変数 `DEFAULT_MODEL` を通じて既に `GET /config/global` の `defaultModel` として実装済みである（`backend/app/usecases/global_config.py:11,46-48`）。本機能は、これと同様の考え方をボット単位に拡張し、ボット設定画面で明示的にデフォルトモデルを指定できるようにする。

## バックエンド変更

### データモデル

- `BotModel`（`backend/app/repositories/models/custom_bot.py`）に `default_model: type_model_name` を追加する。型は `backend/app/routes/schemas/conversation.py` の既存 `type_model_name` Literal を再利用する（`backend/app/routes/schemas/bot_kb.py` の `parsing_model: type_kb_parsing_model` と同様のパターン）。
- DynamoDB には `DefaultModel` 属性として保存する。

### 既存ボットとの後方互換（読み込み時の自動補完）

- DynamoDB アイテムに `DefaultModel` 属性が存在しない場合（＝本機能リリース前に作成されたボット）、読み込み時にその場で「`active_models` 上で有効化されているモデルのうち、定義順で先頭のもの」を `default_model` として補完して返す。
- この補完はメモリ上でのみ行い、DB自体は書き換えない。次回そのボットが PATCH で保存されたタイミングで、実際の値が `DefaultModel` 属性として書き込まれる。
- 事前の一括マイグレーションスクリプトは実施しない。

### 保存時の自動整合（安全側フォールバック）

- `default_model` に指定された値が、保存しようとしている `active_models` 上で false（無効化されている）場合、バックエンドはリクエストを拒否せず、「有効モデルのうち定義順で先頭のもの」に自動的に読み替えて保存する。
- これはフロントエンドが常に整合した組み合わせを送る前提のもとでの防御的フォールバックであり、通常の操作フローでは発火しない。

### API スキーマ

- `backend/app/routes/schemas/bot.py` の `BotInput` / `BotModifyInput` / `BotOutput` / `BotModifyOutput` / `BotSummaryOutput` に `default_model: type_model_name` を追加する。

## フロントエンド変更

### 型定義

- `frontend/src/@types/bot.d.ts` の `BotDetails` に `defaultModel: Model` を追加する（camelCase）。

### ボット設定画面（`BotKbEditPage.tsx`）

- `defaultModel` の state を追加する。
- モデル一覧セクション（`ExpandableDrawerGroup`、`BotKbEditPage.tsx:2694-2719` 付近）の直前に、「デフォルトモデル」ドロップダウンを新設する。選択肢は現在有効化されているモデル（`activeModelsOptions`）と連動する。
- 現在デフォルトに指定されているモデルを Toggle で無効化しようとした場合、保存をブロックするのではなく、その場で自動的に「他の有効モデルのうち定義順で先頭のもの」を新しいデフォルトに付け替える。
- 新規ボット作成時の `defaultModel` 初期値は、`activeModelsOptions` の先頭モデルとする。
- 保存時（作成・更新とも）に `defaultModel` をリクエストボディへ追加する（既存の送信箇所: `BotKbEditPage.tsx:1399, 1448, 1525, 1581` 付近）。

### チャット画面の初期選択ロジック（`useModel.ts`）

- `getDefaultModel()`（`useModel.ts:355-372`）の優先順位を以下のように変更する。
  - 変更前: グローバル `defaultModel` → 有効モデル一覧の先頭
  - 変更後: ボットの `default_model`（`active_models` 上で有効な場合）→ グローバル `defaultModel` → 有効モデル一覧の先頭
- ボットに紐づかない直接チャット（botId 無し）の場合は、これまで通り「グローバル `defaultModel` → 先頭」のロジックのみを使う（変更なし）。
- `localStorage` の `bot_model_{botId}`（ボットごとに前回使用したモデルを記憶する既存の仕組み、`useModel.ts:385-436`）は現状維持とする。最終的な優先順位は次の通り：
  1. `localStorage` に記憶された前回使用モデル（`activeModels` 上で有効な場合）
  2. ボットの `default_model`
  3. グローバル `defaultModel`
  4. 有効モデル一覧の先頭

## スコープ外

- 事前の一括データマイグレーション（読み込み時の自動補完で対応するため不要）
- グローバルデフォルトモデル（`DEFAULT_MODEL` 環境変数）の仕組み自体の変更
- ボットに紐づかない直接チャットのデフォルトモデル指定機能

## テスト方針

- バックエンド: `BotModel` の `default_model` 読み込み時の後方互換フォールバック（属性欠如のケース）、保存時の自動整合ロジック（`active_models` 上で無効なモデルが指定された場合に安全側へ読み替えられること）を検証する。
- フロントエンド: `useModel.ts` の `getDefaultModel()` の優先順位（localStorage → ボット `default_model` → グローバル `defaultModel` → 先頭）、`BotKbEditPage.tsx` でデフォルトモデルを Toggle OFF した際に別モデルへ自動的に付け替わる挙動をそれぞれテストする。
