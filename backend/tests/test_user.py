import sys
import unittest

sys.path.insert(0, ".")

from app.user import User


class TestUserRateLimitId(unittest.TestCase):
    def test_rate_limit_id_defaults_to_own_id(self):
        user = User(id="user-1", name="user-1", email="user-1@example.com", groups=[])
        self.assertIsNone(user.billing_user_id)
        self.assertEqual(user.rate_limit_id, "user-1")

    def test_rate_limit_id_uses_billing_user_id_when_set(self):
        user = User(
            id="PUBLISHED_API#bot-1",
            name="PUBLISHED_API#bot-1",
            email="PUBLISHED_API#bot-1",
            groups=["Admin"],
            billing_user_id="owner-1",
        )
        self.assertEqual(user.rate_limit_id, "owner-1")

    def test_from_published_api_id_without_billing_user_id(self):
        user = User.from_published_api_id("bot-1")
        self.assertEqual(user.id, "PUBLISHED_API#bot-1")
        self.assertIsNone(user.billing_user_id)
        self.assertEqual(user.rate_limit_id, "PUBLISHED_API#bot-1")

    def test_from_published_api_id_with_billing_user_id(self):
        user = User.from_published_api_id("bot-1", billing_user_id="owner-1")
        self.assertEqual(user.id, "PUBLISHED_API#bot-1")
        self.assertEqual(user.billing_user_id, "owner-1")
        self.assertEqual(user.rate_limit_id, "owner-1")


if __name__ == "__main__":
    unittest.main()
