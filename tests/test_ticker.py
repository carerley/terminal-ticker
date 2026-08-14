import tempfile
import unittest
from pathlib import Path

from ticker.core import Cache, Quote, QuoteError, get_quote


class Provider:
    def __init__(self, value=None, error=None):
        self.value, self.error, self.calls = value, error, 0

    def get(self, symbol):
        self.calls += 1
        if self.error:
            raise self.error
        return self.value


def sample():
    return Quote("AAPL", "Apple Inc.", "USD", 231.0, 2.0, 0.87, 229.0, "REGULAR", 100)


class QuoteTests(unittest.TestCase):
    def test_fresh_cache_avoids_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Cache(Path(directory))
            cache.save(sample(), 100)
            provider = Provider(error=QuoteError("should not run"))
            result = get_quote("AAPL", provider, cache, ttl=30, now=120)
            self.assertTrue(result.cached)
            self.assertFalse(result.stale)
            self.assertEqual(provider.calls, 0)

    def test_stale_cache_falls_back_on_error(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Cache(Path(directory))
            cache.save(sample(), 100)
            result = get_quote(
                "AAPL", Provider(error=QuoteError("offline")), cache, ttl=30, now=200
            )
            self.assertTrue(result.cached)
            self.assertTrue(result.stale)

    def test_success_is_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Cache(Path(directory))
            result = get_quote("AAPL", Provider(value=sample()), cache, now=200)
            self.assertEqual(result.price, 231.0)
            self.assertIsNotNone(cache.load("AAPL"))


if __name__ == "__main__":
    unittest.main()
