import json
import sys
from pathlib import Path

import pytest

HUB = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HUB / "orchestrate"))
FAKE = Path(__file__).with_name("fake_seat.py")


@pytest.fixture(autouse=True)
def settings_root(tmp_path, monkeypatch):
    root = tmp_path / "settings"
    monkeypatch.setenv("TIKITAKA_SETTINGS_ROOT", str(root))
    return root


def role(seat, **kw):
    return {"seat": seat, "cli": "fake", "script": str(FAKE), **kw}


@pytest.fixture
def job_raw(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec\nThe artifact must state FUNCTION-ALPHA.\n", encoding="utf-8")
    return {
        "name": "t", "spec": str(spec), "brief": "write the artifact", "artifact_name": "a.md",
        "planned_functions": ["states FUNCTION-ALPHA"], "max_iterations": 3,
        "roles": {"orchestrator": {"seat": "orch", "cli": "internal"}, "builder": role("bld"),
                  "accuracy_reviewer": role("acc"), "cold_reviewer": role("cold"), "judge": role("jdg"),
                  "qa_qc": role("qa"), "auditor": role("aud")},
    }


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    def make(**kw):
        p = tmp_path / "scenario.json"
        p.write_text(json.dumps(kw), encoding="utf-8")
        monkeypatch.setenv("TIKITAKA_FAKE_SCENARIO", str(p))
        return p
    return make
