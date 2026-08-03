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
