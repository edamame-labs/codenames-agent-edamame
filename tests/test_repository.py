import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIKELY_OPENAI_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
IGNORED_PARTS = {".git", ".venv", ".agents", "build", "dist", "__pycache__"}
DIAGRAM_SVGS = (
    "site/diagrams/core.svg",
    "site/diagrams/clock.svg",
    "site/diagrams/forecast-gate.svg",
    "site/diagrams/sim-gate.svg",
)
TEXT_SUFFIXES = {
    "",
    ".css",
    ".html",
    ".js",
    ".json",
    ".lock",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".tsx",
    ".yaml",
    ".yml",
}


class RepositoryHygieneTest(unittest.TestCase):
    def test_no_likely_openai_secret_is_present(self):
        findings = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or IGNORED_PARTS.intersection(path.parts):
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if LIKELY_OPENAI_SECRET.search(text):
                findings.append(str(path.relative_to(ROOT)))
        self.assertEqual(findings, [])

    def test_real_env_files_are_not_present(self):
        env_files = [
            str(path.relative_to(ROOT))
            for path in ROOT.rglob(".env*")
            if path.name != ".env.example"
            and not IGNORED_PARTS.intersection(path.parts)
        ]
        self.assertEqual(env_files, [])

    def test_removed_svg_assets_are_not_referenced(self):
        markdown = [ROOT / "README.md", *(ROOT / "docs").rglob("*.md")]
        allowed = tuple(f"]({name})" for name in DIAGRAM_SVGS) + tuple(
            f"](../{name})" for name in DIAGRAM_SVGS
        )
        stray = []
        for path in markdown:
            text = path.read_text(encoding="utf-8")
            if ".svg" not in text:
                continue
            leftover = text
            for token in allowed:
                leftover = leftover.replace(token, "")
            if ".svg" in leftover:
                stray.append(str(path.relative_to(ROOT)))
        self.assertEqual(stray, [])
        self.assertEqual(list((ROOT / "docs").rglob("*.svg")), [])
        for name in DIAGRAM_SVGS:
            self.assertTrue((ROOT / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
