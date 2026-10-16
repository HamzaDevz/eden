// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

// Systrace sections and counters, visible in atrace/Perfetto with the app category of the package.
// They cost one check when no trace is being recorded.

#ifdef __ANDROID__
#include <cstdint>
#include <cstdio>
#include <map>
#include <mutex>
#include <string>
#include <android/log.h>
#include <android/trace.h>
#include <dlfcn.h>
#include <unwind.h>

namespace Common {

class ScopedTrace {
public:
    explicit ScopedTrace(const char* name) : enabled{ATrace_isEnabled()} {
        if (enabled) {
            ATrace_beginSection(name);
        }
    }
    ~ScopedTrace() {
        if (enabled) {
            ATrace_endSection();
        }
    }

private:
    bool enabled;
};

inline void TraceCounter(const char* name, std::int64_t value) {
    if (ATrace_isEnabled()) {
        ATrace_setCounter(name, value);
    }
}

// A section that ends on another thread, matched by its cookie.
inline void TraceAsyncBegin(const char* name, std::int32_t cookie) {
    if (ATrace_isEnabled()) {
        ATrace_beginAsyncSection(name, cookie);
    }
}

inline void TraceAsyncEnd(const char* name, std::int32_t cookie) {
    if (ATrace_isEnabled()) {
        ATrace_endAsyncSection(name, cookie);
    }
}

// Diagnostic: logs the calling stack (library offsets, for llvm-symbolizer) the 1st, 2nd, 4th, 8th... time it is seen.
inline void LogCallers(const char* tag) {
    struct Walk {
        std::uintptr_t pcs[12];
        int n;
    } walk{{}, 0};
    _Unwind_Backtrace(
        [](_Unwind_Context* ctx, void* arg) {
            auto* w = static_cast<Walk*>(arg);
            if (w->n >= 12) {
                return _URC_END_OF_STACK;
            }
            w->pcs[w->n++] = _Unwind_GetIP(ctx);
            return _URC_NO_REASON;
        },
        &walk);
    std::string key;
    for (int i = 1; i < walk.n; ++i) {
        Dl_info info{};
        const std::uintptr_t base =
            dladdr(reinterpret_cast<void*>(walk.pcs[i]), &info) != 0 ? reinterpret_cast<std::uintptr_t>(info.dli_fbase) : 0;
        char buf[32];
        std::snprintf(buf, sizeof(buf), " 0x%zx", static_cast<size_t>(walk.pcs[i] - base));
        key += buf;
    }
    static std::mutex mutex;
    static std::map<std::string, std::uint64_t> seen;
    std::scoped_lock lock{mutex};
    const std::uint64_t count = ++seen[key];
    if ((count & (count - 1)) == 0) {
        __android_log_print(ANDROID_LOG_WARN, "EdenTrace", "%s x%llu:%s", tag,
                            static_cast<unsigned long long>(count), key.c_str());
    }
}

} // namespace Common

#define COMMON_TRACE_CONCAT2(a, b) a##b
#define COMMON_TRACE_CONCAT(a, b) COMMON_TRACE_CONCAT2(a, b)
#define TRACE_SCOPE(name) ::Common::ScopedTrace COMMON_TRACE_CONCAT(trace_scope_, __LINE__){name}
#define TRACE_COUNTER(name, value) ::Common::TraceCounter(name, value)
#define TRACE_ASYNC_BEGIN(name, cookie) ::Common::TraceAsyncBegin(name, cookie)
#define TRACE_ASYNC_END(name, cookie) ::Common::TraceAsyncEnd(name, cookie)
#else
#define TRACE_SCOPE(name)
#define TRACE_COUNTER(name, value)
#define TRACE_ASYNC_BEGIN(name, cookie)
#define TRACE_ASYNC_END(name, cookie)
#endif
