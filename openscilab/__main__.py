# Copyright (C) 2026 Julian Decker
# Copyright (C) Agustín Giménez Bernad (gusmanb), original LogicAnalyzer
#
# Part of openSciLab, a port and extension of his software;
# the changes are described in docs/improvements.md and CHANGELOG.md.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Allows ``python -m openscilab``."""

if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()  # (the decoder process of a packaged application starts here)
    # (imported here, not above: the engine and index processes import this module as __mp_main__
    # when they start - the application and Qt must not come with it)
    from .app import main

    raise SystemExit(main())
