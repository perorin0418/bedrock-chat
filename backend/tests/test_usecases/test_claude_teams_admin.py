import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
from app.usecases.claude_teams_admin import (
    AgentReleaseUnavailableError,
    InvalidIngestSecretError,
    InvalidRegistrationSecretError,
    build_claude_teams_usage_history_csv,
    create_claude_teams_token,
    delete_claude_teams_token_usecase,
    get_agent_release,
    get_claude_teams_registration_secret,
    get_claude_teams_token_latest_usage,
    get_claude_teams_token_status,
    ingest_claude_teams_usage_snapshot,
    list_claude_teams_tokens,
    regenerate_claude_teams_registration_secret,
    self_register_claude_teams_token,
    update_claude_teams_token,
)
from app.claude_teams.token_repository import ClaudeTeamsTokenItem
from app.claude_teams.usage_history_repository import ClaudeTeamsUsageHistoryItem


class TestClaudeTeamsAdminUsecase(unittest.TestCase):
    @patch("app.usecases.claude_teams_admin.store_claude_teams_token")
    @patch("app.usecases.claude_teams_admin.create_token")
    def test_create_claude_teams_token_stores_secret_then_metadata(
        self, mock_create_token, mock_store_secret
    ):
        mock_create_token.return_value = ClaudeTeamsTokenItem(
            token_id="tok-1", display_name="team-a", enabled=True, created_at=1
        )

        result = create_claude_teams_token(display_name="team-a", token_value="sk-oauth-abc")

        mock_create_token.assert_called_once_with(display_name="team-a")
        mock_store_secret.assert_called_once_with("tok-1", "sk-oauth-abc")
        self.assertEqual(result.token_id, "tok-1")

    @patch("app.usecases.claude_teams_admin.list_tokens")
    def test_list_claude_teams_tokens_returns_repository_result(self, mock_list):
        mock_list.return_value = [
            ClaudeTeamsTokenItem(
                token_id="tok-1", display_name="a", enabled=True, created_at=1
            )
        ]

        result = list_claude_teams_tokens()

        self.assertEqual(result, mock_list.return_value)

    @patch("app.usecases.claude_teams_admin.set_enabled")
    def test_update_claude_teams_token_sets_enabled(self, mock_set_enabled):
        update_claude_teams_token(token_id="tok-1", enabled=False, display_name=None)

        mock_set_enabled.assert_called_once_with("tok-1", False)

    @patch("app.usecases.claude_teams_admin.delete_claude_teams_token")
    @patch("app.usecases.claude_teams_admin.delete_token")
    def test_delete_claude_teams_token_usecase_deletes_both(
        self, mock_delete_token, mock_delete_secret
    ):
        delete_claude_teams_token_usecase("tok-1")

        mock_delete_token.assert_called_once_with("tok-1")
        mock_delete_secret.assert_called_once_with("tok-1")

    @patch("app.usecases.claude_teams_admin.get_latest_usage_snapshot")
    def test_get_claude_teams_token_latest_usage_delegates_to_repository(
        self, mock_get_latest
    ):
        mock_get_latest.return_value = ClaudeTeamsUsageHistoryItem(
            token_id="tok-1", sampled_at_ms=1, fetch_status="ok"
        )

        result = get_claude_teams_token_latest_usage("tok-1")

        mock_get_latest.assert_called_once_with("tok-1")
        self.assertEqual(result, mock_get_latest.return_value)

    @patch("app.usecases.claude_teams_admin.list_usage_history")
    @patch("app.usecases.claude_teams_admin.list_tokens")
    def test_build_csv_for_single_token_includes_header_and_rows(
        self, mock_list_tokens, mock_list_history
    ):
        mock_list_tokens.return_value = [
            ClaudeTeamsTokenItem(
                token_id="tok-1", display_name="team-a", enabled=True, created_at=1
            )
        ]
        mock_list_history.return_value = [
            ClaudeTeamsUsageHistoryItem(
                token_id="tok-1",
                sampled_at_ms=1_700_000_000_000,
                fetch_status="ok",
                five_hour_utilization=12.5,
                five_hour_resets_at="2026-01-01T00:00:00Z",
                seven_day_utilization=3.0,
                seven_day_resets_at="2026-01-07T00:00:00Z",
            )
        ]

        csv_text = build_claude_teams_usage_history_csv(
            since_ms=0, until_ms=2_000_000_000_000, token_id="tok-1"
        )

        lines = csv_text.strip().splitlines()
        self.assertEqual(
            lines[0],
            "token_id,display_name,sampled_at_ms,fetch_status,fetch_error_message,"
            "five_hour_utilization,five_hour_resets_at,seven_day_utilization,"
            "seven_day_resets_at",
        )
        self.assertIn("tok-1", lines[1])
        self.assertIn("ok", lines[1])
        self.assertIn("12.5", lines[1])
        mock_list_history.assert_called_once_with("tok-1", 0, 2_000_000_000_000)

    @patch("app.usecases.claude_teams_admin.list_usage_history")
    @patch("app.usecases.claude_teams_admin.list_tokens")
    def test_build_csv_without_token_id_covers_every_registered_token(
        self, mock_list_tokens, mock_list_history
    ):
        mock_list_tokens.return_value = [
            ClaudeTeamsTokenItem(
                token_id="tok-1", display_name="team-a", enabled=True, created_at=1
            ),
            ClaudeTeamsTokenItem(
                token_id="tok-2", display_name="team-b", enabled=True, created_at=1
            ),
        ]
        mock_list_history.side_effect = [
            [
                ClaudeTeamsUsageHistoryItem(
                    token_id="tok-1", sampled_at_ms=1, fetch_status="ok"
                )
            ],
            [
                ClaudeTeamsUsageHistoryItem(
                    token_id="tok-2",
                    sampled_at_ms=2,
                    fetch_status="auth_error",
                    fetch_error_message="401 Unauthorized (token expired or revoked)",
                )
            ],
        ]

        csv_text = build_claude_teams_usage_history_csv(since_ms=0, until_ms=10)

        lines = csv_text.strip().splitlines()
        self.assertEqual(len(lines), 3)  # header + 2 rows
        self.assertIn("team-a", lines[1])
        self.assertIn("team-b", lines[2])
        # Token-validity failures must show up verbatim in the export,
        # distinct from any usage-limit column.
        self.assertIn("auth_error", lines[2])
        self.assertIn(
            "401 Unauthorized (token expired or revoked)", lines[2]
        )

    @patch("app.usecases.claude_teams_admin.write_usage_snapshot")
    @patch("app.usecases.claude_teams_admin.get_token")
    def test_ingest_usage_snapshot_writes_when_secret_matches(
        self, mock_get_token, mock_write
    ):
        mock_get_token.return_value = ClaudeTeamsTokenItem(
            token_id="tok-1",
            display_name="team-a",
            enabled=True,
            created_at=1,
            ingest_secret="correct-secret",
        )

        ingest_claude_teams_usage_snapshot(
            token_id="tok-1",
            ingest_secret="correct-secret",
            fetch_status="ok",
            fetch_error_message=None,
            five_hour_utilization=42.0,
            five_hour_resets_at="2026-05-14T19:40:00Z",
            seven_day_utilization=10.0,
            seven_day_resets_at="2026-05-21T16:00:01Z",
            sampled_at_ms=1_700_000_000_000,
        )

        mock_write.assert_called_once_with(
            token_id="tok-1",
            fetch_status="ok",
            fetch_error_message=None,
            five_hour_utilization=42.0,
            five_hour_resets_at="2026-05-14T19:40:00Z",
            seven_day_utilization=10.0,
            seven_day_resets_at="2026-05-21T16:00:01Z",
            sampled_at_ms=1_700_000_000_000,
        )

    @patch("app.usecases.claude_teams_admin.write_usage_snapshot")
    @patch("app.usecases.claude_teams_admin.get_token")
    def test_ingest_usage_snapshot_rejects_wrong_secret(
        self, mock_get_token, mock_write
    ):
        mock_get_token.return_value = ClaudeTeamsTokenItem(
            token_id="tok-1",
            display_name="team-a",
            enabled=True,
            created_at=1,
            ingest_secret="correct-secret",
        )

        with self.assertRaises(InvalidIngestSecretError):
            ingest_claude_teams_usage_snapshot(
                token_id="tok-1",
                ingest_secret="wrong-secret",
                fetch_status="ok",
                fetch_error_message=None,
                five_hour_utilization=None,
                five_hour_resets_at=None,
                seven_day_utilization=None,
                seven_day_resets_at=None,
                sampled_at_ms=None,
            )
        mock_write.assert_not_called()

    @patch("app.usecases.claude_teams_admin.write_usage_snapshot")
    @patch("app.usecases.claude_teams_admin.get_token")
    def test_ingest_usage_snapshot_rejects_unknown_token_id(
        self, mock_get_token, mock_write
    ):
        mock_get_token.return_value = None

        with self.assertRaises(InvalidIngestSecretError):
            ingest_claude_teams_usage_snapshot(
                token_id="tok-missing",
                ingest_secret="anything",
                fetch_status="ok",
                fetch_error_message=None,
                five_hour_utilization=None,
                five_hour_resets_at=None,
                seven_day_utilization=None,
                seven_day_resets_at=None,
                sampled_at_ms=None,
            )
        mock_write.assert_not_called()

    @patch("app.usecases.claude_teams_admin.get_token")
    def test_get_token_status_returns_enabled_when_secret_matches(
        self, mock_get_token
    ):
        mock_get_token.return_value = ClaudeTeamsTokenItem(
            token_id="tok-1",
            display_name="team-a",
            enabled=True,
            created_at=1,
            ingest_secret="correct-secret",
        )

        result = get_claude_teams_token_status(
            token_id="tok-1", ingest_secret="correct-secret"
        )

        self.assertTrue(result)

    @patch("app.usecases.claude_teams_admin.get_token")
    def test_get_token_status_returns_false_for_disabled_token(
        self, mock_get_token
    ):
        mock_get_token.return_value = ClaudeTeamsTokenItem(
            token_id="tok-1",
            display_name="team-a",
            enabled=False,
            created_at=1,
            ingest_secret="correct-secret",
        )

        result = get_claude_teams_token_status(
            token_id="tok-1", ingest_secret="correct-secret"
        )

        self.assertFalse(result)

    @patch("app.usecases.claude_teams_admin.get_token")
    def test_get_token_status_rejects_wrong_secret(self, mock_get_token):
        mock_get_token.return_value = ClaudeTeamsTokenItem(
            token_id="tok-1",
            display_name="team-a",
            enabled=True,
            created_at=1,
            ingest_secret="correct-secret",
        )

        with self.assertRaises(InvalidIngestSecretError):
            get_claude_teams_token_status(
                token_id="tok-1", ingest_secret="wrong-secret"
            )

    @patch("app.usecases.claude_teams_admin.get_token")
    def test_get_token_status_rejects_unknown_token_id(self, mock_get_token):
        mock_get_token.return_value = None

        with self.assertRaises(InvalidIngestSecretError):
            get_claude_teams_token_status(
                token_id="tok-missing", ingest_secret="anything"
            )

    @patch("app.usecases.claude_teams_admin.get_or_create_registration_secret")
    def test_get_claude_teams_registration_secret_delegates_to_repository(
        self, mock_get
    ):
        mock_get.return_value = "reg-secret"

        result = get_claude_teams_registration_secret()

        mock_get.assert_called_once_with()
        self.assertEqual(result, "reg-secret")

    @patch("app.usecases.claude_teams_admin.regenerate_registration_secret")
    def test_regenerate_claude_teams_registration_secret_delegates_to_repository(
        self, mock_regenerate
    ):
        mock_regenerate.return_value = "new-reg-secret"

        result = regenerate_claude_teams_registration_secret()

        mock_regenerate.assert_called_once_with()
        self.assertEqual(result, "new-reg-secret")

    @patch("app.usecases.claude_teams_admin.store_claude_teams_token")
    @patch("app.usecases.claude_teams_admin.create_token")
    @patch("app.usecases.claude_teams_admin.get_or_create_registration_secret")
    def test_self_register_creates_token_when_secret_matches(
        self, mock_get_reg_secret, mock_create_token, mock_store_secret
    ):
        mock_get_reg_secret.return_value = "correct-reg-secret"
        mock_create_token.return_value = ClaudeTeamsTokenItem(
            token_id="tok-1",
            display_name="member-pc",
            enabled=True,
            created_at=1,
            ingest_secret="fresh-ingest-secret",
        )

        result = self_register_claude_teams_token(
            registration_secret="correct-reg-secret",
            display_name="member-pc",
            token_value="sk-oauth-abc",
        )

        mock_create_token.assert_called_once_with(
            display_name="member-pc"
        )
        mock_store_secret.assert_called_once_with("tok-1", "sk-oauth-abc")
        self.assertEqual(result.token_id, "tok-1")
        self.assertEqual(result.ingest_secret, "fresh-ingest-secret")

    @patch("app.usecases.claude_teams_admin.store_claude_teams_token")
    @patch("app.usecases.claude_teams_admin.create_token")
    @patch("app.usecases.claude_teams_admin.get_or_create_registration_secret")
    def test_self_register_rejects_wrong_secret(
        self, mock_get_reg_secret, mock_create_token, mock_store_secret
    ):
        mock_get_reg_secret.return_value = "correct-reg-secret"

        with self.assertRaises(InvalidRegistrationSecretError):
            self_register_claude_teams_token(
                registration_secret="wrong-secret",
                display_name="member-pc",
                token_value="sk-oauth-abc",
            )
        mock_create_token.assert_not_called()
        mock_store_secret.assert_not_called()


class TestGetAgentRelease(unittest.TestCase):
    """The agent auto-update lookup. Authenticated with the same
    per-token ingest_secret as the usage-snapshot push, which is what
    keeps the release binary (and therefore the org-wide Registration
    Secret baked into it) from being downloadable by anyone who merely
    learns a URL."""

    def _enabled_token(self, ingest_secret: str = "ingest-secret-1"):
        return ClaudeTeamsTokenItem(
            token_id="tok-1",
            display_name="member-pc",
            enabled=True,
            created_at=1,
            ingest_secret=ingest_secret,
        )

    @patch("app.usecases.claude_teams_admin.generate_agent_download_url")
    @patch("app.usecases.claude_teams_admin.get_agent_release_manifest")
    @patch("app.usecases.claude_teams_admin.get_token")
    def test_returns_version_digest_and_signed_url(
        self, mock_get_token, mock_manifest, mock_presign
    ):
        mock_get_token.return_value = self._enabled_token()
        mock_manifest.return_value = {
            "version": "2026.09.10-abc1234",
            "sha256": "b" * 64,
            "key": "releases/2026.09.10-abc1234/claude_teams_member_agent.exe",
        }
        mock_presign.return_value = "https://signed.example/agent.exe"

        result = get_agent_release(token_id="tok-1", ingest_secret="ingest-secret-1")

        mock_presign.assert_called_once_with(
            "releases/2026.09.10-abc1234/claude_teams_member_agent.exe"
        )
        self.assertEqual(
            result,
            {
                "version": "2026.09.10-abc1234",
                "sha256": "b" * 64,
                "download_url": "https://signed.example/agent.exe",
            },
        )

    @patch("app.usecases.claude_teams_admin.generate_agent_download_url")
    @patch("app.usecases.claude_teams_admin.get_agent_release_manifest")
    @patch("app.usecases.claude_teams_admin.get_token")
    def test_rejects_wrong_ingest_secret_without_touching_the_bucket(
        self, mock_get_token, mock_manifest, mock_presign
    ):
        mock_get_token.return_value = self._enabled_token()

        with self.assertRaises(InvalidIngestSecretError):
            get_agent_release(token_id="tok-1", ingest_secret="wrong-secret")
        # No manifest read and, crucially, no presigned URL minted: an
        # unauthenticated caller must never obtain a download link.
        mock_manifest.assert_not_called()
        mock_presign.assert_not_called()

    @patch("app.usecases.claude_teams_admin.generate_agent_download_url")
    @patch("app.usecases.claude_teams_admin.get_agent_release_manifest")
    @patch("app.usecases.claude_teams_admin.get_token")
    def test_rejects_unknown_token_id(self, mock_get_token, mock_manifest, mock_presign):
        mock_get_token.return_value = None

        with self.assertRaises(InvalidIngestSecretError):
            get_agent_release(token_id="tok-missing", ingest_secret="anything")
        mock_presign.assert_not_called()

    @patch("app.usecases.claude_teams_admin.generate_agent_download_url")
    @patch("app.usecases.claude_teams_admin.get_agent_release_manifest")
    @patch("app.usecases.claude_teams_admin.get_token")
    def test_serves_a_disabled_token_too(
        self, mock_get_token, mock_manifest, mock_presign
    ):
        # A member whose chat-side token was disabled still runs the
        # agent (usage reporting keeps working, and they need the fix in
        # a newer build most of all), so `enabled` deliberately does not
        # gate updates -- unlike the chat-token status route, whose whole
        # purpose is reporting that flag.
        token = self._enabled_token()
        token.enabled = False
        mock_get_token.return_value = token
        mock_manifest.return_value = {"version": "v2", "sha256": "c" * 64, "key": "k"}
        mock_presign.return_value = "https://signed.example/agent.exe"

        result = get_agent_release(token_id="tok-1", ingest_secret="ingest-secret-1")

        self.assertEqual(result["version"], "v2")

    @patch("app.usecases.claude_teams_admin.generate_agent_download_url")
    @patch("app.usecases.claude_teams_admin.get_agent_release_manifest")
    @patch("app.usecases.claude_teams_admin.get_token")
    def test_no_release_configured_raises_unavailable(
        self, mock_get_token, mock_manifest, mock_presign
    ):
        from app.claude_teams.agent_release_repository import (
            AgentReleaseNotConfiguredError,
        )

        mock_get_token.return_value = self._enabled_token()
        mock_manifest.side_effect = AgentReleaseNotConfiguredError("no bucket")

        with self.assertRaises(AgentReleaseUnavailableError):
            get_agent_release(token_id="tok-1", ingest_secret="ingest-secret-1")
        mock_presign.assert_not_called()

    @patch("app.usecases.claude_teams_admin.generate_agent_download_url")
    @patch("app.usecases.claude_teams_admin.get_agent_release_manifest")
    @patch("app.usecases.claude_teams_admin.get_token")
    def test_manifest_without_sha256_is_refused_not_served_unverified(
        self, mock_get_token, mock_manifest, mock_presign
    ):
        # Failing closed keeps the "install unverified bytes vs. get
        # silently stuck" decision out of the agent entirely.
        mock_get_token.return_value = self._enabled_token()
        mock_manifest.return_value = {"version": "v2", "key": "releases/v2/agent.exe"}

        with self.assertRaises(AgentReleaseUnavailableError):
            get_agent_release(token_id="tok-1", ingest_secret="ingest-secret-1")
        mock_presign.assert_not_called()

    @patch("app.usecases.claude_teams_admin.generate_agent_download_url")
    @patch("app.usecases.claude_teams_admin.get_agent_release_manifest")
    @patch("app.usecases.claude_teams_admin.get_token")
    def test_manifest_without_version_or_key_is_refused(
        self, mock_get_token, mock_manifest, mock_presign
    ):
        mock_get_token.return_value = self._enabled_token()

        for incomplete in (
            {"sha256": "d" * 64, "key": "releases/v2/agent.exe"},
            {"version": "v2", "sha256": "d" * 64},
            {"version": "   ", "sha256": "d" * 64, "key": "k"},
        ):
            mock_manifest.return_value = incomplete
            with self.assertRaises(AgentReleaseUnavailableError):
                get_agent_release(token_id="tok-1", ingest_secret="ingest-secret-1")
        mock_presign.assert_not_called()


if __name__ == "__main__":
    unittest.main()
