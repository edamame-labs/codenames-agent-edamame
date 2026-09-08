import os
import sys
import unittest
import unittest.mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API = str(ROOT / "api")
if API not in sys.path:
    sys.path.insert(0, API)

import _agents
import _engine


class WebHintTest(unittest.TestCase):
    def test_luna_is_the_default_hint_model(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_agents.hint_model_slug(), "openai/gpt-5.6-luna")

    def test_hint_agent_receives_no_unrevealed_key_labels(self):
        state = _engine.new_game(
            single_team=False,
            seats={"red_g": "human"},
            seed=1,
        )
        state["words"][:3] = ["KNOWN", "TARGET", "DANGER"]
        state["key"][:3] = ["Red", "Blue", "Assassin"]
        state["revealed"][0] = True
        state["phase"] = "guess"
        state["pending"] = {"clue": "AIM", "number": 1, "made": 0}
        state["history"] = [["Red_Codemaster", "AIM", 1]]

        with unittest.mock.patch.object(
            _engine._agents,
            "simple_hint",
            return_value=[
                ("TARGET", "Clearest ordinary match for AIM."),
                ("DANGER", "Another possible reading. It might be wrong."),
            ],
        ) as factory:
            suggestion, error = _engine.hint(state)

        factory.assert_called_once()
        team, board, clue, number = factory.call_args.args
        self.assertEqual(team, "Red")
        self.assertIsNone(error)
        self.assertEqual(
            [idea["word"] for idea in suggestion["ideas"]],
            ["TARGET", "DANGER"],
        )
        self.assertEqual(
            suggestion["ideas"][0]["reason"],
            "Clearest ordinary match for AIM.",
        )
        self.assertNotIn("word", suggestion)
        self.assertNotIn("reason", suggestion)
        self.assertEqual(board[0], "*RED*")
        self.assertEqual(board[1:3], ["TARGET", "DANGER"])
        self.assertEqual((clue, number), ("AIM", 1))
        self.assertNotIn("*BLUE*", board)
        self.assertNotIn("*ASSASSIN*", board)

    def test_hint_drops_duplicate_and_missing_words(self):
        state = _engine.new_game(
            single_team=False,
            seats={"red_g": "human"},
            seed=1,
        )
        state["words"][:2] = ["ALPHA", "BRAVO"]
        state["revealed"][0] = False
        state["phase"] = "guess"
        state["pending"] = {"clue": "START", "number": 2, "made": 0}

        with unittest.mock.patch.object(
            _engine._agents,
            "simple_hint",
            return_value=[
                ("ALPHA", "First idea."),
                ("MISSING", "Not on the board."),
                ("ALPHA", "Duplicate."),
                ("BRAVO", "Second idea."),
            ],
        ):
            suggestion, error = _engine.hint(state)

        self.assertIsNone(error)
        self.assertEqual(
            [(idea["word"], idea["reason"]) for idea in suggestion["ideas"]],
            [("ALPHA", "First idea."), ("BRAVO", "Second idea.")],
        )

    def test_hint_is_refused_when_it_is_not_the_human_guess(self):
        state = _engine.new_game(
            single_team=False,
            seats={"red_cm": "human", "red_g": "ai", "blue_cm": "ai", "blue_g": "ai"},
            seed=2,
        )
        state["phase"] = "guess"
        state["pending"] = {"clue": "AIM", "number": 1, "made": 0}

        with unittest.mock.patch.object(
            _engine._agents,
            "simple_hint",
            side_effect=AssertionError("hint should stay seat-gated"),
        ):
            suggestion, error = _engine.hint(state)

        self.assertIsNone(suggestion)
        self.assertIn("your guess", error)
        self.assertEqual(state["hints_remaining"], 3)


if __name__ == "__main__":
    unittest.main()
