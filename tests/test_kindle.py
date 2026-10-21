"""The morning page's Reminders columns in kindle.py."""

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import support  # noqa: F401  (sets up the import path and env)
import kindle

TODAY = date(2026, 10, 14)


def rem(title, due=None, lst="Reminders", done=False):
    """One reminder in remindctl's `show all --json` shape. Due times are noon UTC, which
    is the same calendar day in every time zone the tests might run in."""
    return {"id": title, "title": title, "dueDate": f"{due}T12:00:00Z" if due else None,
            "isCompleted": done, "completionDate": None, "listName": lst,
            "listID": lst.lower(), "priority": 0}


SAMPLE = [
    rem("Undated chore"),
    rem("Long overdue", "2026-09-20"),         # 24 days late, past the 14 day window
    rem("Recently overdue", "2026-10-12"),
    rem("Later this week", "2026-10-17"),
    rem("Due today", "2026-10-14"),
    rem("Already done", "2026-10-14", done=True),
    rem("Milk", lst="Groceries"),               # not a left-column list
    rem("Ship the release \U0001F680", "2026-10-15", lst="Work"),
]


class Reminders(unittest.TestCase):
    def setUp(self):
        self.mine, self.work = kindle.reminders(SAMPLE, TODAY)

    def titles(self, items):
        return [t["text"] for t in items]

    def test_splits_work_from_the_rest(self):
        self.assertEqual(self.titles(self.work), ["Ship the release"])
        self.assertNotIn("Ship the release", self.titles(self.mine))

    def test_skips_completed_and_other_lists(self):
        self.assertNotIn("Already done", self.titles(self.mine))
        self.assertNotIn("Milk", self.titles(self.mine))

    def test_strips_emoji(self):
        self.assertEqual(self.work[0]["text"], "Ship the release")

    def test_flags_overdue(self):
        flags = {t["text"]: t["overdue"] for t in self.mine}
        self.assertTrue(flags["Recently overdue"])
        self.assertTrue(flags["Long overdue"])
        self.assertFalse(flags["Due today"])

    def test_order_puts_long_overdue_last(self):
        self.assertEqual(self.titles(self.mine), ["Recently overdue", "Due today", "Later this week",
                                                  "Undated chore", "Long overdue"])

    def test_skip_lists_leave_a_list_off(self):
        with mock.patch.object(kindle, "SKIP_LISTS", {"work"}):
            _, work = kindle.reminders(SAMPLE, TODAY)
        self.assertEqual(work, [])

    def test_missing_file_means_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(kindle, "REMINDERS_JSON", Path(tmp) / "reminders.json"):
                self.assertIsNone(kindle.reminders())


class LocalDay(unittest.TestCase):
    def test_utc_stamp(self):
        self.assertEqual(kindle._local_day("2026-10-13T12:00:00Z"), "2026-10-13")

    def test_bad_input(self):
        for bad in ("", "not a date", None):
            with self.subTest(bad=bad):
                self.assertEqual(kindle._local_day(bad), "")


class Page(unittest.TestCase):
    NOW = datetime(2026, 10, 14, 5, 30)
    HABITS = {"tasks": [{"text": "Note task", "done": False}]}

    def render(self, rem):
        return kindle.page(self.HABITS, {}, wx={}, now=self.NOW, rem=rem)

    def test_reminders_layout(self):
        html = self.render(kindle.reminders(SAMPLE, TODAY))
        self.assertIn("<h2>Reminders</h2>", html)
        self.assertIn("<h2>Work</h2>", html)
        # Five reminders plus the note task, three shown.
        self.assertIn("and 3 more in Reminders", html)

    def test_empty_work_column(self):
        self.assertIn("Nothing for work.", self.render(([], [])))

    def test_falls_back_to_notes_layout(self):
        html = self.render(None)
        self.assertIn("<h2>Today</h2>", html)
        self.assertNotIn("<h2>Work</h2>", html)


if __name__ == "__main__":
    unittest.main()
