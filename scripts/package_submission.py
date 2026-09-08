"""Build a deterministic, credential-free drop-in submission archive."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
MEMBERS = (
    (ROOT / "submission" / "codemaster_team.py", "codemaster_team.py"),
    (ROOT / "submission" / "guesser_team.py", "guesser_team.py"),
    (ROOT / "submission" / "README.md", "README.md"),
    (ROOT / "submission" / "requirements.txt", "requirements.txt"),
    (ROOT / "LICENSE", "LICENSE"),
    (ROOT / "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
)


def build(output: Path) -> Path:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, archive_name in MEMBERS:
            info = zipfile.ZipInfo(archive_name, date_time=(2026, 8, 19, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())
    return output


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / "edamame-cog-2026-submission.zip",
    )
    args = parser.parse_args(argv)
    print(build(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
