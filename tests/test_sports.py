"""Watchability scoring and the spoiler check in sports.py."""

import unittest

import support  # noqa: F401  (sets up the import path and env)
import llm
import sports


def nba_game(home_score, away_score, period=4, season_type=2):
    """The smallest NBA game dict that watchability() accepts."""
    def team(tid, score):
        return {"id": tid, "name": f"Team {tid}", "score": score, "lines": [], "home": tid == "h"}
    return {"league": "NBA", "final": True, "state": "post", "detail": "Final",
            "home": team("h", home_score), "away": team("a", away_score),
            "period": period, "season_type": season_type, "note": "",
            "lead_changes": None, "max_deficit": None, "stars": [], "leaders": [],
            "goals": [], "red_cards": 0}


class Watchability(unittest.TestCase):
    def test_close_overtime_game_beats_a_blowout(self):
        close, why = sports.watchability(nba_game(112, 110, period=5))
        blowout, _ = sports.watchability(nba_game(130, 98))
        self.assertGreater(close, blowout)
        self.assertEqual(blowout, 0)
        self.assertIn("went to overtime", why)

    def test_preseason_costs_four_points(self):
        regular, _ = sports.watchability(nba_game(112, 110, period=5, season_type=2))
        preseason, _ = sports.watchability(nba_game(112, 110, period=5, season_type=1))
        self.assertEqual(regular - preseason, 4)

    def test_unfinished_game_scores_zero(self):
        g = nba_game(50, 48)
        g["final"] = False
        self.assertEqual(sports.watchability(g), (0, []))


class SpoilerCheck(unittest.TestCase):
    def test_rejects_results(self):
        for line in ("The Lakers won it in overtime.",
                     "Boston beat Miami on the road.",
                     "A 112-108 thriller in Denver."):
            with self.subTest(line=line):
                self.assertIsNotNone(sports.SPOILER.search(line))

    def test_accepts_a_clean_line(self):
        line = "Went to overtime, and the lead changed hands 14 times."
        self.assertIsNone(sports.SPOILER.search(line))

    def test_fallback_line_without_a_model_is_spoiler_free(self):
        llm.start(disabled=True)  # chat_json returns None, so the reasons are used
        g = nba_game(112, 110, period=5)
        g.update(away={**g["away"], "name": "Away"}, home={**g["home"], "name": "Home"})
        g["why"] = sports.watchability(g)[1]
        sports.watch_lines([g])
        self.assertEqual(g["watch_line"], "Came down to the final possessions and went to overtime.")
        self.assertIsNone(sports.SPOILER.search(g["watch_line"]))


if __name__ == "__main__":
    unittest.main()
