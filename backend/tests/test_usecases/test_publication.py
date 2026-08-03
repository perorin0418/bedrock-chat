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
    @patch("app.usecases.publication.bind_api_key_owner", side_effect=Exception("boom"))
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
    @patch(
        "app.usecases.publication.find_build_status_by_build_id",
        return_value="SUCCEEDED",
    )
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
