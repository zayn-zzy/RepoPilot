import unittest

from pkg.utils import add, multiply


class TestUtils(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

    def test_multiply(self):
        # This must fail against the injected bug (3 * 4 != 7).
        self.assertEqual(multiply(3, 4), 12)


if __name__ == "__main__":
    unittest.main()
