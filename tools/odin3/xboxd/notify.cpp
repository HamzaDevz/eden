// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#include "notify.h"

#include <condition_variable>
#include <deque>
#include <map>
#include <mutex>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <grp.h>
#include <sys/wait.h>
#include <unistd.h>

#include "log.h"

namespace {
constexpr const char* CLASS_PATH = "-Djava.class.path=/data/local/tmp/odin3-xbox/padoff.dex";
constexpr uid_t SHELL_ID = 2000;

struct Entry {
    std::string label;
    std::string battery;
    std::string address;
    bool low;
};

struct Alert {
    std::string title;
    std::string battery;
    std::string address;
};

std::mutex mutex;
std::condition_variable wake;
std::map<std::string, Entry> entries;
std::deque<Alert> alerts;
bool dirty = false;

/// Posts through PadNotify as the shell user, which owns com.android.shell's notifications
void Post(const std::vector<std::string>& args) {
    std::vector<const char*> argv{"app_process", CLASS_PATH, "/system/bin", "PadNotify"};
    for (const std::string& arg : args) {
        argv.push_back(arg.c_str());
    }
    argv.push_back(nullptr);
    const pid_t pid = fork();
    if (pid == 0) {
        const int null_fd = open("/dev/null", O_WRONLY);
        dup2(null_fd, STDOUT_FILENO);
        if (getuid() == 0 && (setgroups(0, nullptr) != 0 ||
                              setresgid(SHELL_ID, SHELL_ID, SHELL_ID) != 0 ||
                              setresuid(SHELL_ID, SHELL_ID, SHELL_ID) != 0)) {
            _exit(126);
        }
        execv("/system/bin/app_process", const_cast<char* const*>(argv.data()));
        _exit(127);
    }
    int status = -1;
    if (pid < 0 || waitpid(pid, &status, 0) != pid || !WIFEXITED(status) ||
        WEXITSTATUS(status) != 0) {
        Log::error("Could not show the controllers' batteries (status %d)", status);
    }
}

void Work() {
    while (true) {
        std::vector<std::string> args;
        {
            std::unique_lock lock(mutex);
            wake.wait(lock, [] { return dirty || !alerts.empty(); });
            const Alert alert = alerts.empty() ? Alert{} : alerts.front();
            if (!alerts.empty()) {
                alerts.pop_front();
            }
            dirty = false;
            args = {alert.title, alert.battery + "|" + alert.address};
            for (const auto& [key, entry] : entries) {
                args.push_back(entry.label + "|" + entry.battery + "|" + entry.address);
            }
        }
        Post(args);
    }
}

/// Called with mutex held
void Changed(Alert alert = {}) {
    static std::once_flag started;
    std::call_once(started, [] { std::thread(Work).detach(); });
    if (!alert.title.empty()) {
        alerts.push_back(std::move(alert));
    }
    dirty = true;
    wake.notify_one();
}
} // namespace

namespace Notify {

void Clear() {
    std::lock_guard lock(mutex);
    entries.clear();
    Changed();
}

void Show(const std::string& key, const std::string& label, const std::string& battery,
          const std::string& address, bool low, const std::string& alert) {
    std::lock_guard lock(mutex);
    Entry& entry = entries[key];
    if (alert.empty() && entry.label == label && entry.battery == battery && entry.low == low) {
        return;
    }
    entry = {label, battery, address, low};
    Changed({alert, battery, address});
}

void Hide(const std::string& key) {
    std::lock_guard lock(mutex);
    const auto it = entries.find(key);
    if (it == entries.end()) {
        return;
    }
    Alert alert;
    if (it->second.low) {
        alert = {it->second.label + " déconnectée, batterie à plat", it->second.battery,
                 it->second.address};
    }
    entries.erase(it);
    Changed(std::move(alert));
}

} // namespace Notify
