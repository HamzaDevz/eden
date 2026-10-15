// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <chrono>

namespace Common::ADPF {

enum class Session {
    Render,
    Background,
};

bool IsSessionSupported(Session session);

bool AddCurrentThread(Session session);
void RemoveCurrentThread();

// Nothing reports actual work durations. The vsync-to-vsync interval reported before never fell below
// the target, so the hint service kept raising CPU clocks: +1.1 W on Zelda EoW (Odin 3) to gain 0.5 fps.
void SetTargetWorkDuration(std::chrono::nanoseconds target);

void Shutdown();

} // namespace Common::ADPF
