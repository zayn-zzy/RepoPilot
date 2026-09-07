import unittest

from pkg.utils import add


class TestIntegration(unittest.TestCase):
    def test_add_roundtrip(self):
        # A slow-path stand-in for integration coverage (kept separate so the
        # pipeline's unit stage excludes this directory).
        total = sum(add(i, 1) for i in range(100))
        self.assertEqual(total, 5050)


if __name__ == "__main__":
    unittest.main()
