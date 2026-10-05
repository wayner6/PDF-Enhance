"""Collect build-environment license files without adding a packaging dependency."""

from importlib.metadata import distributions
import json
from pathlib import Path
import platform
import shutil
import sys

import pymupdf


ROOT = Path(__file__).resolve().parents[1]


def collect(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "LICENSE", output / "LICENSE.txt")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", output / "THIRD_PARTY_NOTICES.md")
    shutil.copytree(ROOT / "licenses", output / "licenses", dirs_exist_ok=True)
    packages = []
    for dist in sorted(distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        if name.lower() == "pdf-enhance":
            continue
        target = output / "licenses" / f"{name}-{dist.version}"
        target.mkdir(parents=True, exist_ok=True)
        (target / "METADATA.txt").write_text(dist.read_text("METADATA") or "", encoding="utf-8")
        for index, file in enumerate(dist.files or []):
            if any(word in file.name.lower() for word in ("license", "licence", "copying", "notice", "copyright")):
                source = dist.locate_file(file)
                if source.is_file() and source.suffix not in (".py", ".pyc"):
                    shutil.copy2(source, target / f"{index:04d}-{file.name}")
        packages.append({
            "name": name, "version": dist.version,
            "project_urls": dist.metadata.get_all("Project-URL") or [],
            "homepage": dist.metadata.get("Home-page"),
            "source_index": f"https://pypi.org/project/{name}/{dist.version}/#files",
        })
    for name in ("LICENSE", "LICENSE.txt"):
        path = Path(sys.base_prefix) / name
        if path.is_file():
            shutil.copy2(path, output / "licenses" / "Python-LICENSE.txt")
    for index, path in enumerate(sorted((Path(sys.base_prefix) / "tcl").rglob("license.terms"))):
        shutil.copy2(path, output / "licenses" / f"Tcl-Tk-{index}-license.terms")
    mupdf_version = pymupdf.version[1]
    (output / "BUILD_DEPENDENCIES.json").write_text(json.dumps({
        "python": platform.python_version(), "platform": platform.platform(),
        "packages": packages,
        "mupdf": {
            "version": mupdf_version,
            "source_archive": f"https://mupdf.com/downloads/archive/mupdf-{mupdf_version}-source.tar.gz",
        },
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    output = ROOT / "build" / "distribution-notices"
    if output.exists():
        shutil.rmtree(output)
    collect(output)
    shutil.make_archive(str(ROOT / "build" / "PDF-Enhance-Licenses"), "zip", output)
