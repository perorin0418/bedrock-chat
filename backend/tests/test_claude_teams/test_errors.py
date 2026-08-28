import sys
import unittest

sys.path.insert(0, ".")
from app.claude_teams.errors import classify_api_retry_error


class TestClassifyApiRetryError(unittest.TestCase):
    def test_rate_limit_and_billing_error_classify_as_cooldown(self):
        self.assertEqual(classify_api_retry_error("rate_limit"), "cooldown")
        self.assertEqual(classify_api_retry_error("billing_error"), "cooldown")

    def test_auth_errors_classify_as_disable(self):
        self.assertEqual(classify_api_retry_error("authentication_failed"), "disable")
        self.assertEqual(classify_api_retry_error("oauth_org_not_allowed"), "disable")

    def test_other_errors_classify_as_passthrough(self):
        for kind in [
            "overloaded",
            "invalid_request",
            "model_not_found",
            "server_error",
            "max_output_tokens",
            "unknown",
            "some_future_error_type",
        ]:
            self.assertEqual(classify_api_retry_error(kind), "passthrough")


if __name__ == "__main__":
    unittest.main()
