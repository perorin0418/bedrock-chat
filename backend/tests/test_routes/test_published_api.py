import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, ".")

from app.routes.published_api import _get_request_api_key_id


def _request_with_header_values(values):
    request = MagicMock()
    request.headers.getlist.return_value = values
    return request


def _request_with_header(value):
    values = [] if value is None else [value]
    return _request_with_header_values(values)


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

    def test_uses_last_value_when_header_duplicated(self):
        # Simulates a spoofed client-supplied header followed by the Lambda
        # Web Adapter's real value, which it appends after any client value.
        spoofed = '{"identity": {"apiKeyId": "victim-key"}}'
        real = '{"identity": {"apiKeyId": "real-key"}}'
        request = _request_with_header_values([spoofed, real])
        self.assertEqual(_get_request_api_key_id(request), "real-key")

    def test_returns_api_key_id_when_single_legitimate_value(self):
        request = _request_with_header_values(
            ['{"identity": {"apiKeyId": "only-key"}}']
        )
        self.assertEqual(_get_request_api_key_id(request), "only-key")

    def test_returns_none_when_api_key_id_is_not_a_string(self):
        request = _request_with_header_values(['{"identity": {"apiKeyId": 123}}'])
        self.assertIsNone(_get_request_api_key_id(request))

        request_null = _request_with_header_values(['{"identity": {"apiKeyId": null}}'])
        self.assertIsNone(_get_request_api_key_id(request_null))


if __name__ == "__main__":
    unittest.main()
