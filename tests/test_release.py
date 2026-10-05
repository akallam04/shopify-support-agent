from app.config import Settings
from app.release import release_manifest


def test_the_release_id_changes_with_models_and_gate_settings(monkeypatch) -> None:
    monkeypatch.setenv("GIT_SHA", "abc123def456")
    base = release_manifest(Settings(_env_file=None))
    assert base["git_sha"] == "abc123def456"
    assert len(base["release"]) == 12 and len(base["prompts"]) == 12 and len(base["knowledge"]) == 12
    assert release_manifest(Settings(_env_file=None)) == base
    assert release_manifest(Settings(_env_file=None, answer_model="claude-sonnet-5-5"))["release"] != base["release"]
    assert release_manifest(Settings(_env_file=None, gate_reflection=True))["release"] != base["release"]
