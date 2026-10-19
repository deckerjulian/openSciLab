# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The typeface of the application, Inter Tight (SIL Open Font License): fetched from Google Fonts.

openSciLab hands on no font files. At the first start the static weights it uses (regular, medium,
semibold, bold, italic) and the license are downloaded into ``<settings>/fonts`` (each file checked
against its SHA-256) and used from then on; until they are there, the system's font is used. The
download runs in a thread of its own: :func:`ensure` waits for it only a moment. Qt free
(``ui/theme.load_fonts`` registers the files with Qt).
"""

from __future__ import annotations

import logging
import os
import threading

from . import settings

log = logging.getLogger(__name__)

#: the files of the typeface: name -> (URL, SHA-256); Google Fonts' static instances of Inter Tight 3.004
FILES = {
    "InterTight-Regular.ttf": ("https://fonts.gstatic.com/s/intertight/v9/NGSnv5HMAFg6IuGlBNMjxJEL2VmU3NS7Z2mjDw-qXA.ttf",
                               "27350bc95b961b9b4e1a80d872f49e903fe727f290f6b2a079e67ceb1685fb1f"),
    "InterTight-Medium.ttf": ("https://fonts.gstatic.com/s/intertight/v9/NGSnv5HMAFg6IuGlBNMjxJEL2VmU3NS7Z2mjPQ-qXA.ttf",
                              "c2a4bdafb30500a3bd0843f95ad659ad38492f025837803e6a8ef8672c575e8e"),
    "InterTight-SemiBold.ttf": ("https://fonts.gstatic.com/s/intertight/v9/NGSnv5HMAFg6IuGlBNMjxJEL2VmU3NS7Z2mj0QiqXA.ttf",
                                "a40a1fdc93cadcbb0e3ba16d6010988118a1a3add06a097330b09cb41c2b8d45"),
    "InterTight-Bold.ttf": ("https://fonts.gstatic.com/s/intertight/v9/NGSnv5HMAFg6IuGlBNMjxJEL2VmU3NS7Z2mj6AiqXA.ttf",
                            "98272f3512338c52940816f5db61966ca1cdbc3cdb3c0d981da22612723da59e"),
    "InterTight-Italic.ttf": ("https://fonts.gstatic.com/s/intertight/v9/NGShv5HMAFg6IuGlBNMjxLsC66ZMtb8hyW62x0xCHy5X.ttf",
                              "ba1cad99039a691944564d8ebb7bbf05a6d686b69e26494a8605c00a4e9c106f"),
    "OFL.txt": ("https://raw.githubusercontent.com/google/fonts/87338f0add37763e05760424870846463a2ed1dd/ofl/intertight/OFL.txt",
                "50240ab035cf1b6b3307940235481d515c4b6de3ab1fa843dbe59e7892cb9d58"),
}

#: how long the start of the application waits for a download that has begun (seconds)
START_WAIT = 4.0

_lock = threading.Lock()
_thread: threading.Thread | None = None


def folder() -> str:
    """Where the files are (``OPENSCILAB_FONTS_DIR``, else ``<settings>/fonts``)."""
    return os.environ.get("OPENSCILAB_FONTS_DIR") or os.path.join(settings.settings_directory(), "fonts")


def font_files() -> list[str]:
    """The font files that are there."""
    base = folder()
    return [os.path.join(base, name) for name in FILES
            if name.endswith(".ttf") and os.path.isfile(os.path.join(base, name))]


def installed() -> bool:
    base = folder()
    return all(os.path.isfile(os.path.join(base, name)) for name in FILES)


def download() -> bool:
    """Fetch the files that are missing (blocks); ``True``: all are there now."""
    from .vendor import VendorError, _download

    base = folder()
    os.makedirs(base, exist_ok=True)
    for name, (url, sha256) in FILES.items():
        path = os.path.join(base, name)
        if os.path.isfile(path):
            continue
        try:
            data = _download(url, None, f"the typeface ({name})", sha256=sha256)
        except VendorError as error:
            log.info("%s", error)
            return False
        partial = f"{path}.part-{os.getpid()}"
        with open(partial, "wb") as handle:
            handle.write(data)
        os.replace(partial, path)
    return True


def ensure(wait: float = START_WAIT) -> bool:
    """Start the download when files are missing and wait at most ``wait`` seconds for it; ``True``:
    the typeface is there. A download that takes longer goes on (the files count from the next start)."""
    global _thread
    if installed():
        return True
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=download, name="typeface download", daemon=True)
            _thread.start()
        thread = _thread
    thread.join(wait)
    return installed()
