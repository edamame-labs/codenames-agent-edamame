import unittest
import unittest.mock

from evaluation import run_official


class DummyCodemaster:
    def __init__(self, team):
        self.team = team

    def get_clue(self):
        return "SAFE", 1


class DummyGuesser:
    def __init__(self, team):
        self.team = team

    def get_answer(self):
        return "DOG"


class FakeGame:
    def __init__(
        self,
        red_codemaster,
        red_guesser,
        blue_codemaster,
        blue_guesser,
        **kwargs
    ):
        del blue_codemaster, blue_guesser, kwargs
        self.red_codemaster = red_codemaster
        self.red_guesser = red_guesser
        self.game_winner = "R"

    def run(self):
        self.red_codemaster("Red").get_clue()
        self.red_guesser("Red").get_answer()

    def get_move_history(self):
        return [["Red_Codemaster", "SAFE", 1]]

    def get_words_on_board(self):
        return ["*RED*", "*BLUE*", "DOG"]


class OfficialRunnerTest(unittest.TestCase):
    def _run(self):
        return run_official.run_game(
            FakeGame,
            seed=3442,
            track="single",
            pairing_id="self",
            red_cm=DummyCodemaster,
            red_g=DummyGuesser,
            blue_cm=DummyCodemaster,
            blue_g=DummyGuesser,
        )

    def test_runner_emits_track_aware_public_metrics(self):
        record = self._run()
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["metric"], "official_score")
        self.assertEqual(record["metric_value"], 1)
        self.assertEqual(record["callbacks"], 2)
        self.assertEqual(record["assassin"], 0)
        self.assertEqual(record["over_60s_callbacks"], 0)

    def test_response_limit_breach_is_counted(self):
        with unittest.mock.patch.object(run_official, "RESPONSE_LIMIT_MS", -1):
            record = self._run()
        self.assertEqual(record["over_60s_callbacks"], 2)


if __name__ == "__main__":
    unittest.main()
