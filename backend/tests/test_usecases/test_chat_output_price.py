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
