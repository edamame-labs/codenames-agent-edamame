import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.package_submission import build


ROOT = Path(__file__).resolve().parents[1]
LIKELY_OPENAI_SECRET = re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")


class PackageTest(unittest.TestCase):
    def test_public_archive_is_minimal_deterministic_and_secret_free(self):
        with tempfile.TemporaryDirectory() as directory:
            first = build(Path(directory) / "first.zip")
            second = build(Path(directory) / "second.zip")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(
                    archive.namelist(),
                    [
                        "codemaster_team.py",
                        "guesser_team.py",
                        "README.md",
                        "requirements.txt",
                        "LICENSE",
                        "THIRD_PARTY_NOTICES.md",
                    ],
                )
                dependency_block = re.search(
                    r"dependencies\s*=\s*\[(.*?)\]",
                    (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
                    re.DOTALL,
                )
                self.assertIsNotNone(dependency_block)
                project_requirements = re.findall(
                    r'"([^"]+)"', dependency_block.group(1)
                )
                archive_requirements = (
                    archive.read("requirements.txt").decode("utf-8").splitlines()
                )
                self.assertEqual(archive_requirements, project_requirements)
                for name in archive.namelist():
                    self.assertIsNone(
                        LIKELY_OPENAI_SECRET.search(archive.read(name)),
                        name,
                    )


if __name__ == "__main__":
    unittest.main()
