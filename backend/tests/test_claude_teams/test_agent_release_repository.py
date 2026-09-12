import json
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.claude_teams.agent_release_repository import (
    DOWNLOAD_URL_EXPIRATION_SECONDS,
    MANIFEST_KEY,
    AgentReleaseNotConfiguredError,
    generate_agent_download_url,
    get_agent_release_manifest,
)
from botocore.exceptions import ClientError


def _body(payload: bytes) -> MagicMock:
    stream = MagicMock()
    stream.read.return_value = payload
    return stream


class TestGetAgentReleaseManifest(unittest.TestCase):
    @patch.dict("os.environ", {"CLAUDE_TEAMS_AGENT_RELEASE_BUCKET": "release-bucket"})
    @patch("boto3.client")
    def test_reads_and_parses_manifest_from_fixed_key(self, mock_boto3_client):
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        manifest = {
            "version": "2026.09.10-abc1234",
            "sha256": "a" * 64,
            "key": "releases/2026.09.10-abc1234/claude_teams_member_agent.exe",
        }
        mock_client.get_object.return_value = {
            "Body": _body(json.dumps(manifest).encode())
        }

        result = get_agent_release_manifest()

        mock_client.get_object.assert_called_once_with(
            Bucket="release-bucket", Key=MANIFEST_KEY
        )
        self.assertEqual(result, manifest)

    @patch.dict("os.environ", {}, clear=True)
    def test_no_bucket_configured_is_not_configured_error(self):
        # A deployment that never provisioned/wired a release bucket must
        # degrade to "no updates published", never to a 500.
        with self.assertRaises(AgentReleaseNotConfiguredError):
            get_agent_release_manifest()

    @patch.dict("os.environ", {"CLAUDE_TEAMS_AGENT_RELEASE_BUCKET": "release-bucket"})
    @patch("boto3.client")
    def test_missing_manifest_is_not_configured_error(self, mock_boto3_client):
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "nope"}}, "GetObject"
        )

        with self.assertRaises(AgentReleaseNotConfiguredError):
            get_agent_release_manifest()

    @patch.dict("os.environ", {"CLAUDE_TEAMS_AGENT_RELEASE_BUCKET": "release-bucket"})
    @patch("boto3.client")
    def test_access_denied_is_treated_as_nothing_published(self, mock_boto3_client):
        # Without s3:ListBucket, a missing object is indistinguishable
        # from a denied one; both mean "no release", never a malformed
        # request.
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject"
        )

        with self.assertRaises(AgentReleaseNotConfiguredError):
            get_agent_release_manifest()

    @patch.dict("os.environ", {"CLAUDE_TEAMS_AGENT_RELEASE_BUCKET": "release-bucket"})
    @patch("boto3.client")
    def test_unexpected_client_error_propagates(self, mock_boto3_client):
        # A genuine infrastructure fault must not be silently reported to
        # members as "nothing published", which would mask a broken
        # rollout indefinitely.
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "boom"}}, "GetObject"
        )

        with self.assertRaises(ClientError):
            get_agent_release_manifest()

    @patch.dict("os.environ", {"CLAUDE_TEAMS_AGENT_RELEASE_BUCKET": "release-bucket"})
    @patch("boto3.client")
    def test_malformed_manifest_is_not_configured_error(self, mock_boto3_client):
        # An admin's JSON typo must not wedge every member's scheduled
        # run into an error state.
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.get_object.return_value = {"Body": _body(b"{not json")}

        with self.assertRaises(AgentReleaseNotConfiguredError):
            get_agent_release_manifest()

    @patch.dict("os.environ", {"CLAUDE_TEAMS_AGENT_RELEASE_BUCKET": "release-bucket"})
    @patch("boto3.client")
    def test_non_object_manifest_is_not_configured_error(self, mock_boto3_client):
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.get_object.return_value = {"Body": _body(b'["not", "an object"]')}

        with self.assertRaises(AgentReleaseNotConfiguredError):
            get_agent_release_manifest()


class TestGenerateAgentDownloadURL(unittest.TestCase):
    @patch.dict("os.environ", {"CLAUDE_TEAMS_AGENT_RELEASE_BUCKET": "release-bucket"})
    @patch("boto3.client")
    def test_signs_short_lived_get_for_the_named_key(self, mock_boto3_client):
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client
        mock_client.generate_presigned_url.return_value = "https://signed.example/x.exe"

        url = generate_agent_download_url("releases/v1/claude_teams_member_agent.exe")

        self.assertEqual(url, "https://signed.example/x.exe")
        mock_client.generate_presigned_url.assert_called_once_with(
            ClientMethod="get_object",
            Params={
                "Bucket": "release-bucket",
                "Key": "releases/v1/claude_teams_member_agent.exe",
            },
            ExpiresIn=DOWNLOAD_URL_EXPIRATION_SECONDS,
            HttpMethod="GET",
        )

    @patch.dict("os.environ", {"CLAUDE_TEAMS_AGENT_RELEASE_BUCKET": "release-bucket"})
    @patch("app.claude_teams.agent_release_repository.REGION", "ap-northeast-1")
    @patch("app.utils.BEDROCK_REGION", "us-east-1")
    @patch("boto3.client")
    def test_signs_in_the_stack_region_not_the_bedrock_region(self, mock_boto3_client):
        # The release bucket is created by the main stack in REGION,
        # while the house presigning helper signs with BEDROCK_REGION.
        # Signing in the wrong region yields SignatureDoesNotMatch at
        # download time -- and only on the cross-region deployments a
        # separate BEDROCK_REGION exists to support, so a same-region
        # test would never catch it.
        #
        # Patches the module-level name rather than reloading the module:
        # a reload would rebind AgentReleaseNotConfiguredError to a new
        # class object, so `except`/`assertRaises` in other tests that
        # imported the original would silently stop matching.
        mock_client = MagicMock()
        mock_boto3_client.return_value = mock_client

        generate_agent_download_url("releases/v1/agent.exe")

        self.assertEqual(
            mock_boto3_client.call_args.kwargs["region_name"], "ap-northeast-1"
        )

    def test_expiration_is_minutes_not_hours(self):
        # The URL is consumed seconds after being issued; a long-lived
        # one would only widen the window in which a leaked URL is
        # replayable by someone who never held an ingest_secret.
        self.assertLessEqual(DOWNLOAD_URL_EXPIRATION_SECONDS, 900)

    @patch.dict("os.environ", {}, clear=True)
    def test_no_bucket_configured_raises(self):
        with self.assertRaises(AgentReleaseNotConfiguredError):
            generate_agent_download_url("releases/v1/x.exe")


if __name__ == "__main__":
    unittest.main()
