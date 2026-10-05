"""Keep the release version, Windows resources and packaged notices in sync."""

import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from pdf_enhance_core import __version__
from tools.collect_licenses import collect


ROOT = Path(__file__).resolve().parents[1]


def test_release_version_and_license():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert __version__ == "1.0.0"
    assert f'version = "{__version__}"' in project
    assert 'license = "AGPL-3.0-only"' in project
    assembly = ET.parse(ROOT / "windows_app.manifest").find("{urn:schemas-microsoft-com:asm.v1}assemblyIdentity")
    assert assembly.attrib["version"] == f"{__version__}.0"
    resource = (ROOT / "windows_version_info.txt").read_text(encoding="utf-8")
    assert f"'FileVersion', '{__version__}.0'" in resource
    assert f"'ProductVersion', '{__version__}'" in resource
    result = subprocess.run([sys.executable, "pdf_enhance.py", "--version"], cwd=ROOT,
                            capture_output=True, text=True, check=True)
    assert result.stdout.strip() == f"PDF-Enhance {__version__}"


def test_distribution_notices(tmp_path):
    collect(tmp_path)
    assert (tmp_path / "LICENSE.txt").read_text(encoding="utf-8") == (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in (tmp_path / "LICENSE.txt").read_text(encoding="utf-8")
    assert (tmp_path / "THIRD_PARTY_NOTICES.md").exists()
    assert (tmp_path / "licenses" / "RapidOCR-LICENSE.txt").exists()
    manifest = json.loads((tmp_path / "BUILD_DEPENDENCIES.json").read_text(encoding="utf-8"))
    pymupdf = next(package for package in manifest["packages"] if package["name"].lower() == "pymupdf")
    assert pymupdf["version"] in pymupdf["source_index"]
    assert manifest["mupdf"]["source_archive"].endswith("-source.tar.gz")
    assert list((tmp_path / "licenses").rglob("*COPYING"))
