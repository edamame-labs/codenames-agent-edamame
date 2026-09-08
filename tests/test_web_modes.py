import json
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
import game as game_api


SEATS = ("red_cm", "red_g", "blue_cm", "blue_g")


class WebModeConfigTest(unittest.TestCase):
    def test_new_game_accepts_each_supported_player_perspective(self):
        configurations = [
            (True, None),
            (True, "red_cm"),
            (True, "red_g"),
            (False, None),
            (False, "red_cm"),
            (False, "red_g"),
            (False, "blue_cm"),
            (False, "blue_g"),
        ]
        for single_team, human_seat in configurations:
            with self.subTest(single_team=single_team, human_seat=human_seat):
                seats = {seat: "ai" for seat in SEATS}
                if human_seat:
                    seats[human_seat] = "human"
                code, body = game_api._handle(
                    {
                        "op": "new",
                        "single_team": single_team,
                        "seats": seats,
                        "seed": 17,
                    }
                )
                self.assertEqual(code, 200)
                self.assertIn("token", body)
                self.assertEqual(body["view"]["seats"], seats)

    def test_new_game_rejects_ambiguous_or_unused_human_seats(self):
        invalid = [
            (
                True,
                {"blue_g": "human"},
                "Blue seats are not used",
            ),
            (
                False,
                {"red_g": "human", "blue_g": "human"},
                "at most one human",
            ),
        ]
        for single_team, seats, expected in invalid:
            with self.subTest(single_team=single_team, seats=seats):
                code, body = game_api._handle(
                    {
                        "op": "new",
                        "single_team": single_team,
                        "seats": seats,
                    }
                )
                self.assertEqual(code, 400)
                self.assertIn(expected, body["error"])

    def test_new_game_rejects_malformed_custom_setup(self):
        code, body = game_api._handle(
            {"op": "new", "single_team": "false", "seats": {}}
        )
        self.assertEqual(code, 400)
        self.assertIn("boolean", body["error"])

        code, body = game_api._handle(
            {"op": "new", "single_team": False, "seats": {"red_g": "person"}}
        )
        self.assertEqual(code, 400)
        self.assertIn("ai or human", body["error"])


class WebModeEngineTest(unittest.TestCase):
    def test_single_team_codemaster_uses_clue_count_objective(self):
        agent = _agents.WebCodemaster("Red", client=object(), model="test")
        agent.set_single_team(True)
        agent.set_game_state(
            ["DOG", "CAT", "MOON", "STAR", "WAR", "BOMB", "TABLE"],
            ["Red", "Red", "Red", "Red", "Blue", "Blue", "Civilian"],
        )

        instructions, payload = agent._prompt_payload(agent._pairs())
        state = json.loads(payload)

        self.assertEqual(state["track"], "single_team")
        self.assertEqual(state["scoreboard"]["race_state"], "single")
        self.assertEqual(state["scoreboard"]["deficit"], 0)
        self.assertFalse(state["scoreboard"]["far_behind"])
        self.assertIn("few clues", state["strategy"]["objective"])
        self.assertIn("- single: fewer clues is better", instructions)

    def test_engine_passes_track_to_ai_codemaster(self):
        class FakeCodemaster:
            def __init__(self):
                self.single_team = None

            def set_single_team(self, enabled):
                self.single_team = enabled

            def set_move_history(self, _history):
                return None

            def set_game_state(self, _board, _key):
                return None

            def get_clue(self):
                return "ORBITAL", 1

        for single_team in (True, False):
            with self.subTest(single_team=single_team):
                state = _engine.new_game(
                    single_team=single_team,
                    seats={},
                    seed=18,
                )
                agent = FakeCodemaster()
                with unittest.mock.patch.object(
                    _engine._agents,
                    "codemaster",
                    return_value=agent,
                ):
                    _engine.advance(state, None)
                self.assertIs(agent.single_team, single_team)

    def test_blue_operative_view_stays_key_blind(self):
        state = _engine.new_game(
            single_team=False,
            seats={"blue_g": "human"},
            seed=19,
        )
        initial = _engine.view(state)
        self.assertEqual(initial["status"], "ai_turn")
        self.assertTrue(all(cell["label"] is None for cell in initial["cells"]))

        state["turn"] = "Blue"
        state["phase"] = "guess"
        state["pending"] = {"clue": "OCEAN", "number": 1, "made": 0}
        shown = _engine.view(state)
        self.assertEqual(shown["status"], "need_human_guess")
        self.assertEqual(shown["acting_seat"], "blue_g")
        self.assertTrue(all(cell["label"] is None for cell in shown["cells"]))

    def test_blue_spymaster_sees_key_and_clue_number_is_clamped(self):
        state = _engine.new_game(
            single_team=False,
            seats={"blue_cm": "human"},
            seed=23,
        )
        state["turn"] = "Blue"
        shown = _engine.view(state)
        self.assertEqual(shown["status"], "need_human_clue")
        self.assertEqual(shown["acting_seat"], "blue_cm")
        self.assertTrue(all(cell["label"] is not None for cell in shown["cells"]))

        clue = next(
            word
            for word in ("ORBITAL", "JAZZ", "QUARK", "VELVET")
            if _agents.legal_human_clue(word, state["words"], [])
        )
        _engine.advance(
            state,
            {"type": "clue", "word": clue, "number": 99},
        )
        self.assertEqual(state["pending"]["number"], state["key"].count("Blue"))
        self.assertEqual(state["phase"], "guess")

    def test_human_operative_gets_the_optional_bonus_guess(self):
        state = _engine.new_game(
            single_team=False,
            seats={"blue_g": "human"},
            seed=29,
        )
        blue = [i for i, label in enumerate(state["key"]) if label == "Blue"]
        state["turn"] = "Blue"
        state["phase"] = "guess"
        state["pending"] = {"clue": "PAIR", "number": 1, "made": 0}

        _engine.advance(state, {"type": "guess", "index": blue[0]})
        self.assertEqual(state["phase"], "guess")
        self.assertEqual(state["turn"], "Blue")
        self.assertEqual(state["pending"]["made"], 1)

        _engine.advance(state, {"type": "guess", "index": blue[1]})
        self.assertEqual(state["phase"], "clue")
        self.assertEqual(state["turn"], "Red")
        self.assertIsNone(state["pending"])

    def test_single_team_civilian_ends_turn_but_not_game(self):
        state = _engine.new_game(
            single_team=True,
            seats={"red_g": "human"},
            seed=31,
        )
        civilian = state["key"].index("Civilian")
        state["phase"] = "guess"
        state["pending"] = {"clue": "TEST", "number": 1, "made": 0}

        _engine.advance(state, {"type": "guess", "index": civilian})
        self.assertIsNone(state["winner"])
        self.assertEqual(state["phase"], "clue")
        self.assertEqual(state["turn"], "Red")

    def test_single_team_revealing_all_blue_cards_is_a_loss(self):
        state = _engine.new_game(
            single_team=True,
            seats={"red_g": "human"},
            seed=37,
        )
        blue = [i for i, label in enumerate(state["key"]) if label == "Blue"]
        for idx in blue[:-1]:
            state["revealed"][idx] = True
        state["phase"] = "guess"
        state["pending"] = {"clue": "TEST", "number": 1, "made": 0}

        _engine.advance(state, {"type": "guess", "index": blue[-1]})
        self.assertEqual(state["winner"], "B")
        self.assertEqual(state["phase"], "over")
        self.assertEqual(state["end"]["reason"], "cleared")
        self.assertEqual(state["end"]["word"], state["words"][blue[-1]])


if __name__ == "__main__":
    unittest.main()
