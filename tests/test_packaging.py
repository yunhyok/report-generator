import json

import pytest

from scripts import build_release


def test_installer_uses_program_files_and_shows_destination_page():
    installer = (build_release.ROOT / "packaging/installer.iss").read_text(encoding="utf-8")
    assert "DefaultDirName={autopf}\\{#AppName}" in installer
    assert "DisableDirPage=no" in installer
    assert "PrivilegesRequired=admin" in installer


def test_release_version_is_synchronized():
    import tomllib

    version = tomllib.loads((build_release.ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert f'__version__ = "{version}"' in (build_release.ROOT / "src/report_generator/__init__.py").read_text(encoding="utf-8")
    assert f"v{version}" in (build_release.ROOT / "src/report_generator/docs/help.html").read_text(encoding="utf-8")


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
