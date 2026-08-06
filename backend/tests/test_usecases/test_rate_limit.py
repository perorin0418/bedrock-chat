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
        rate_limit._no_rate_limit_group_cache.clear()

    def test_skips_limit_check_when_exempt(self):
        with patch.object(
            rate_limit, "_is_rate_limit_exempt", return_value=True
        ), patch.object(rate_limit, "get_usage_status") as mock_get_usage_status:
            rate_limit.check_rate_limit("user-1")

            mock_get_usage_status.assert_not_called()

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


class TestIsRateLimitExempt(unittest.TestCase):
    def setUp(self):
        rate_limit._no_rate_limit_group_cache.clear()

    def test_not_exempt_when_user_pool_id_not_configured(self):
        with patch.object(rate_limit, "USER_POOL_ID", ""), patch.object(
            rate_limit, "cognito_client"
        ) as mock_cognito:
            self.assertFalse(rate_limit._is_rate_limit_exempt("user-1"))

            mock_cognito.admin_list_groups_for_user.assert_not_called()

    def test_exempt_when_user_in_no_rate_limit_group(self):
        with patch.object(rate_limit, "USER_POOL_ID", "us-east-1_test"), patch.object(
            rate_limit, "cognito_client"
        ) as mock_cognito:
            mock_cognito.admin_list_groups_for_user.return_value = {
                "Groups": [{"GroupName": "NoRateLimit"}]
            }

            self.assertTrue(rate_limit._is_rate_limit_exempt("user-1"))

    def test_not_exempt_when_user_not_in_no_rate_limit_group(self):
        with patch.object(rate_limit, "USER_POOL_ID", "us-east-1_test"), patch.object(
            rate_limit, "cognito_client"
        ) as mock_cognito:
            mock_cognito.admin_list_groups_for_user.return_value = {
                "Groups": [{"GroupName": "Admin"}]
            }

            self.assertFalse(rate_limit._is_rate_limit_exempt("user-1"))

    def test_caches_within_ttl(self):
        with patch.object(rate_limit, "USER_POOL_ID", "us-east-1_test"), patch.object(
            rate_limit, "cognito_client"
        ) as mock_cognito, patch("time.time", side_effect=[100.0, 130.0]):
            mock_cognito.admin_list_groups_for_user.return_value = {
                "Groups": [{"GroupName": "NoRateLimit"}]
            }

            first = rate_limit._is_rate_limit_exempt("user-1")
            second = rate_limit._is_rate_limit_exempt("user-1")

            self.assertTrue(first)
            self.assertTrue(second)
            mock_cognito.admin_list_groups_for_user.assert_called_once()

    def test_refetches_after_ttl_expires(self):
        with patch.object(rate_limit, "USER_POOL_ID", "us-east-1_test"), patch.object(
            rate_limit, "cognito_client"
        ) as mock_cognito, patch("time.time", side_effect=[100.0, 161.0]):
            mock_cognito.admin_list_groups_for_user.side_effect = [
                {"Groups": [{"GroupName": "NoRateLimit"}]},
                {"Groups": []},
            ]

            first = rate_limit._is_rate_limit_exempt("user-1")
            second = rate_limit._is_rate_limit_exempt("user-1")

            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(mock_cognito.admin_list_groups_for_user.call_count, 2)

    def test_falls_back_to_stale_cache_on_error(self):
        with patch.object(rate_limit, "USER_POOL_ID", "us-east-1_test"), patch.object(
            rate_limit, "cognito_client"
        ) as mock_cognito, patch("time.time", side_effect=[100.0, 161.0]):
            mock_cognito.admin_list_groups_for_user.side_effect = [
                {"Groups": [{"GroupName": "NoRateLimit"}]},
                Exception("boom"),
            ]

            first = rate_limit._is_rate_limit_exempt("user-1")
            second = rate_limit._is_rate_limit_exempt("user-1")

            self.assertTrue(first)
            self.assertTrue(second)

    def test_not_exempt_on_error_with_no_cache(self):
        with patch.object(rate_limit, "USER_POOL_ID", "us-east-1_test"), patch.object(
            rate_limit, "cognito_client"
        ) as mock_cognito, patch("time.time", side_effect=[100.0]):
            mock_cognito.admin_list_groups_for_user.side_effect = Exception("boom")

            self.assertFalse(rate_limit._is_rate_limit_exempt("user-1"))


class TestGetUsageStatus(unittest.TestCase):
    def setUp(self):
        rate_limit._limit_cache.clear()

    def test_returns_usage_and_limit_for_both_windows(self):
        with patch.object(
            rate_limit, "get_current_time", return_value=1_700_000_000_000
        ), patch.object(
            rate_limit, "_get_limit", side_effect=[10.0, 336.0]
        ), patch.object(
            rate_limit, "get_usage_since", side_effect=[5.0, 100.0]
        ):
            status = rate_limit.get_usage_status("user-1")

            self.assertEqual(status.five_hour.used, 5.0)
            self.assertEqual(status.five_hour.limit, 10.0)
            self.assertEqual(status.seven_day.used, 100.0)
            self.assertEqual(status.seven_day.limit, 336.0)


if __name__ == "__main__":
    unittest.main()
