import functools
import json
import pathlib
import re


_DEFAULT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "skills"
    / "cloud-sync"
    / "references"
    / "secret_patterns.json"
)


@functools.lru_cache(maxsize=4)
def load_patterns(path: str | None = None) -> list[dict]:
    p = pathlib.Path(path) if path else _DEFAULT
    return json.loads(p.read_text(encoding="utf-8"))["patterns"]


def scan_secret(text: str, patterns: list[dict] | None = None) -> list[str]:
    pats = patterns if patterns is not None else load_patterns()
    return [p["name"] for p in pats if re.search(p["regex"], text)]
