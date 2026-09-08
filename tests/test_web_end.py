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
import _state


def _guess_state(seed=11):
    state = _engine.new_game(
        single_team=False,
        seats={"red_cm": "ai", "red_g": "human", "blue_cm": "ai", "blue_g": "ai"},
        seed=seed,
    )
    state["turn"] = "Red"
    state["phase"] = "guess"
    state["pending"] = {"clue": "VEHICLE", "number": 2, "made": 0}
    state["history"] = [["Red_Codemaster", "VEHICLE", 2]]
    return state


class WebEndReasonTest(unittest.TestCase):
    def test_assassin_guess_records_the_word_not_a_clear(self):
        state = _guess_state()
        idx = state["key"].index("Assassin")
        word = state["words"][idx]

        _engine.advance(state, {"type": "guess", "index": idx})
        shown = _engine.view(state)

        self.assertEqual(state["winner"], "B")
        self.assertEqual(state["end"]["reason"], "assassin")
        self.assertEqual(state["end"]["word"], word)
        self.assertEqual(state["end"]["by"], "Red")
        self.assertEqual(shown["end"]["reason"], "assassin")
        self.assertGreater(shown["counts"]["red_left"], 0)
        self.assertGreater(shown["counts"]["blue_left"], 0)

    def test_cleared_board_is_not_called_an_assassin(self):
        state = _guess_state()
        reds = [i for i, color in enumerate(state["key"]) if color == "Red"]
        for idx in reds[:-1]:
            state["revealed"][idx] = True

        _engine.advance(state, {"type": "guess", "index": reds[-1]})
        shown = _engine.view(state)

        self.assertEqual(state["winner"], "R")
        self.assertEqual(state["end"]["reason"], "cleared")
        self.assertEqual(state["end"]["word"], state["words"][reds[-1]])
        self.assertEqual(shown["end"]["reason"], "cleared")
        self.assertEqual(shown["counts"]["red_left"], 0)

    def test_guessing_the_opponent_last_card_wins_for_them(self):
        state = _guess_state()
        blues = [i for i, color in enumerate(state["key"]) if color == "Blue"]
        for idx in blues[:-1]:
            state["revealed"][idx] = True

        _engine.advance(state, {"type": "guess", "index": blues[-1]})
        shown = _engine.view(state)

        self.assertEqual(state["winner"], "B")
        self.assertEqual(state["end"]["reason"], "cleared")
        self.assertEqual(state["end"]["word"], state["words"][blues[-1]])
        self.assertEqual(state["end"]["by"], "Red")
        self.assertEqual(shown["counts"]["blue_left"], 0)
        self.assertGreater(shown["counts"]["red_left"], 0)

    def test_human_clue_rejects_board_words_and_repeats(self):
        state = _engine.new_game(
            single_team=False,
            seats={"red_cm": "human", "red_g": "ai", "blue_cm": "ai", "blue_g": "ai"},
            seed=5,
        )
        board_word = state["words"][0]
        returned, events, error = _engine.advance(
            state, {"type": "clue", "word": board_word, "number": 1}
        )
        self.assertIs(returned, state)
        self.assertEqual(events, [])
        self.assertIn("not on the board", error)
        self.assertEqual(state["phase"], "clue")
        self.assertIsNone(state["pending"])

        returned, events, error = _engine.advance(
            state, {"type": "clue", "word": "A", "number": 1}
        )
        self.assertIn("not on the board", error)
        self.assertEqual(state["phase"], "clue")

        safe = next(
            word
            for word in ("ORBITAL", "JAZZ", "QUARK", "VELVET")
            if _agents.legal_human_clue(word, state["words"], [])
        )
        _engine.advance(state, {"type": "clue", "word": safe, "number": 1})
        self.assertEqual(state["phase"], "guess")
        self.assertEqual(state["pending"]["clue"], safe)

        state["pending"] = None
        state["phase"] = "clue"
        state["turn"] = "Red"
        returned, events, error = _engine.advance(
            state, {"type": "clue", "word": safe, "number": 1}
        )
        self.assertIn("not on the board", error)
        self.assertIsNone(state["pending"])

    def test_human_operative_view_hides_unrevealed_labels(self):
        state = _guess_state()
        shown = _engine.view(state)
        self.assertEqual(shown["status"], "need_human_guess")
        hidden = [cell for cell in shown["cells"] if not cell["revealed"]]
        self.assertTrue(hidden)
        self.assertTrue(all(cell["label"] is None for cell in hidden))


class WebSecretTest(unittest.TestCase):
    def test_production_rejects_missing_or_default_secret(self):
        with unittest.mock.patch.dict(
            os.environ,
            {"VERCEL_ENV": "production", "CLUECAST_APP_SECRET": ""},
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                _state.encode({"ok": True})
        with unittest.mock.patch.dict(
            os.environ,
            {
                "VERCEL_ENV": "production",
                "CLUECAST_APP_SECRET": _state.INSECURE_DEFAULT_SECRET,
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                _state.encode({"ok": True})

    def test_production_accepts_a_unique_secret(self):
        with unittest.mock.patch.dict(
            os.environ,
            {"VERCEL_ENV": "production", "CLUECAST_APP_SECRET": "unique-prod-secret"},
            clear=False,
        ):
            token = _state.encode({"ok": True})
            self.assertEqual(_state.decode(token), {"ok": True})


if __name__ == "__main__":
    unittest.main()
