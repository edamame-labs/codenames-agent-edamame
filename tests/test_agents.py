import json
import os
import sys
import threading
import time
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "submission", ROOT / "evaluation" / "framework_stub"):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

import codemaster_team as codemaster_module
import guesser_team as guesser_module
from codemaster_team import TeamCodemaster, _api_key as codemaster_api_key
from guesser_team import TeamGuesser, _api_key as guesser_api_key
from players.codemaster import Codemaster
from players.guesser import Guesser


class ScriptedResponses:
    def __init__(self, values=None, by_model=None):
        self.values = list(values or [])
        self.by_model = {
            key: list(queue) for key, queue in dict(by_model or {}).items()
        }
        self.calls = []
        self._lock = threading.Lock()

    def parse(self, **kwargs):
        with self._lock:
            self.calls.append(kwargs)
            model = str(kwargs.get("model") or "")
            queue = self.by_model.get(model)
            value = queue.pop(0) if queue is not None else self.values.pop(0)
        if callable(value):
            value = value()
        if isinstance(value, BaseException):
            raise value
        return SimpleNamespace(output_parsed=value)


class FakeClient:
    def __init__(self, values=None, by_model=None):
        self.responses = ScriptedResponses(values, by_model)


def clue(word, number, targets, first="", second=""):
    return SimpleNamespace(
        clue=word,
        number=number,
        targets=list(targets),
        listener_first=first,
        listener_second=second,
    )


def ranking(*words):
    return SimpleNamespace(ranking=list(words))


def slow(value, seconds=0.20):
    def call():
        time.sleep(seconds)
        return value

    return call


def wait_for_model(client, model, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for call in list(client.responses.calls):
            if call.get("model") == model:
                return call
        time.sleep(0.005)
    raise AssertionError("no call recorded for {}".format(model))


class PublicReleaseTest(unittest.TestCase):
    def test_classes_implement_official_player_interfaces(self):
        self.assertTrue(issubclass(TeamCodemaster, Codemaster))
        self.assertTrue(issubclass(TeamGuesser, Guesser))

    def test_credentials_come_only_from_environment(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(codemaster_api_key(), "")
            self.assertEqual(guesser_api_key(), "")
        with unittest.mock.patch.dict(
            os.environ, {"OPENAI_API_KEY": "test-placeholder"}, clear=True
        ):
            self.assertEqual(codemaster_api_key(), "test-placeholder")
            self.assertEqual(guesser_api_key(), "test-placeholder")

    def test_offline_codemaster_returns_legal_one_never_zero(self):
        with unittest.mock.patch.dict(os.environ, {"V0_OFFLINE": "1"}, clear=False):
            agent = TeamCodemaster("Red")
            agent.set_game_state(
                ["DOG", "MOON", "WAR"], ["Red", "Blue", "Assassin"]
            )
            word, number = agent.get_clue()
        self.assertRegex(word, r"^[A-Za-z]+$")
        self.assertEqual(number, 1)

    def test_published_timeout_budget_is_preserved(self):
        self.assertEqual(
            (
                codemaster_module.PRIMARY_TIMEOUT,
                codemaster_module.BACKUP_TIMEOUT,
                codemaster_module.TURN_DEADLINE,
            ),
            (45.0, 15.0, 50.0),
        )
        self.assertEqual(
            (
                guesser_module.GUESSER_TIMEOUT,
                guesser_module.BACKUP_TIMEOUT,
                guesser_module.TURN_DEADLINE,
            ),
            (45.0, 15.0, 50.0),
        )


class CodemasterPolicyTest(unittest.TestCase):
    def _agent(self, primary, backup):
        client = FakeClient(
            by_model={
                "gpt-5.6-sol": [primary],
                "gpt-5.6-terra": [backup],
            }
        )
        agent = TeamCodemaster("Red", client=client)
        agent.set_game_state(
            ["DOG", "CAT", "MOON", "WAR"],
            ["Red", "Red", "Blue", "Assassin"],
        )
        return agent, client

    def test_clean_listener_path_preserves_pair(self):
        agent, _ = self._agent(
            clue("PETS", 2, ["DOG", "CAT"], "DOG", "CAT"),
            clue("ANIMALS", 1, ["DOG"], "DOG", "MOON"),
        )
        self.assertEqual(agent.get_clue(), ("PETS", 2))

    def test_dirty_second_guess_clamps_pair_to_one(self):
        agent, _ = self._agent(
            clue("PETS", 2, ["DOG", "CAT"], "DOG", "WAR"),
            TimeoutError("unused"),
        )
        self.assertEqual(agent.get_clue(), ("PETS", 1))
        self.assertEqual(agent.harness_events[-1]["reason"], "v0_clamped")

    def test_dangerous_first_guess_rejects_primary(self):
        agent, _ = self._agent(
            clue("PETS", 2, ["DOG", "CAT"], "WAR", "DOG"),
            clue("CANINE", 1, ["DOG"], "DOG", "MOON"),
        )
        self.assertEqual(agent.get_clue(), ("CANINE", 1))
        self.assertEqual(
            agent.harness_events[-1]["reason"],
            "v0_backup_after_v0_assassin_first",
        )

    def test_invalid_target_rejects_primary(self):
        agent, _ = self._agent(
            clue("SPACE", 1, ["MOON"], "MOON", "DOG"),
            clue("CANINE", 1, ["DOG"], "DOG", "MOON"),
        )
        self.assertEqual(agent.get_clue(), ("CANINE", 1))

    def test_malformed_or_illegal_primary_uses_backup(self):
        invalid_responses = (
            None,
            clue("DOGS", 1, ["CAT"], "CAT"),
            clue("PETS", 3, ["DOG", "CAT"], "DOG", "CAT"),
        )
        for invalid in invalid_responses:
            with self.subTest(invalid=invalid):
                agent, _ = self._agent(
                    invalid,
                    clue("CANINE", 1, ["DOG"], "DOG"),
                )
                self.assertEqual(agent.get_clue(), ("CANINE", 1))
                self.assertEqual(
                    agent.harness_events[-1]["reason"],
                    "v0_backup_after_malformed_response",
                )

    def test_api_failure_uses_parallel_backup(self):
        agent, _ = self._agent(
            TimeoutError("primary"),
            clue("ANIMALS", 2, ["DOG", "CAT"], "DOG", "CAT"),
        )
        self.assertEqual(agent.get_clue(), ("ANIMALS", 2))
        self.assertEqual(
            agent.harness_events[-1]["reason"], "v0_backup_after_api_error"
        )

    def test_hard_deadline_uses_ready_backup_without_joining_primary(self):
        agent, _ = self._agent(
            slow(clue("SLOW", 2, ["DOG", "CAT"], "DOG", "CAT")),
            clue("ANIMALS", 2, ["DOG", "CAT"], "DOG", "CAT"),
        )
        agent.turn_deadline = 0.03
        started = time.monotonic()
        self.assertEqual(agent.get_clue(), ("ANIMALS", 2))
        self.assertLess(time.monotonic() - started, 0.12)
        self.assertEqual(
            agent.harness_events[-1]["reason"],
            "v0_backup_after_turn_deadline",
        )

    def test_both_hanging_calls_return_local_fallback_at_deadline(self):
        agent, _ = self._agent(
            slow(clue("SLOW", 2, ["DOG", "CAT"], "DOG", "CAT")),
            slow(clue("LATE", 2, ["DOG", "CAT"], "DOG", "CAT")),
        )
        agent.turn_deadline = 0.03
        started = time.monotonic()
        word, number = agent.get_clue()
        self.assertLess(time.monotonic() - started, 0.12)
        self.assertRegex(word, r"^[A-Za-z]+$")
        self.assertEqual(number, 1)

    def test_unverified_second_forecast_clamps_pair(self):
        for second in ("", "NOPE"):
            with self.subTest(second=second):
                agent, _ = self._agent(
                    clue("PETS", 2, ["DOG", "CAT"], "DOG", second),
                    TimeoutError("unused"),
                )
                self.assertEqual(agent.get_clue(), ("PETS", 1))
                self.assertEqual(
                    agent.harness_events[-1]["reason"], "v0_clamped"
                )

    def test_unverified_first_forecast_rejects_primary(self):
        for first in ("", "NOPE"):
            with self.subTest(first=first):
                agent, _ = self._agent(
                    clue("PETS", 2, ["DOG", "CAT"], first, "CAT"),
                    clue("CANINE", 1, ["DOG"], "DOG", "MOON"),
                )
                self.assertEqual(agent.get_clue(), ("CANINE", 1))
                self.assertEqual(
                    agent.harness_events[-1]["reason"],
                    "v0_backup_after_v0_non_own_first",
                )

    def test_exhausted_emergency_pool_returns_legal_clue(self):
        with unittest.mock.patch.dict(
            os.environ, {"V0_OFFLINE": "1"}, clear=False
        ):
            agent = TeamCodemaster("Red")
            agent.set_game_state(
                ["DOG", "MOON", "WAR"], ["Red", "Blue", "Assassin"]
            )
            agent.set_move_history(
                [
                    ["Red_Codemaster", used, 1]
                    for used in codemaster_module.EMERGENCY_CLUES
                ]
            )
            word, number = agent.get_clue()
        self.assertEqual(number, 1)
        self.assertTrue(
            codemaster_module.is_legal_clue(
                word,
                ["DOG", "MOON", "WAR"],
                list(codemaster_module.EMERGENCY_CLUES),
            )
        )


class GuesserPolicyTest(unittest.TestCase):
    def _agent(self, primary, backup, number=2):
        client = FakeClient(
            by_model={
                "gpt-5.6-sol": [primary],
                "gpt-5.6-terra": [backup],
            }
        )
        agent = TeamGuesser("Red", client=client)
        agent.set_board(["DOG", "CAT", "MOON"])
        agent.set_clue("PETS", number)
        return agent, client

    def test_exact_n_without_plus_one(self):
        agent, _ = self._agent(ranking("DOG", "CAT"), ranking("MOON"))
        self.assertEqual(agent.get_answer(), "DOG")
        self.assertTrue(agent.keep_guessing())
        self.assertEqual(agent.get_answer(), "CAT")
        self.assertFalse(agent.keep_guessing())

    def test_zero_from_foreign_codemaster_means_one_not_unlimited(self):
        agent, _ = self._agent(ranking("DOG"), ranking("MOON"), number=0)
        self.assertEqual(agent.get_answer(), "DOG")
        self.assertFalse(agent.keep_guessing())

    def test_backup_is_capped_at_one_guess(self):
        agent, client = self._agent(TimeoutError("primary"), ranking("MOON"))
        self.assertEqual(agent.get_answer(), "MOON")
        self.assertFalse(agent.keep_guessing())
        primary = json.loads(wait_for_model(client, "gpt-5.6-sol")["input"])
        backup = json.loads(wait_for_model(client, "gpt-5.6-terra")["input"])
        self.assertEqual(primary["return_exactly_this_many_words"], 2)
        self.assertEqual(backup["return_exactly_this_many_words"], 1)

    def test_unfinished_previous_clues_are_in_prompt_state(self):
        agent, client = self._agent(ranking("DOG", "CAT"), ranking("MOON"))
        agent.set_move_history(
            [
                ["Red_Codemaster", "RAILWAY", 2],
                ["Red_Guesser", "TRACK", "*Red*", True],
                ["Red_Guesser", "PASS", "*Civilian*", False],
                ["Red_Codemaster", "PETS", 2],
            ]
        )
        self.assertEqual(agent.get_answer(), "DOG")
        primary = json.loads(wait_for_model(client, "gpt-5.6-sol")["input"])
        self.assertEqual(
            primary["unfinished_previous_clues"][0]["clue"], "RAILWAY"
        )

    def test_hard_deadline_uses_one_word_backup(self):
        agent, _ = self._agent(slow(ranking("DOG", "CAT")), ranking("MOON"))
        agent.turn_deadline = 0.03
        started = time.monotonic()
        self.assertEqual(agent.get_answer(), "MOON")
        self.assertLess(time.monotonic() - started, 0.12)
        self.assertFalse(agent.keep_guessing())

    def test_plan_exhausted_midturn_stops_without_unplanned_guess(self):
        agent, _ = self._agent(ranking("DOG", "CAT"), ranking("MOON"))
        self.assertEqual(agent.get_answer(), "DOG")
        # The remaining planned word becomes unavailable mid-turn.
        agent._guessed = ["DOG", "CAT"]
        self.assertIsNone(agent.get_answer())
        self.assertEqual(agent._made, 1)
        self.assertFalse(agent.keep_guessing())


if __name__ == "__main__":
    unittest.main()
