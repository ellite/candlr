import unittest
from datetime import date

from app.events_logic import days_since, previous_occurrence


class PreviousOccurrenceTests(unittest.TestCase):
    def test_current_year_occurrence(self):
        today = date(2026, 9, 19)
        self.assertEqual(previous_occurrence(9, 18, today), date(2026, 9, 18))
        self.assertEqual(days_since(9, 18, today), 1)

    def test_previous_year_occurrence(self):
        today = date(2026, 1, 3)
        self.assertEqual(previous_occurrence(12, 30, today), date(2025, 12, 30))
        self.assertEqual(days_since(12, 30, today), 4)

    def test_today_is_zero_days_ago(self):
        today = date(2026, 9, 19)
        self.assertEqual(days_since(9, 19, today), 0)

    def test_february_29_uses_february_28_in_a_non_leap_year(self):
        today = date(2025, 3, 1)
        self.assertEqual(previous_occurrence(2, 29, today), date(2025, 2, 28))
        self.assertEqual(days_since(2, 29, today), 1)


if __name__ == "__main__":
    unittest.main()
