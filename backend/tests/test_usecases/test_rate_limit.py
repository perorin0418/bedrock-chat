import os
import sys
import unittest
from unittest.mock import patch

os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

sys.path.insert(0, ".")

from app.repositories.common import RateLimitExceededError
from app.usecases import rate_limit


class TestCheckRateLimit(unittest.TestCase):
    def setUp(self):
        rate_limit._limit_cache.clear()

    def test_allows_when_under_both_limits(self):
        with patch.object(
            rate_limit, "get_current_time", return_value=1_700_000_000_000
        ), patch.object(
            rate_limit, "_get_limit", side_effect=[10.0, 336.0]
        ), patch.object(
            rate_limit, "get_usage_since", side_effect=[5.0, 100.0]
        ):
            rate_limit.check_rate_limit("user-1")

    def test_raises_when_five_hour_sum_exceeds_limit(self):
        with patch.object(
            rate_limit, "get_current_time", return_value=1_700_000_000_000
        ), patch.object(
            rate_limit, "_get_limit", side_effect=[10.0, 336.0]
        ), patch.object(
            rate_limit, "get_usage_since", side_effect=[10.01, 100.0]
        ):
            with self.assertRaises(RateLimitExceededError):
                rate_limit.check_rate_limit("user-1")

    def test_does_not_raise_when_five_hour_sum_equals_limit(self):
        # "超過" is strictly `>`, not `>=`
        with patch.object(
            rate_limit, "get_current_time", return_value=1_700_000_000_000
        ), patch.object(
            rate_limit, "_get_limit", side_effect=[10.0, 336.0]
        ), patch.object(
            rate_limit, "get_usage_since", side_effect=[10.0, 100.0]
        ):
            rate_limit.check_rate_limit("user-1")

    def test_raises_when_seven_day_sum_exceeds_limit(self):
        with patch.object(
            rate_limit, "get_current_time", return_value=1_700_000_000_000
        ), patch.object(
            rate_limit, "_get_limit", side_effect=[10.0, 336.0]
        ), patch.object(
            rate_limit, "get_usage_since", side_effect=[5.0, 336.01]
        ):
            with self.assertRaises(RateLimitExceededError):
                rate_limit.check_rate_limit("user-1")

    def test_get_limit_caches_within_ttl(self):
        with patch.object(rate_limit, "ssm_client") as mock_ssm, patch(
            "time.time", side_effect=[100.0, 130.0]
        ):
            mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "10"}}

            first = rate_limit._get_limit("/test/param")
            second = rate_limit._get_limit("/test/param")

            self.assertEqual(first, 10.0)
            self.assertEqual(second, 10.0)
            mock_ssm.get_parameter.assert_called_once()

    def test_get_limit_refetches_after_ttl_expires(self):
        with patch.object(rate_limit, "ssm_client") as mock_ssm, patch(
            "time.time", side_effect=[100.0, 161.0]
        ):
            mock_ssm.get_parameter.side_effect = [
                {"Parameter": {"Value": "10"}},
                {"Parameter": {"Value": "20"}},
            ]

            first = rate_limit._get_limit("/test/param")
            second = rate_limit._get_limit("/test/param")

            self.assertEqual(first, 10.0)
            self.assertEqual(second, 20.0)
            self.assertEqual(mock_ssm.get_parameter.call_count, 2)

    def test_get_limit_falls_back_to_stale_cache_on_ssm_error(self):
        with patch.object(rate_limit, "ssm_client") as mock_ssm, patch(
            "time.time", side_effect=[100.0, 161.0]
        ):
            mock_ssm.get_parameter.side_effect = [
                {"Parameter": {"Value": "10"}},
                Exception("boom"),
            ]

            first = rate_limit._get_limit("/test/param")
            second = rate_limit._get_limit("/test/param")

            self.assertEqual(first, 10.0)
            self.assertEqual(second, 10.0)
            self.assertEqual(mock_ssm.get_parameter.call_count, 2)

    def test_get_limit_raises_on_ssm_error_with_no_cache(self):
        with patch.object(rate_limit, "ssm_client") as mock_ssm, patch(
            "time.time", side_effect=[100.0]
        ):
            mock_ssm.get_parameter.side_effect = Exception("boom")

            with self.assertRaises(Exception):
                rate_limit._get_limit("/test/param")


if __name__ == "__main__":
    unittest.main()
