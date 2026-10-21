"""House rules enforced in code: no investment advice in Markets, no long dashes anywhere."""

import unittest

import support  # noqa: F401  (sets up the import path and env)
import llm
import paper_sources

EM, EN = "\u2014", "\u2013"  # written as escapes so the source itself stays dash-free


class MarketsAdvice(unittest.TestCase):
    def test_catches_advice(self):
        for line in ("You should buy the dip while it lasts.",
                     "Analysts lifted their price target to 250 dollars.",
                     "Now may be a good time to sell."):
            with self.subTest(line=line):
                self.assertIsNotNone(paper_sources.ADVICE.search(line))

    def test_passes_plain_facts(self):
        for line in ("The S&P 500 rose 0.4% as bond yields fell.",
                     "Stocks slid in a broad sell-off after the jobs report."):
            with self.subTest(line=line):
                self.assertIsNone(paper_sources.ADVICE.search(line))


class Dedash(unittest.TestCase):
    def test_dash_between_clauses_becomes_a_comma(self):
        self.assertEqual(llm.dedash(f"The market rose {EM} then it fell."), "The market rose, then it fell.")

    def test_trailing_dash_ends_the_sentence(self):
        self.assertEqual(llm.dedash(f"He paused {EM}"), "He paused.")

    def test_leading_attribution_dash_is_dropped(self):
        self.assertEqual(llm.dedash(f"{EM} Marcus Aurelius"), "Marcus Aurelius")

    def test_number_ranges_keep_a_hyphen(self):
        self.assertEqual(llm.dedash(f"Pages 10{EN}12"), "Pages 10-12")

    def test_text_without_dashes_is_untouched(self):
        self.assertEqual(llm.dedash("Nothing to fix here."), "Nothing to fix here.")


if __name__ == "__main__":
    unittest.main()
