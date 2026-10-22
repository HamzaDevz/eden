// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <functional>
#include <memory>

#include "common/common_types.h"

namespace Core {
class System;
}

namespace Tools {

/// Presses buttons and moves sticks (-1..1) for one npad index; set by the frontend.
using GameBridgePadSink =
    std::function<void(std::size_t player, u64 buttons, float lx, float ly, float rx, float ry)>;

/**
 * Line-based TCP server that lets an external agent watch and play the running game: once per
 * composited frame it streams every connected controller and a list of watched guest memory
 * locations, and it takes controller input for any player. See tools/odin3/bridge.py.
 */
class GameBridge {
public:
    explicit GameBridge(Core::System& system, u16 port, GameBridgePadSink pad_sink);
    ~GameBridge();

    /// Called by the display once per composited frame.
    void OnFrame();

private:
    struct Impl;
    std::unique_ptr<Impl> impl;
};

} // namespace Tools
