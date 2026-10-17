"""Rules about the repo itself. They exist because the same mistake happened twice: a test that repeated the manifest version
broke on every release (0168d4b, then 3328cdf). Read the version from the manifest; never type it here."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_test_repeats_the_manifest_version():
    version = json.loads((ROOT / "custom_components/supergrok/manifest.json").read_text(encoding="utf-8"))["version"]
    literal = re.compile(r"""["']""" + re.escape(version) + r"""["']""")
    hits = [
        f"{path.name}:{no}"
        for path in sorted((ROOT / "tests").glob("test_*.py"))
        if path.name != Path(__file__).name
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if literal.search(line)
    ]
    assert not hits, f"these tests type the release version; read it from manifest.json instead: {hits}"
