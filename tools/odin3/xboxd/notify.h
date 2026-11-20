// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// Keeps every controller's battery among the console's notifications through PadNotify.java: an
// ongoing list, plus a heads-up alert as a controller connects, runs low or drops on a low battery

#pragma once

#include <string>

namespace Notify {

/// Empties the list a previous run may have left
void Clear();

/// Adds or updates a controller. An empty battery is read from the Bluetooth stack for address.
void Show(const std::string& key, const std::string& label, const std::string& battery,
          const std::string& address, bool low, const std::string& alert = {});

/// Takes a controller off the list, telling it dropped when its battery was low
void Hide(const std::string& key);

} // namespace Notify
