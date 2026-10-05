import unittest
from datetime import date

from app.events_logic import (
    NEVER,
    cadence_label,
    days_since,
    days_until,
    elapsed,
    needs_start_year,
    next_occurrence,
    occurs_on,
    previous_occurrence,
    start_year_problem,
)


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


class UnanchoredMonthlyTests(unittest.TestCase):
    """Every 1 month needs no start year, only the day."""

    def test_next_this_month_and_next_month(self):
        self.assertEqual(next_occurrence(3, 20, date(2026, 9, 19), unit="month"), date(2026, 9, 20))
        self.assertEqual(next_occurrence(3, 8, date(2026, 9, 19), unit="month"), date(2026, 10, 8))
        self.assertEqual(days_until(3, 8, date(2026, 9, 19), unit="month"), 19)

    def test_rolls_over_the_year(self):
        self.assertEqual(next_occurrence(1, 8, date(2026, 12, 19), unit="month"), date(2027, 1, 8))
        self.assertEqual(previous_occurrence(1, 20, date(2026, 1, 5), unit="month"), date(2025, 12, 20))

    def test_day_past_the_end_of_a_month_is_clamped(self):
        self.assertEqual(next_occurrence(1, 31, date(2026, 2, 10), unit="month"), date(2026, 2, 28))
        self.assertEqual(next_occurrence(1, 31, date(2028, 2, 10), unit="month"), date(2028, 2, 29))
        self.assertEqual(next_occurrence(1, 31, date(2026, 4, 30), unit="month"), date(2026, 4, 30))
        self.assertEqual(previous_occurrence(1, 30, date(2026, 3, 1), unit="month"), date(2026, 2, 28))

    def test_today_and_occurs_on(self):
        today = date(2026, 9, 8)
        self.assertEqual(days_until(1, 8, today, unit="month"), 0)
        self.assertEqual(days_since(1, 8, today, unit="month"), 0)
        self.assertTrue(occurs_on(1, 8, today, unit="month"))
        self.assertFalse(occurs_on(1, 8, today))
        self.assertTrue(occurs_on(1, 31, date(2026, 4, 30), unit="month"))
        self.assertFalse(occurs_on(1, 31, date(2026, 4, 29), unit="month"))


class AnchoredCadenceTests(unittest.TestCase):
    """Every other cadence counts from the full start date."""

    def test_every_37_days(self):
        # Jan 1, Feb 7, Mar 16 ...
        args = dict(year=2026, interval=37, unit="day")
        self.assertEqual(next_occurrence(1, 1, date(2026, 2, 8), **args), date(2026, 3, 16))
        self.assertEqual(previous_occurrence(1, 1, date(2026, 2, 8), **args), date(2026, 2, 7))
        self.assertEqual(days_until(1, 1, date(2026, 1, 1), **args), 0)
        self.assertEqual(days_since(1, 1, date(2026, 1, 1), **args), 0)
        self.assertTrue(occurs_on(1, 1, date(2026, 3, 16), **args))
        self.assertFalse(occurs_on(1, 1, date(2026, 3, 17), **args))

    def test_every_4_weeks(self):
        args = dict(year=2026, interval=4, unit="week")  # Tue Sep 1, Tue Sep 29 ...
        self.assertEqual(next_occurrence(9, 1, date(2026, 9, 2), **args), date(2026, 9, 29))
        self.assertEqual(days_until(9, 1, date(2026, 9, 2), **args), 27)
        self.assertEqual(previous_occurrence(9, 1, date(2026, 10, 20), **args), date(2026, 9, 29))

    def test_every_7_months(self):
        args = dict(year=2026, interval=7, unit="month")  # Jan 15, Aug 15, Mar 15 2027 ...
        self.assertEqual(next_occurrence(1, 15, date(2026, 9, 1), **args), date(2027, 3, 15))
        self.assertEqual(previous_occurrence(1, 15, date(2026, 9, 1), **args), date(2026, 8, 15))
        self.assertEqual(next_occurrence(1, 15, date(2026, 8, 15), **args), date(2026, 8, 15))

    def test_every_3_months_clamps_to_the_end_of_short_months(self):
        args = dict(year=2026, interval=3, unit="month")  # Jan 31, Apr 30, Jul 31, Oct 31
        self.assertEqual(next_occurrence(1, 31, date(2026, 2, 1), **args), date(2026, 4, 30))
        self.assertEqual(next_occurrence(1, 31, date(2026, 5, 1), **args), date(2026, 7, 31))

    def test_every_5_years(self):
        args = dict(year=2020, interval=5, unit="year")
        self.assertEqual(next_occurrence(3, 8, date(2026, 1, 1), **args), date(2030, 3, 8))
        self.assertEqual(previous_occurrence(3, 8, date(2026, 1, 1), **args), date(2025, 3, 8))
        self.assertEqual(days_until(3, 8, date(2020, 3, 8), **args), 0)
        self.assertEqual(next_occurrence(3, 8, date(2025, 3, 9), **args), date(2030, 3, 8))

    def test_every_4_years_from_a_leap_day(self):
        args = dict(year=2024, interval=4, unit="year")
        self.assertEqual(next_occurrence(2, 29, date(2024, 3, 1), **args), date(2028, 2, 29))

    def test_a_start_date_in_the_future_has_not_happened_yet(self):
        args = dict(year=2027, interval=10, unit="day")
        today = date(2026, 12, 25)
        self.assertEqual(next_occurrence(1, 1, today, **args), date(2027, 1, 1))
        self.assertEqual(days_until(1, 1, today, **args), 7)
        self.assertIsNone(previous_occurrence(1, 1, today, **args))
        self.assertEqual(days_since(1, 1, today, **args), NEVER)

    def test_missing_year_falls_back_to_the_month_day_rule(self):
        self.assertEqual(next_occurrence(1, 8, date(2026, 9, 19), interval=7, unit="month"), date(2026, 10, 8))
        self.assertEqual(next_occurrence(3, 8, date(2026, 9, 19), interval=5, unit="year"), date(2027, 3, 8))

    def test_elapsed_counts_in_the_types_unit(self):
        self.assertEqual(elapsed(3, 8, 2026, "month", date(2026, 10, 8)), 7)
        self.assertEqual(elapsed(1, 1, 2026, "week", date(2026, 2, 5)), 5)
        self.assertEqual(elapsed(1, 1, 2026, "day", date(2026, 2, 7)), 37)
        self.assertEqual(elapsed(3, 8, 2020, "year", date(2030, 3, 8)), 10)

    def test_start_year_rules(self):
        self.assertFalse(needs_start_year(1, "year"))
        self.assertFalse(needs_start_year(1, "month"))
        for interval, unit in [(2, "year"), (2, "month"), (1, "week"), (1, "day"), (37, "day")]:
            self.assertTrue(needs_start_year(interval, unit))
            self.assertIn("full start date", start_year_problem(interval, unit, False))
            self.assertIsNone(start_year_problem(interval, unit, True))
        self.assertIsNone(start_year_problem(1, "year", False))
        self.assertEqual(cadence_label(1, "week"), "every week")
        self.assertEqual(cadence_label(37, "day"), "every 37 days")


if __name__ == "__main__":
    unittest.main()
