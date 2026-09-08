"""Summarize Team Edamame JSON or JSONL results without mixing track metrics."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Iterable, Optional


def records_from_path(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        records = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "{}:{} is not valid JSON".format(path, line_number)
                ) from exc
            if isinstance(row, dict):
                records.append(row)
        return records
    if isinstance(payload, dict) and isinstance(payload.get("games"), list):
        return [row for row in payload["games"] if isinstance(row, dict)]
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    raise ValueError("{} does not contain result objects".format(path))


def metric_family(row: dict) -> str:
    track = str(row.get("track") or "").lower()
    metric = str(row.get("metric") or "").lower()
    mode = str(row.get("mode") or "").lower()
    # An explicit track or mode always takes precedence over the metric label,
    # so a two-team row reported with metric "clues" is never filed as single.
    if track == "single" or mode.startswith("single"):
        return "single_team_official_score"
    if track == "two" or mode.startswith("two"):
        return "two_team_red_win"
    if metric in {"clues", "official_score"}:
        return "single_team_official_score"
    if metric in {"red_win", "win"}:
        return "two_team_red_win"
    return "unclassified"


def summarize(records: Iterable[dict]) -> dict:
    rows = list(records)
    groups = {}
    for family in sorted({metric_family(row) for row in rows}):
        subset = [row for row in rows if metric_family(row) == family]
        values = [
            float(row["metric_value"])
            for row in subset
            if row.get("metric_value") is not None
        ]
        groups[family] = {
            "games": len(subset),
            "mean_metric": round(statistics.mean(values), 4) if values else None,
            "wins": sum(
                str(row.get("result") or "").lower() in {"win", "red_win"}
                or row.get("won") is True
                for row in subset
            ),
            "assassins": sum(int(row.get("assassin") or 0) for row in subset),
        }
    latencies = [
        float(row["max_callback_ms"])
        for row in rows
        if row.get("max_callback_ms") is not None
    ]
    return {
        "schema_version": 1,
        "games": len(rows),
        "groups": groups,
        "max_callback_ms": round(max(latencies), 3) if latencies else None,
        "over_60s_games": sum(value > 60_000 for value in latencies),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    rows = []
    for path in args.paths:
        rows.extend(records_from_path(path))
    print(json.dumps(summarize(rows), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
