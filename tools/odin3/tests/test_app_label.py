# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# Our Odin 3 build sits next to the official app on the console: its launcher label tells them apart.
import glob
import os
import re
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
APK = os.path.join(ROOT, "src/android/app/build/outputs/apk/genshinSpoof/relWithDebInfo/"
                   "app-genshinSpoof-relWithDebInfo.apk")


def badging():
    aapt2 = sorted(glob.glob(os.path.expanduser("~/Library/Android/sdk/build-tools/*/aapt2")))[-1]
    return subprocess.run([aapt2, "dump", "badging", APK], capture_output=True, text=True,
                          check=True).stdout


def test_our_build_is_labelled_edenhome():
    assert re.search(r"^application-label:'EdenHome'$", badging(), re.M)


def test_our_build_keeps_its_own_package_next_to_the_official_app():
    assert re.search(r"^package: name='com\.miHoYo\.Yuanshen\.relWithDebInfo'", badging(), re.M)
