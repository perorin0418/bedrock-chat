import os
import sys
import unittest
from unittest.mock import patch

os.environ["BEDROCK_REGION"] = "us-east-1"
os.environ["REGION"] = "us-east-1"

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
