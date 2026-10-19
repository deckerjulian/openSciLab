"""The typeface is fetched from Google Fonts, never handed on with openSciLab (core/typeface.py)."""

from __future__ import annotations

import os

from openscilab.core import typeface, vendor


def test_the_files_come_from_their_projects_with_checksums():
    for url, sha256 in typeface.FILES.values():
        assert url.startswith(("https://fonts.gstatic.com/", "https://raw.githubusercontent.com/google/fonts/"))
        assert len(sha256) == 64
    assert "OFL.txt" in typeface.FILES  # (the license travels with the files)
    package = os.path.join(os.path.dirname(typeface.__file__), "..", "ui", "fonts")
    assert typeface.folder() != os.path.normpath(package)


def test_fetched_once_into_the_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSCILAB_FONTS_DIR", str(tmp_path / "fonts"))
    asked = []
    monkeypatch.setattr(vendor, "_download", lambda url, progress, what, share=(0, 1), sha256="":
                        asked.append((url, sha256)) or b"font")
    assert typeface.ensure(wait=10)
    assert sorted(os.listdir(tmp_path / "fonts")) == sorted(typeface.FILES)
    assert all(sha256 for _url, sha256 in asked)  # (each file is checked)
    asked.clear()
    assert typeface.ensure(wait=10) and not asked  # (there: not fetched again)


def test_without_a_connection_the_system_font_stays(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSCILAB_FONTS_DIR", str(tmp_path / "fonts"))

    def offline(url, progress, what, share=(0, 1), sha256=""):
        raise vendor.VendorError(f"{what} could not be downloaded (no connection)")

    monkeypatch.setattr(vendor, "_download", offline)
    assert not typeface.ensure(wait=10)
    assert typeface.font_files() == []
    assert not [name for name in os.listdir(tmp_path / "fonts") if ".part-" in name]
