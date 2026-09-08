import json
import tempfile
import unittest
from pathlib import Path

from evaluation.summarize import metric_family, records_from_path, summarize


ROOT = Path(__file__).resolve().parents[1]


class EvaluationSummaryTest(unittest.TestCase):
    def test_track_metrics_are_kept_separate(self):
        rows = [
            {
                "track": "single",
                "won": True,
                "metric": "official_score",
                "metric_value": 6,
                "assassin": 0,
                "max_callback_ms": 21_300,
            },
            {
                "track": "two",
                "won": True,
                "metric": "red_win",
                "metric_value": 1,
                "assassin": 0,
                "max_callback_ms": 45_200,
            },
        ]
        result = summarize(rows)
        self.assertEqual(result["games"], 2)
        self.assertEqual(
            result["groups"]["single_team_official_score"]["mean_metric"], 6.0
        )
        self.assertEqual(
            result["groups"]["two_team_red_win"]["mean_metric"], 1.0
        )
        self.assertEqual(result["max_callback_ms"], 45_200.0)
        self.assertEqual(result["over_60s_games"], 0)

    def test_historical_wrapper_and_jsonl_are_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapped = root / "wrapped.json"
            wrapped.write_text(
                json.dumps(
                    {
                        "games": [
                            {
                                "mode": "Single-Team / self",
                                "metric": "clues",
                                "metric_value": 6,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            lines = root / "games.jsonl"
            lines.write_text(
                json.dumps(
                    {
                        "track": "two",
                        "metric": "red_win",
                        "metric_value": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(len(records_from_path(wrapped)), 1)
            self.assertEqual(len(records_from_path(lines)), 1)
            self.assertEqual(
                metric_family(records_from_path(lines)[0]),
                "two_team_red_win",
            )

    def test_preserved_smoke_evidence_matches_documented_aggregate(self):
        records = records_from_path(
            ROOT / "evaluation" / "results" / "v0-smoke.json"
        )
        result = summarize(records)
        self.assertEqual(result["games"], 4)
        self.assertEqual(result["max_callback_ms"], 45_200.0)
        self.assertEqual(result["over_60s_games"], 0)
        self.assertEqual(
            result["groups"]["single_team_official_score"],
            {
                "games": 2,
                "mean_metric": 7.5,
                "wins": 2,
                "assassins": 0,
            },
        )
        self.assertEqual(
            result["groups"]["two_team_red_win"],
            {
                "games": 2,
                "mean_metric": 1.0,
                "wins": 2,
                "assassins": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
