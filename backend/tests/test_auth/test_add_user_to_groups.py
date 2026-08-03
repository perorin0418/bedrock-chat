import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["USER_POOL_ID"] = "test-pool-id"
os.environ["CLAUDE_CODE_IAM_USER_TABLE_NAME"] = "test-claude-code-iam-user-table"
os.environ["CLAUDE_CODE_BEDROCK_POLICY_ARN"] = (
    "arn:aws:iam::123456789012:policy/ClaudeCodeBedrockAccessPolicy"
)

# add_user_to_groups.py is a standalone Lambda module (not part of the
# `app` package), so it needs its own sys.path entry to be importable here.
sys.path.insert(0, "auth/add_user_to_groups")
import add_user_to_groups  # noqa: E402


class TestIamUserName(unittest.TestCase):
    def test_iam_user_name(self):
        self.assertEqual(
            add_user_to_groups._iam_user_name("abc-123"), "claude-code-abc-123"
        )


class TestSecretName(unittest.TestCase):
    def test_secret_name(self):
        self.assertEqual(
            add_user_to_groups._secret_name("abc-123"), "claude-code/abc-123"
        )


class TestEnsureIamUser(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("add_user_to_groups.iam")
        self.mock_iam = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_creates_new_user_and_attaches_policy(self):
        self.mock_iam.create_user.return_value = {
            "User": {"Arn": "arn:aws:iam::123456789012:user/claude-code-user-1"}
        }

        arn = add_user_to_groups._ensure_iam_user("user-1")

        self.assertEqual(arn, "arn:aws:iam::123456789012:user/claude-code-user-1")
        self.mock_iam.create_user.assert_called_once_with(UserName="claude-code-user-1")
        self.mock_iam.attach_user_policy.assert_called_once_with(
            UserName="claude-code-user-1",
            PolicyArn=add_user_to_groups.CLAUDE_CODE_BEDROCK_POLICY_ARN,
        )

    def test_falls_back_to_get_user_when_already_exists(self):
        class EntityAlreadyExistsException(Exception):
            pass

        self.mock_iam.exceptions.EntityAlreadyExistsException = (
            EntityAlreadyExistsException
        )
        self.mock_iam.create_user.side_effect = EntityAlreadyExistsException()
        self.mock_iam.get_user.return_value = {
            "User": {"Arn": "arn:aws:iam::123456789012:user/claude-code-user-1"}
        }

        arn = add_user_to_groups._ensure_iam_user("user-1")

        self.assertEqual(arn, "arn:aws:iam::123456789012:user/claude-code-user-1")
        self.mock_iam.get_user.assert_called_once_with(UserName="claude-code-user-1")
        # Policy is still (re-)attached even on the idempotent path.
        self.mock_iam.attach_user_policy.assert_called_once()


class TestEnsureAccessKey(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("add_user_to_groups.iam")
        self.mock_iam = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_creates_key_when_none_exist(self):
        self.mock_iam.list_access_keys.return_value = {"AccessKeyMetadata": []}
        self.mock_iam.create_access_key.return_value = {
            "AccessKey": {
                "AccessKeyId": "AKIAEXAMPLE",
                "SecretAccessKey": "secret-value",
            }
        }

        key = add_user_to_groups._ensure_access_key("user-1")

        self.assertEqual(
            key, {"AccessKeyId": "AKIAEXAMPLE", "SecretAccessKey": "secret-value"}
        )
        self.mock_iam.create_access_key.assert_called_once_with(
            UserName="claude-code-user-1"
        )

    def test_skips_when_active_key_already_exists(self):
        self.mock_iam.list_access_keys.return_value = {
            "AccessKeyMetadata": [{"AccessKeyId": "AKIAOLD", "Status": "Active"}]
        }

        key = add_user_to_groups._ensure_access_key("user-1")

        self.assertIsNone(key)
        self.mock_iam.create_access_key.assert_not_called()

    def test_creates_key_when_existing_key_is_inactive(self):
        self.mock_iam.list_access_keys.return_value = {
            "AccessKeyMetadata": [{"AccessKeyId": "AKIAOLD", "Status": "Inactive"}]
        }
        self.mock_iam.create_access_key.return_value = {
            "AccessKey": {"AccessKeyId": "AKIANEW", "SecretAccessKey": "secret"}
        }

        key = add_user_to_groups._ensure_access_key("user-1")

        self.assertIsNotNone(key)
        self.mock_iam.create_access_key.assert_called_once()


class TestStoreAccessKeySecret(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("add_user_to_groups.secretsmanager")
        self.mock_secretsmanager = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_creates_new_secret(self):
        self.mock_secretsmanager.create_secret.return_value = {
            "ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:claude-code/user-1"
        }

        arn = add_user_to_groups._store_access_key_secret("user-1", "AKIA", "secret")

        self.assertEqual(
            arn,
            "arn:aws:secretsmanager:us-east-1:123456789012:secret:claude-code/user-1",
        )
        self.mock_secretsmanager.create_secret.assert_called_once()
        self.assertEqual(
            self.mock_secretsmanager.create_secret.call_args.kwargs["Name"],
            "claude-code/user-1",
        )

    def test_updates_existing_secret(self):
        class ResourceExistsException(Exception):
            pass

        self.mock_secretsmanager.exceptions.ResourceExistsException = (
            ResourceExistsException
        )
        self.mock_secretsmanager.create_secret.side_effect = ResourceExistsException()
        self.mock_secretsmanager.update_secret.return_value = {
            "ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:claude-code/user-1"
        }

        arn = add_user_to_groups._store_access_key_secret("user-1", "AKIA", "secret")

        self.assertEqual(
            arn,
            "arn:aws:secretsmanager:us-east-1:123456789012:secret:claude-code/user-1",
        )
        self.mock_secretsmanager.update_secret.assert_called_once()


class TestProvisionClaudeCodeIamUser(unittest.TestCase):
    def setUp(self):
        self.patcher_iam = patch("add_user_to_groups.iam")
        self.mock_iam = self.patcher_iam.start()

    def tearDown(self):
        self.patcher_iam.stop()

    @patch("add_user_to_groups._record_provisioning")
    @patch("add_user_to_groups._store_access_key_secret")
    @patch("add_user_to_groups._ensure_access_key")
    @patch("add_user_to_groups._ensure_iam_user")
    def test_happy_path_records_active_status(
        self, mock_ensure_user, mock_ensure_key, mock_store_secret, mock_record
    ):
        mock_ensure_user.return_value = (
            "arn:aws:iam::123456789012:user/claude-code-user-1"
        )
        mock_ensure_key.return_value = {
            "AccessKeyId": "AKIANEW",
            "SecretAccessKey": "secret",
        }
        mock_store_secret.return_value = (
            "arn:aws:secretsmanager:us-east-1:123456789012:secret:claude-code/user-1"
        )

        add_user_to_groups.provision_claude_code_iam_user("user-1", "user1@example.com")

        mock_record.assert_called_once_with(
            "user-1",
            "claude-code-user-1",
            "arn:aws:iam::123456789012:user/claude-code-user-1",
            "arn:aws:secretsmanager:us-east-1:123456789012:secret:claude-code/user-1",
            "ACTIVE",
        )
        self.mock_iam.delete_access_key.assert_not_called()

    @patch("add_user_to_groups._record_provisioning")
    @patch("add_user_to_groups._store_access_key_secret")
    @patch("add_user_to_groups._ensure_access_key")
    @patch("add_user_to_groups._ensure_iam_user")
    def test_skips_secret_store_and_records_empty_arn_when_key_already_exists(
        self, mock_ensure_user, mock_ensure_key, mock_store_secret, mock_record
    ):
        mock_ensure_user.return_value = (
            "arn:aws:iam::123456789012:user/claude-code-user-1"
        )
        mock_ensure_key.return_value = None

        add_user_to_groups.provision_claude_code_iam_user("user-1", "user1@example.com")

        mock_store_secret.assert_not_called()
        mock_record.assert_called_once_with(
            "user-1",
            "claude-code-user-1",
            "arn:aws:iam::123456789012:user/claude-code-user-1",
            "",
            "ACTIVE",
        )

    @patch("add_user_to_groups._record_provisioning")
    @patch("add_user_to_groups._store_access_key_secret")
    @patch("add_user_to_groups._ensure_access_key")
    @patch("add_user_to_groups._ensure_iam_user")
    def test_secret_write_failure_rolls_back_access_key_and_does_not_raise(
        self, mock_ensure_user, mock_ensure_key, mock_store_secret, mock_record
    ):
        mock_ensure_user.return_value = (
            "arn:aws:iam::123456789012:user/claude-code-user-1"
        )
        mock_ensure_key.return_value = {
            "AccessKeyId": "AKIANEW",
            "SecretAccessKey": "secret",
        }
        mock_store_secret.side_effect = Exception("secrets manager is down")

        # Must not raise: Cognito sign-up has already succeeded by this point.
        add_user_to_groups.provision_claude_code_iam_user("user-1", "user1@example.com")

        self.mock_iam.delete_access_key.assert_called_once_with(
            UserName="claude-code-user-1", AccessKeyId="AKIANEW"
        )
        mock_record.assert_not_called()

    @patch("add_user_to_groups._ensure_iam_user")
    def test_iam_user_creation_failure_does_not_raise(self, mock_ensure_user):
        mock_ensure_user.side_effect = Exception("iam is unavailable")

        # Must not raise.
        add_user_to_groups.provision_claude_code_iam_user("user-1", "user1@example.com")


class TestNotify(unittest.TestCase):
    def setUp(self):
        self.patcher = patch("add_user_to_groups.sns")
        self.mock_sns = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_noop_when_topic_arn_not_configured(self):
        with patch("add_user_to_groups.CLAUDE_CODE_NOTIFICATION_TOPIC_ARN", ""):
            add_user_to_groups._notify("subject", "message")
        self.mock_sns.publish.assert_not_called()

    def test_publishes_when_topic_arn_configured(self):
        with patch(
            "add_user_to_groups.CLAUDE_CODE_NOTIFICATION_TOPIC_ARN",
            "arn:aws:sns:us-east-1:123456789012:topic",
        ):
            add_user_to_groups._notify("subject", "message")
        self.mock_sns.publish.assert_called_once_with(
            TopicArn="arn:aws:sns:us-east-1:123456789012:topic",
            Subject="subject",
            Message="message",
        )

    def test_does_not_raise_when_publish_fails(self):
        self.mock_sns.publish.side_effect = Exception("sns unavailable")
        with patch(
            "add_user_to_groups.CLAUDE_CODE_NOTIFICATION_TOPIC_ARN",
            "arn:aws:sns:us-east-1:123456789012:topic",
        ):
            add_user_to_groups._notify("subject", "message")  # must not raise


class TestProvisionClaudeCodeIamUserNotifiesOnFailure(unittest.TestCase):
    def setUp(self):
        self.patcher_iam = patch("add_user_to_groups.iam")
        self.patcher_iam.start()
        self.patcher_notify = patch("add_user_to_groups._notify")
        self.mock_notify = self.patcher_notify.start()

    def tearDown(self):
        self.patcher_iam.stop()
        self.patcher_notify.stop()

    @patch("add_user_to_groups._ensure_iam_user")
    def test_notifies_on_provisioning_failure(self, mock_ensure_user):
        mock_ensure_user.side_effect = Exception("iam is unavailable")

        add_user_to_groups.provision_claude_code_iam_user("user-1", "user1@example.com")

        self.mock_notify.assert_called_once()
        self.assertIn("provisioning failed", self.mock_notify.call_args.args[0].lower())


class TestHandlerDispatchesClaudeCodeProvisioning(unittest.TestCase):
    def setUp(self):
        self.patcher_cognito = patch("add_user_to_groups.cognito")
        self.mock_cognito = self.patcher_cognito.start()
        self.patcher_provision = patch(
            "add_user_to_groups.provision_claude_code_iam_user"
        )
        self.mock_provision = self.patcher_provision.start()

    def tearDown(self):
        self.patcher_cognito.stop()
        self.patcher_provision.stop()

    def _confirm_sign_up_event(self):
        return {
            "userName": "user1@example.com",
            "triggerSource": "PostConfirmation_ConfirmSignUp",
            "request": {
                "userAttributes": {
                    "sub": "abc-123",
                    "cognito:user_status": "CONFIRMED",
                }
            },
        }

    def test_provisions_using_sub_when_enabled(self):
        with patch("add_user_to_groups.ENABLE_CLAUDE_CODE_PROVISIONING", True):
            add_user_to_groups.handler(self._confirm_sign_up_event(), MagicMock())

        self.mock_provision.assert_called_once_with("abc-123", "user1@example.com")

    def test_does_not_provision_when_disabled(self):
        with patch("add_user_to_groups.ENABLE_CLAUDE_CODE_PROVISIONING", False):
            add_user_to_groups.handler(self._confirm_sign_up_event(), MagicMock())

        self.mock_provision.assert_not_called()

    def test_does_not_provision_on_post_authentication(self):
        event = {
            "userName": "user1@example.com",
            "triggerSource": "PostAuthentication_Authentication",
            "request": {"userAttributes": {"cognito:user_status": "CONFIRMED"}},
        }
        with patch("add_user_to_groups.ENABLE_CLAUDE_CODE_PROVISIONING", True):
            add_user_to_groups.handler(event, MagicMock())

        self.mock_provision.assert_not_called()


if __name__ == "__main__":
    unittest.main()
