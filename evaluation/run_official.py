"""Run the public Team Edamame submission against the official CoG framework.

This runner imports the unmodified official ``Game`` class, loads the two
published player files directly, and emits one JSON record per game. It never
copies or records API credentials, prompts, raw responses, or board keys.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable, Optional


ROOT = Path(__file__).resolve().parents[1]
RESPONSE_LIMIT_MS = 60_000.0


def load_env(path: Path) -> None:
    """Load simple KEY=value entries without overriding exported variables."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def framework_root(path: Path) -> Path:
    """Resolve either a Codenames_GPT checkout or its codenames directory."""

    path = path.expanduser().resolve()
    candidates = (path, path / "codenames")
    for candidate in candidates:
        if (
            (candidate / "game.py").is_file()
            and (candidate / "players" / "codemaster.py").is_file()
            and (candidate / "players" / "guesser.py").is_file()
        ):
            return candidate
    raise SystemExit(
        "--framework must point to Codenames_GPT or its codenames directory"
    )


def load_file_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load {}".format(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_class(reference: str):
    """Load ``package.module:Class`` or ``package.module.Class``."""

    module_name, separator, class_name = reference.partition(":")
    if not separator:
        module_name, separator, class_name = reference.rpartition(".")
    if not module_name or not class_name:
        raise ValueError("class reference must be package.module:Class")
    return getattr(importlib.import_module(module_name), class_name)


def timed_player(player_class, method_name: str, role: str, timings: list[dict]):
    """Wrap one public callback without changing the underlying implementation."""

    original = getattr(player_class, method_name)

    def measured(self, *args, **kwargs):
        started = time.monotonic()
        try:
            return original(self, *args, **kwargs)
        finally:
            timings.append(
                {
                    "role": role,
                    "team": getattr(self, "team", None),
                    "latency_ms": round(
                        (time.monotonic() - started) * 1000.0, 3
                    ),
                }
            )

    return type(
        "Timed{}".format(player_class.__name__),
        (player_class,),
        {method_name: measured, "__module__": __name__},
    )


def revealed_count(words: Iterable[str], label: str) -> int:
    marker = "*{}*".format(label).upper()
    return sum(str(word).upper() == marker for word in words)


def run_game(
    Game,
    *,
    seed: int,
    track: str,
    pairing_id: str,
    red_cm,
    red_g,
    blue_cm,
    blue_g,
) -> dict:
    timings: list[dict] = []
    RedCM = timed_player(red_cm, "get_clue", "codemaster", timings)
    RedG = timed_player(red_g, "get_answer", "guesser", timings)
    BlueCM = timed_player(blue_cm, "get_clue", "codemaster", timings)
    BlueG = timed_player(blue_g, "get_answer", "guesser", timings)
    started = time.monotonic()
    game = Game(
        RedCM,
        RedG,
        BlueCM,
        BlueG,
        seed=seed,
        do_print=False,
        do_log=False,
        single_team=track == "single",
    )
    game.run()
    elapsed = time.monotonic() - started
    history = list(game.get_move_history() or [])
    words = list(game.get_words_on_board() or [])
    red_turns = sum(
        bool(move) and move[0] == "Red_Codemaster" for move in history
    )
    assassin = revealed_count(words, "Assassin")
    won = game.game_winner == "R"
    metric = red_turns if track == "single" and won else 25 if track == "single" else int(won)
    latencies = [float(row["latency_ms"]) for row in timings]
    return {
        "schema_version": 1,
        "seed": seed,
        "track": track,
        "pairing_id": pairing_id,
        "winner": game.game_winner,
        "won": won,
        "metric": "official_score" if track == "single" else "red_win",
        "metric_value": metric,
        "red_clues": red_turns,
        "assassin": assassin,
        "revealed": {
            "red": revealed_count(words, "Red"),
            "blue": revealed_count(words, "Blue"),
            "civilian": revealed_count(words, "Civilian"),
            "assassin": assassin,
        },
        "callbacks": len(timings),
        "max_callback_ms": round(max(latencies), 3) if latencies else None,
        "over_60s_callbacks": sum(value > RESPONSE_LIMIT_MS for value in latencies),
        "elapsed_seconds": round(elapsed, 3),
    }


def pairings(args, candidate_cm, candidate_g):
    if args.mode == "single-self":
        return [
            (
                "single",
                "self",
                candidate_cm,
                candidate_g,
                candidate_cm,
                candidate_g,
            )
        ]
    if args.mode == "two-self":
        return [
            (
                "two",
                "self",
                candidate_cm,
                candidate_g,
                candidate_cm,
                candidate_g,
            )
        ]
    if args.mode == "single-cross":
        if not args.foreign_codemaster or not args.foreign_guesser:
            raise SystemExit(
                "single-cross requires --foreign-codemaster and --foreign-guesser"
            )
        foreign_cm = load_class(args.foreign_codemaster)
        foreign_g = load_class(args.foreign_guesser)
        return [
            (
                "single",
                "candidate-cm__foreign-guesser",
                candidate_cm,
                foreign_g,
                candidate_cm,
                candidate_g,
            ),
            (
                "single",
                "foreign-cm__candidate-guesser",
                foreign_cm,
                candidate_g,
                candidate_cm,
                candidate_g,
            ),
        ]
    if not args.opponent_codemaster or not args.opponent_guesser:
        raise SystemExit(
            "two-vs requires --opponent-codemaster and --opponent-guesser"
        )
    return [
        (
            "two",
            "candidate-red__opponent-blue",
            candidate_cm,
            candidate_g,
            load_class(args.opponent_codemaster),
            load_class(args.opponent_guesser),
        )
    ]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--framework",
        required=True,
        help="Path to the official Codenames_GPT checkout or codenames folder.",
    )
    parser.add_argument(
        "--mode",
        choices=("single-self", "single-cross", "two-self", "two-vs"),
        default="single-self",
    )
    parser.add_argument("--seed", type=int, action="append")
    parser.add_argument("--foreign-codemaster")
    parser.add_argument("--foreign-guesser")
    parser.add_argument("--opponent-codemaster")
    parser.add_argument("--opponent-guesser")
    parser.add_argument(
        "--extra-pythonpath",
        action="append",
        default=[],
        help="Additional import root for foreign/opponent classes.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Force deterministic legal fallbacks; makes no API calls.",
    )
    parser.add_argument(
        "--env-file",
        default=str(ROOT / ".env"),
        help="Optional local env file; exported values take precedence.",
    )
    parser.add_argument("--output", help="Optional JSONL output path.")
    args = parser.parse_args(argv)

    load_env(Path(args.env_file))
    if args.offline:
        os.environ["V0_OFFLINE"] = "1"
    elif not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "OPENAI_API_KEY is not set. Export it, use a gitignored .env, "
            "or pass --offline."
        )

    output_path = (
        Path(args.output).expanduser().resolve() if args.output else None
    )

    official_root = framework_root(Path(args.framework))
    for path in [official_root, *(Path(item).expanduser().resolve() for item in args.extra_pythonpath)]:
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)

    os.chdir(official_root)
    from game import Game

    cm_module = load_file_module(
        "edamame_submission_codemaster", ROOT / "submission" / "codemaster_team.py"
    )
    g_module = load_file_module(
        "edamame_submission_guesser", ROOT / "submission" / "guesser_team.py"
    )
    candidate_cm = cm_module.TeamCodemaster
    candidate_g = g_module.TeamGuesser

    records = []
    for seed in args.seed or [3442]:
        for track, pairing_id, red_cm, red_g, blue_cm, blue_g in pairings(
            args, candidate_cm, candidate_g
        ):
            record = run_game(
                Game,
                seed=seed,
                track=track,
                pairing_id=pairing_id,
                red_cm=red_cm,
                red_g=red_g,
                blue_cm=blue_cm,
                blue_g=blue_g,
            )
            records.append(record)
            print(json.dumps(record, sort_keys=True), flush=True)

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
            encoding="utf-8",
        )
    return 2 if any(record["over_60s_callbacks"] for record in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
