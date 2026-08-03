import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, ".")

from app.routes.published_api import _get_request_api_key_id


def _request_with_header(value):
    request = MagicMock()
    request.headers = {"x-amzn-request-context": value} if value is not None else {}
    return request


class TestGetRequestApiKeyId(unittest.TestCase):
    def test_returns_api_key_id_when_present(self):
        request = _request_with_header('{"identity": {"apiKeyId": "key-1"}}')
        self.assertEqual(_get_request_api_key_id(request), "key-1")

    def test_returns_none_when_header_missing(self):
        request = _request_with_header(None)
        self.assertIsNone(_get_request_api_key_id(request))

    def test_returns_none_when_header_is_malformed_json(self):
        request = _request_with_header("not-json")
        self.assertIsNone(_get_request_api_key_id(request))

    def test_returns_none_when_identity_missing(self):
        request = _request_with_header("{}")
        self.assertIsNone(_get_request_api_key_id(request))


if __name__ == "__main__":
    unittest.main()
