"""Build, self-test and package the portable Windows release without installing packages.

Prepare an isolated environment first:
  py -3.12 -m venv .venv-build
  .venv-build\\Scripts\\python -m pip install -r requirements-build.txt
  .venv-build\\Scripts\\python -m pytest
  .venv-build\\Scripts\\python scripts/build_release.py
Use --check for read-only preflight. Build stages and previous releases are retained
under build/ for inspection; this script never recursively deletes directories.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "SPD Report Generator"
RUNTIME_PACKAGES = (
    "PySide6_Essentials", "shiboken6", "numpy", "Pillow", "matplotlib", "openpyxl",
    "et_xmlfile", "contourpy", "cycler", "fonttools", "kiwisolver", "packaging",
    "pyparsing", "python-dateutil", "six", "PyInstaller",
)


def inside_workspace(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT) or resolved == ROOT:
        raise ValueError(f"Build path must be inside the project workspace: {path}")
    return resolved


def preflight() -> tuple[str, dict[str, str]]:
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"} or sys.maxsize <= 2**32:
        raise RuntimeError("The portable release must be built with Windows x64 Python.")
    if sys.version_info[:3] != (3, 12, 10):
        raise RuntimeError("Use CPython 3.12.10 to reproduce this release.")
    versions = {}
    for line in (ROOT / "requirements-build.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, expected = line.split("==")
        actual = metadata.version(name)
        if actual != expected:
            raise RuntimeError(f"{name}: expected {expected}, found {actual}. Prepare the isolated build environment.")
        versions[name] = actual
    required = (
        "LICENSE", "packaging/report-generator.spec", "src/report_generator/docs/help.html",
        "src/report_generator/docs/THIRD_PARTY_NOTICES.txt", "src/report_generator/selftest.py",
        "packaging/licenses/Qt/LGPL-3.0-only.txt", "packaging/licenses/Qt/GPL-3.0-only.txt",
        "packaging/licenses/openpyxl/LICENSE.txt", "packaging/licenses/et-xmlfile/LICENSE.txt",
    )
    for name in required:
        if not (ROOT / name).is_file():
            raise FileNotFoundError(f"Required release input is missing: {name}")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise FileNotFoundError("The installed CPython LICENSE.txt is required.")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not version or any(character not in "0123456789.abrc-" for character in version):
        raise ValueError("Unexpected release version.")
    return version, versions


def copy_licenses(destination: Path) -> None:
    destination = inside_workspace(destination)
    shutil.copytree(ROOT / "packaging/licenses", destination, dirs_exist_ok=True)
    python_dir = inside_workspace(destination / "Python")
    python_dir.mkdir()
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", python_dir / "LICENSE.txt")
    for name in RUNTIME_PACKAGES:
        distribution = metadata.distribution(name)
        count = 0
        for relative in distribution.files or []:
            normalized = str(relative).replace("\\", "/")
            basename = Path(normalized).name.lower()
            if not (".dist-info/licenses/" in normalized or
                    basename.startswith(("license", "licence", "copying"))):
                continue
            if Path(normalized).suffix.lower() in {".py", ".pyc", ".pyd"}:
                continue
            source = Path(distribution.locate_file(relative))
            if not source.is_file():
                continue
            target = inside_workspace(destination / name / normalized)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            count += 1
        if count == 0 and name not in {"openpyxl", "et_xmlfile"}:
            raise RuntimeError(f"No installed license files were found for {name}.")


def build() -> Path:
    version, versions = preflight()
    build_root = inside_workspace(ROOT / "build")
    release_root = inside_workspace(ROOT / "dist")
    build_root.mkdir(exist_ok=True)
    release_root.mkdir(exist_ok=True)
    stage = inside_workspace(Path(tempfile.mkdtemp(prefix="release-", dir=build_root)))
    environment = os.environ.copy()
    environment["MPLBACKEND"] = "Agg"
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTHONHASHSEED"] = "0"
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["MPLCONFIGDIR"] = str(stage / "matplotlib-cache")
    # Avoid dependency discovery from unrelated software's DLL directories.
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    environment["PATH"] = os.pathsep.join(str(p) for p in (
        Path(sys.prefix), Path(sys.prefix) / "Scripts", Path(sys.base_prefix),
        Path(sys.base_prefix) / "DLLs", system_root / "System32", system_root,
    ))
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(stage / "dist"), "--workpath", str(stage / "work"),
        str(ROOT / "packaging/report-generator.spec"),
    ], cwd=ROOT, env=environment, check=True)
    portable = inside_workspace(stage / "dist" / APP_NAME)
    executable = portable / f"{APP_NAME}.exe"
    if not executable.is_file():
        raise RuntimeError("PyInstaller did not create the portable executable.")
    copy_licenses(portable / "licenses")
    for source, filename in ((ROOT / "LICENSE", "LICENSE"),
                             (ROOT / "src/report_generator/docs/THIRD_PARTY_NOTICES.txt", "THIRD_PARTY_NOTICES.txt"),
                             (ROOT / "src/report_generator/docs/help.html", "help.html")):
        shutil.copy2(source, portable / filename)
    (portable / "BUILD_MANIFEST.json").write_text(json.dumps({
        "application": APP_NAME, "version": version, "python": platform.python_version(),
        "platform": "windows-x64", "packages": versions,
        "qt_modules": ["QtCore", "QtGui", "QtWidgets"], "matplotlib_backend": "Agg",
    }, indent=2) + "\n", encoding="utf-8")
    smoke = inside_workspace(stage / "self-test")
    # QApplication and the multiprocessing worker both run inside the frozen app.
    subprocess.run([str(executable), "--self-test", str(smoke)], cwd=portable,
                   env=environment, check=True, timeout=180)
    archive_name = f"SPD-Report-Generator-v{version}-windows-x64.zip"
    staged_zip = inside_workspace(stage / archive_name)
    with zipfile.ZipFile(staged_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(portable.rglob("*")):
            if path.is_symlink():
                raise ValueError("Portable artifacts must not contain symbolic links.")
            if path.is_file():
                archive.write(path, Path(APP_NAME) / path.relative_to(portable))
    with staged_zip.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    checksum = inside_workspace(stage / f"{archive_name}.sha256")
    checksum.write_text(f"{digest}  {archive_name}\n", encoding="ascii")
    final_portable = inside_workspace(release_root / APP_NAME)
    previous = inside_workspace(stage / "previous-release")
    if final_portable.exists():
        final_portable.rename(previous)
    try:
        portable.rename(final_portable)
    except OSError:
        if previous.exists():
            previous.rename(final_portable)
        raise
    os.replace(staged_zip, inside_workspace(release_root / archive_name))
    os.replace(checksum, inside_workspace(release_root / checksum.name))
    print(f"Portable folder: {final_portable}")
    print(f"Release archive: {release_root / archive_name}")
    print(f"SHA256: {digest}")
    print(f"Build diagnostics retained: {stage}")
    return release_root / archive_name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate build inputs and pinned versions without building")
    arguments = parser.parse_args()
    if arguments.check:
        version, versions = preflight()
        print(f"Build preflight passed: v{version}, {len(versions)} pinned distributions.")
    else:
        build()


if __name__ == "__main__":
    main()
