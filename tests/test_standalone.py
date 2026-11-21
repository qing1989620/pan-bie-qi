import json

from fastapi.testclient import TestClient

from skill_discriminator.adoption import CATALOG
from skill_discriminator.api import create_app


def test_packaged_browser_assets_and_real_evidence():
    with TestClient(create_app()) as client:
        assert "开源 Skill 判别器" in client.get("/").text
        for name in ("style.css", "app.js"):
            assert client.get(f"/static/{name}").status_code == 200
        studies = client.get("/api/discriminator/adoption-studies").json()
        assert studies["utility_study"]["signed"]["rows"] == 1185
        evidence = client.get("/api/discriminator/evidence/recheck").json()
        assert (evidence["correct"], evidence["artifacts"]) == (64, 64)
        assert evidence["passed"] == {"no_skill": 15, "skill": 16}
        assert evidence["holdout"] == {"pairs": 16, "passed": {"no_skill": 8, "skill": 8}}


def test_tampered_evidence_returns_explicit_unavailability(monkeypatch, tmp_path):
    from skill_discriminator import verify

    for name in ("codebook-cases.json", "codebook-outcomes.json", "codebook-ablation.json"):
        (tmp_path / name).write_bytes((CATALOG.parent / name).read_bytes())
    changed = tmp_path / "codebook-outcomes.json"
    rows = json.loads(changed.read_bytes())
    rows.pop()
    changed.write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr(verify, "CATALOG", tmp_path / CATALOG.name)
    with TestClient(create_app()) as client:
        assert client.get("/api/discriminator/evidence/recheck").status_code == 503
