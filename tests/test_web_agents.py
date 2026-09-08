import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
API = str(ROOT / "api")
if API not in sys.path:
    sys.path.insert(0, API)

import _agents
import _engine
import _openrouter


class RecordingResponses:
    def __init__(self, parsed):
        self.parsed = parsed
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.parsed)


class WebAgentTest(unittest.TestCase):
    def test_openrouter_forwards_reasoning_configuration(self):
        responses = _openrouter._Responses("test-key", "https://example.test", {})
        payload = {
            "choices": [
                {
                    "message": {
                        "content": '{"clue":"MUSIC","targets":["PIANO"]}'
                    }
                }
            ]
        }
        with unittest.mock.patch.object(
            responses,
            "_post",
            return_value=payload,
        ) as post:
            result = responses.parse(
                model="test-terra",
                instructions="Return a clue.",
                input="{}",
                text_format=_agents.WebClueResponse,
                reasoning={"effort": "none"},
                max_output_tokens=800,
            )

        self.assertEqual(result.output_parsed.number, 1)
        self.assertEqual(
            post.call_args.args[0]["reasoning"],
            {"effort": "none"},
        )

    def test_openrouter_identifies_empty_final_content(self):
        responses = _openrouter._Responses("test-key", "https://example.test", {})
        payload = {"choices": [{"message": {"content": None}}]}
        with unittest.mock.patch.object(
            responses,
            "_post",
            return_value=payload,
        ):
            result = responses.parse(
                model="test-terra",
                instructions="Return a clue.",
                input="{}",
                text_format=_agents.WebClueResponse,
            )

        self.assertIsNone(result.output_parsed)
        self.assertEqual(result.parse_error, "empty_content")

    def test_parse_diagnostics_do_not_include_model_values(self):
        try:
            _agents.WebClueResponse.model_validate_json(
                '{"clue":"MUSIC","targets":"SECRET_VALUE"}'
            )
        except Exception as exc:
            code = _openrouter._parse_error_code(exc)
        else:
            self.fail("invalid response unexpectedly parsed")

        self.assertIn("targets:list_type", code)
        self.assertNotIn("SECRET_VALUE", code)

    def test_default_models_are_terra_and_luna(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_agents.model_slug(), "openai/gpt-5.6-terra")
            self.assertEqual(
                _agents.simulator_model_slug(),
                "openai/gpt-5.6-luna",
            )

    def test_web_codemaster_allows_model_to_choose_beyond_two(self):
        agent = _agents.WebCodemaster("Red", client=object(), model="test-terra")
        self.assertEqual(agent._allowed_numbers(5), [1, 2, 3, 4, 5])
        response = _agents.WebClueResponse(
            clue="MUSIC",
            targets=["PIANO", "DRUM", "FLUTE", "SONG"],
        )
        self.assertEqual(response.number, 4)
        self.assertEqual(
            set(_agents.WebClueResponse.model_json_schema()["properties"]),
            {"clue", "targets"},
        )

    def test_codemaster_retries_once_then_succeeds(self):
        agent = _agents.WebCodemaster("Red", client=object(), model="test-terra")
        agent.set_game_state(
            ["DOG", "CAT", "WAR"],
            ["Red", "Red", "Assassin"],
        )
        selection = _agents.codemaster_module.Selection(
            "ANIMALS",
            2,
            ("DOG", "CAT"),
        )
        with unittest.mock.patch.object(
            agent,
            "_model_clue",
            side_effect=[
                (None, "generator_malformed_response", None),
                (selection, None, None),
            ],
        ) as model_clue:
            self.assertEqual(agent.get_clue(), ("ANIMALS", 2))

        self.assertEqual(model_clue.call_count, 2)
        self.assertEqual(agent.harness_events[0]["attempt"], 1)
        self.assertEqual(agent.harness_events[1]["attempt"], 2)
        self.assertEqual(
            agent.retry_feedback[0]["required_fields"],
            ["clue", "targets"],
        )

    def test_retry_prompt_explicitly_repairs_generator_format(self):
        agent = _agents.WebCodemaster("Red", client=object(), model="test-terra")
        agent.set_game_state(
            ["DOG", "CAT", "WAR"],
            ["Red", "Red", "Assassin"],
        )
        agent.retry_feedback.append(
            {
                "rejection": "generator_malformed_response[number:int_parsing]",
                "required_fields": ["clue", "targets"],
                "number_source": "targets.length",
            }
        )

        instructions, payload = agent._prompt_payload(agent._pairs())

        self.assertIn("FORMAT REPAIR REQUIRED", instructions)
        self.assertIn("Do not return a number field", instructions)
        self.assertEqual(
            json.loads(payload)["rejected_attempts"][0]["number_source"],
            "targets.length",
        )

    def test_behind_prompt_forbids_slow_ones(self):
        agent = _agents.WebCodemaster("Red", client=object(), model="test-terra")
        agent.set_game_state(
            ["DOG", "CAT", "BIRD", "FISH", "MOON", "SUN", "WAR"],
            ["Red", "Red", "Red", "Red", "Blue", "Blue", "Assassin"],
        )

        instructions, payload = agent._prompt_payload(agent._pairs())
        state = json.loads(payload)

        self.assertEqual(state["scoreboard"]["race_state"], "behind")
        self.assertEqual(state["scoreboard"]["own_remaining"], 4)
        self.assertEqual(state["scoreboard"]["opponent_remaining"], 2)
        self.assertEqual(state["scoreboard"]["deficit"], 2)
        self.assertTrue(state["scoreboard"]["far_behind"])
        self.assertTrue(state["scoreboard"]["late_behind"])
        self.assertEqual(state["scoreboard"]["minimum_preferred_number"], 3)
        self.assertIn("behind by 2+", state["strategy"]["number_one_policy"])
        self.assertIn("too far behind", state["strategy"]["objective"])
        self.assertIn("be more aggressive", instructions)
        self.assertIn("A clue of 1 is a last resort", instructions)
        self.assertNotIn("Protect the assassin first, then opponent cards", instructions)

    def test_behind_by_one_near_finish_prefers_three(self):
        agent = _agents.WebCodemaster("Red", client=object(), model="test-terra")
        agent.set_game_state(
            ["DOG", "CAT", "BIRD", "FISH", "MOON", "SUN", "STAR", "WAR"],
            ["Red", "Red", "Red", "Red", "Blue", "Blue", "Blue", "Assassin"],
        )

        _instructions, payload = agent._prompt_payload(agent._pairs())
        state = json.loads(payload)

        self.assertEqual(state["scoreboard"]["race_state"], "behind")
        self.assertEqual(state["scoreboard"]["deficit"], 1)
        self.assertFalse(state["scoreboard"]["far_behind"])
        self.assertTrue(state["scoreboard"]["late_behind"])
        self.assertEqual(state["scoreboard"]["minimum_preferred_number"], 3)

    def test_behind_by_one_early_prefers_two(self):
        agent = _agents.WebCodemaster("Red", client=object(), model="test-terra")
        agent.set_game_state(
            ["DOG", "CAT", "BIRD", "FISH", "LION", "MOON", "SUN", "STAR", "SKY", "SEA", "WAR"],
            ["Red", "Red", "Red", "Red", "Red", "Blue", "Blue", "Blue", "Blue", "Assassin", "Civilian"],
        )

        _instructions, payload = agent._prompt_payload(agent._pairs())
        state = json.loads(payload)

        self.assertEqual(state["scoreboard"]["own_remaining"], 5)
        self.assertEqual(state["scoreboard"]["opponent_remaining"], 4)
        self.assertEqual(state["scoreboard"]["deficit"], 1)
        self.assertFalse(state["scoreboard"]["far_behind"])
        self.assertFalse(state["scoreboard"]["late_behind"])
        self.assertEqual(state["scoreboard"]["minimum_preferred_number"], 2)
        self.assertIn("losing move when behind", state["strategy"]["number_one_policy"])

    def test_ahead_prompt_allows_cleaner_play(self):
        agent = _agents.WebCodemaster("Red", client=object(), model="test-terra")
        agent.set_game_state(
            ["DOG", "CAT", "MOON", "SUN", "STAR", "SKY", "WAR"],
            ["Red", "Red", "Blue", "Blue", "Blue", "Blue", "Assassin"],
        )

        _instructions, payload = agent._prompt_payload(agent._pairs())
        state = json.loads(payload)

        self.assertEqual(state["scoreboard"]["race_state"], "ahead")
        self.assertEqual(state["scoreboard"]["minimum_preferred_number"], 1)
        self.assertIn("use 1 only when no coherent group", state["strategy"]["number_one_policy"])

    def test_codemaster_raises_after_automatic_attempts(self):
        agent = _agents.WebCodemaster("Red", client=object(), model="test-terra")
        agent.set_game_state(["DOG", "WAR"], ["Red", "Assassin"])
        with unittest.mock.patch.object(
            agent,
            "_model_clue",
            return_value=(None, "generator_api_error", None),
        ):
            with self.assertRaises(_agents.AgentTurnError):
                agent.get_clue()

        self.assertEqual(len(agent.harness_events), 3)

    def test_luna_simulator_receives_no_key(self):
        parsed = _agents.ListenerSimulationResponse(
            strategy="Guess the two direct animal matches, then stop.",
            ranking=["DOG", "CAT", "TABLE"],
            planned_guesses=2,
        )
        responses = RecordingResponses(parsed)
        client = SimpleNamespace(responses=responses)
        agent = _agents.WebCodemaster("Red", client=client, model="test-terra")
        groups = {
            "own": ["DOG", "CAT"],
            "opponent": ["MOON"],
            "civilian": ["TABLE"],
            "assassin": ["WAR"],
        }
        selection = _agents.codemaster_module.Selection(
            "ANIMALS",
            2,
            ("DOG", "CAT"),
        )

        result, reason = agent._gate(selection, None, groups)

        self.assertEqual(result, selection)
        self.assertIsNone(reason)
        self.assertEqual(len(responses.calls), 2)
        payloads = [json.loads(call["input"]) for call in responses.calls]
        self.assertEqual(
            {call["model"] for call in responses.calls},
            {"openai/gpt-5.6-luna"},
        )
        self.assertEqual(
            {payload["listener_profile"] for payload in payloads},
            {
                "literal player; follow the announced count for plausible matches",
                "broad associative player; explore multiple natural matches",
            },
        )
        for call, payload in zip(responses.calls, payloads):
            self.assertEqual(
                set(payload),
                {
                    "clue",
                    "number",
                    "live_words",
                    "listener_profile",
                    "public_history",
                    "unfinished_previous_clues",
                    "maximum_actions",
                },
            )
            self.assertNotIn("own", call["input"].lower())
            self.assertNotIn("assassin", call["input"].lower())

    def test_later_assassin_action_clamps_before_danger(self):
        agent = _agents.WebCodemaster("Red", client=object(), model="test-terra")
        groups = {
            "own": ["DOG", "CAT"],
            "opponent": ["MOON"],
            "civilian": ["TABLE"],
            "assassin": ["WAR"],
        }
        selection = _agents.codemaster_module.Selection(
            "ANIMALS",
            2,
            ("DOG", "CAT"),
        )
        plans = [
            {
                "profile": "listener one",
                "strategy": "Take the pair and stop.",
                "ranking": ["DOG", "CAT", "TABLE"],
                "actions": [
                    {"word": "DOG", "continue_if_correct": True},
                    {"word": "CAT", "continue_if_correct": False},
                ],
            },
            {
                "profile": "listener two",
                "strategy": "Continue into a risky second guess.",
                "ranking": ["DOG", "WAR", "CAT"],
                "actions": [
                    {"word": "DOG", "continue_if_correct": True},
                    {"word": "WAR", "continue_if_correct": False},
                ],
            },
        ]

        with unittest.mock.patch.object(
            agent,
            "_simulate_listener",
            return_value=(plans, None),
        ):
            result, reason = agent._gate(selection, None, groups)

        self.assertEqual(result.number, 1)
        self.assertEqual(result.targets, ("DOG",))
        self.assertEqual(reason, "simulators_clamped_before_risk")

    def test_behind_keeps_number_when_second_guess_is_civilian(self):
        agent = _agents.WebCodemaster("Red", client=object(), model="test-terra")
        groups = {
            "own": ["DOG", "CAT", "BIRD", "FISH"],
            "opponent": ["MOON", "SUN"],
            "civilian": ["TABLE"],
            "assassin": ["WAR"],
        }
        selection = _agents.codemaster_module.Selection(
            "ANIMALS",
            2,
            ("DOG", "CAT"),
        )
        plans = [
            {
                "profile": "listener one",
                "strategy": "Take the first animal, then a stretch.",
                "ranking": ["DOG", "TABLE", "CAT"],
                "actions": [
                    {"word": "DOG", "continue_if_correct": True},
                    {"word": "TABLE", "continue_if_correct": False},
                ],
            },
            {
                "profile": "listener two",
                "strategy": "Take the first animal, then a stretch.",
                "ranking": ["CAT", "TABLE", "DOG"],
                "actions": [
                    {"word": "CAT", "continue_if_correct": True},
                    {"word": "TABLE", "continue_if_correct": False},
                ],
            },
        ]

        with unittest.mock.patch.object(
            agent,
            "_simulate_listener",
            return_value=(plans, None),
        ):
            result, reason = agent._gate(selection, None, groups)

        self.assertEqual(result, selection)
        self.assertIsNone(reason)

    def test_assassin_as_first_action_still_rejects_clue(self):
        agent = _agents.WebCodemaster("Red", client=object(), model="test-terra")
        groups = {
            "own": ["DOG", "CAT"],
            "opponent": ["MOON"],
            "civilian": ["TABLE"],
            "assassin": ["WAR"],
        }
        selection = _agents.codemaster_module.Selection(
            "ANIMALS",
            2,
            ("DOG", "CAT"),
        )
        plans = [
            {
                "profile": "listener one",
                "strategy": "Guess the dangerous first association.",
                "ranking": ["WAR", "DOG", "CAT"],
                "actions": [
                    {"word": "WAR", "continue_if_correct": False},
                ],
            },
            {
                "profile": "listener two",
                "strategy": "Take the animal pair.",
                "ranking": ["DOG", "CAT", "TABLE"],
                "actions": [
                    {"word": "DOG", "continue_if_correct": True},
                    {"word": "CAT", "continue_if_correct": False},
                ],
            },
        ]

        with unittest.mock.patch.object(
            agent,
            "_simulate_listener",
            return_value=(plans, None),
        ):
            result, reason = agent._gate(selection, None, groups)

        self.assertIsNone(result)
        self.assertEqual(reason, "listener_1_assassin_first")

    def test_number_clamps_to_both_listeners_supported_length(self):
        agent = _agents.WebCodemaster("Red", client=object(), model="test-terra")
        groups = {
            "own": ["PIANO", "DRUM", "FLUTE", "SONG"],
            "opponent": ["MOON", "SHIP", "CROWN", "GATE", "TOWER"],
            "civilian": ["TABLE"],
            "assassin": ["WAR"],
        }
        selection = _agents.codemaster_module.Selection(
            "MUSIC",
            4,
            ("PIANO", "DRUM", "FLUTE", "SONG"),
        )
        plans = [
            {
                "profile": "listener one",
                "strategy": "Take three strong matches and stop.",
                "ranking": ["PIANO", "DRUM", "FLUTE", "MOON", "TABLE"],
                "actions": [
                    {"word": "PIANO", "continue_if_correct": True},
                    {"word": "DRUM", "continue_if_correct": True},
                    {"word": "FLUTE", "continue_if_correct": False},
                ],
            },
            {
                "profile": "listener two",
                "strategy": "Take three music matches and stop.",
                "ranking": ["DRUM", "PIANO", "SONG", "TABLE", "MOON"],
                "actions": [
                    {"word": "DRUM", "continue_if_correct": True},
                    {"word": "PIANO", "continue_if_correct": True},
                    {"word": "SONG", "continue_if_correct": False},
                ],
            },
        ]

        with unittest.mock.patch.object(
            agent,
            "_simulate_listener",
            return_value=(plans, None),
        ):
            result, reason = agent._gate(selection, None, groups)

        self.assertEqual(result.number, 3)
        self.assertEqual(result.targets, ("PIANO", "DRUM", "FLUTE"))
        self.assertEqual(reason, "simulators_clamped_before_risk")

    def test_early_stopping_no_longer_forces_number_down(self):
        agent = _agents.WebCodemaster("Red", client=object(), model="test-terra")
        groups = {
            "own": ["PIANO", "DRUM", "FLUTE", "SONG"],
            "opponent": ["MOON"],
            "civilian": ["TABLE"],
            "assassin": ["WAR"],
        }
        selection = _agents.codemaster_module.Selection(
            "MUSIC",
            4,
            ("PIANO", "DRUM", "FLUTE", "SONG"),
        )
        plans = [
            {
                "profile": "listener one",
                "strategy": "Make one cautious guess.",
                "ranking": ["PIANO", "DRUM", "FLUTE", "SONG", "TABLE"],
                "actions": [
                    {"word": "PIANO", "continue_if_correct": False},
                ],
            },
            {
                "profile": "listener two",
                "strategy": "Make one cautious guess.",
                "ranking": ["DRUM", "PIANO", "SONG", "FLUTE", "MOON"],
                "actions": [
                    {"word": "DRUM", "continue_if_correct": False},
                ],
            },
        ]

        with unittest.mock.patch.object(
            agent,
            "_simulate_listener",
            return_value=(plans, None),
        ):
            result, reason = agent._gate(selection, None, groups)

        self.assertEqual(result, selection)
        self.assertIsNone(reason)

    def test_engine_preserves_turn_for_manual_retry(self):
        state = _engine.new_game(
            single_team=True,
            seats={"red_cm": "ai", "red_g": "human"},
            seed=7,
        )
        failed_agent = unittest.mock.Mock()
        failed_agent.get_clue.side_effect = _agents.AgentTurnError("failed")

        with unittest.mock.patch.object(
            _engine._agents,
            "codemaster",
            return_value=failed_agent,
        ):
            returned, events, error = _engine.advance(state, None)

        self.assertIs(returned, state)
        self.assertEqual(events, [])
        self.assertEqual(state["phase"], "clue")
        self.assertIsNone(state["pending"])
        self.assertIn("try again", error.lower())

    def test_ai_guesser_reveals_one_card_per_step(self):
        state = _engine.new_game(
            single_team=False,
            seats={"red_cm": "ai", "red_g": "ai", "blue_cm": "ai", "blue_g": "ai"},
            seed=3,
        )
        state["words"][0] = "DOG"
        state["words"][1] = "CAT"
        state["key"][0] = "Red"
        state["key"][1] = "Red"
        state["turn"] = "Red"
        state["phase"] = "guess"
        state["pending"] = {"clue": "ANIMALS", "number": 2, "made": 0}

        class FakeGuesser:
            def __init__(self):
                self.answers = ["DOG", "CAT"]
                self.index = 0

            def set_move_history(self, *_args):
                return None

            def set_board(self, *_args):
                return None

            def set_clue(self, clue, num_guesses):
                self.clue = clue
                self.num_guesses = num_guesses

            def get_answer(self):
                if self.index >= len(self.answers):
                    return None
                word = self.answers[self.index]
                self.index += 1
                return word

            def keep_guessing(self):
                return self.index < len(self.answers)

        fake = FakeGuesser()
        with unittest.mock.patch.object(_engine._agents, "guesser", return_value=fake):
            _engine.advance(state, None)

        self.assertTrue(state["revealed"][0])
        self.assertFalse(state["revealed"][1])
        self.assertEqual(state["phase"], "guess")
        self.assertEqual(state["turn"], "Red")
        self.assertEqual(state["pending"]["made"], 1)
        self.assertEqual(state["ai_queue"], ["CAT"])
        self.assertEqual(fake.clue, "ANIMALS")
        self.assertEqual(fake.num_guesses, 3)

        with unittest.mock.patch.object(
            _engine._agents,
            "guesser",
            side_effect=AssertionError("plan should be reused"),
        ):
            _engine.advance(state, None)

        self.assertTrue(state["revealed"][1])
        self.assertEqual(state["phase"], "clue")
        self.assertEqual(state["turn"], "Blue")
        self.assertIsNone(state["pending"])

    def test_ai_guesser_miss_ends_turn_after_one_card(self):
        state = _engine.new_game(
            single_team=False,
            seats={"red_cm": "ai", "red_g": "ai", "blue_cm": "ai", "blue_g": "ai"},
            seed=4,
        )
        state["words"][0] = "TABLE"
        state["words"][1] = "DOG"
        state["key"][0] = "Civilian"
        state["key"][1] = "Red"
        state["turn"] = "Red"
        state["phase"] = "guess"
        state["pending"] = {"clue": "ANIMALS", "number": 2, "made": 0}

        class FakeGuesser:
            def set_move_history(self, *_args):
                return None

            def set_board(self, *_args):
                return None

            def set_clue(self, *_args, **_kwargs):
                return None

            def get_answer(self):
                return "TABLE"

            def keep_guessing(self):
                return True

        with unittest.mock.patch.object(
            _engine._agents,
            "guesser",
            return_value=FakeGuesser(),
        ):
            _engine.advance(state, None)

        self.assertTrue(state["revealed"][0])
        self.assertFalse(state["revealed"][1])
        self.assertEqual(state["phase"], "clue")
        self.assertEqual(state["turn"], "Blue")


if __name__ == "__main__":
    unittest.main()
