# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# A FIG renamed (amiibo.SHOWN) stays its AI's.
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import amiibo  # noqa: E402


def test_renamed_fig_is_its_ai():
    ais = ["Ultimus", "Mac"]
    assert amiibo.shown("Mac") == "Big Mac" and amiibo.ai_of("Big Mac") == "Mac"
    assert amiibo.named("Big Mac", ais) == "Mac"
    assert amiibo.named("Ultimus", ais) == "Ultimus"
    assert amiibo.named("Mac", ais) == "Mac"  # the card of before the rename
