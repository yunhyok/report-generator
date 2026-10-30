import json

import pytest

from scripts import build_release


def test_explicit_compiler_path_does_not_need_external_translation(tmp_path, monkeypatch):
    compiler = tmp_path / "Inno Setup" / "ISCC.exe"
    compiler.parent.mkdir()
    compiler.touch()
    monkeypatch.setenv("ISCC", str(compiler))
    assert build_release.find_iscc() == compiler.resolve()
    compiler.unlink()
    with pytest.raises(FileNotFoundError, match="missing"):
        build_release.find_iscc()


def test_installer_rejects_mismatched_payload_before_compiling(tmp_path, monkeypatch):
    monkeypatch.setattr(build_release, "ROOT", tmp_path)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "SPD Report Generator.exe").touch()
    (payload / "BUILD_MANIFEST.json").write_text(json.dumps({"version": "0.1.0"}))
    monkeypatch.setattr(build_release.subprocess, "run", lambda *a, **k: pytest.fail("Compiler must not run"))
    with pytest.raises(ValueError, match="versions do not match"):
        build_release.compile_installer(payload, tmp_path / "stage", "0.1.1")
    assert not (tmp_path / "stage").exists()
