// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// Standalone driver for the Odin 3, run as root from AYN's "Run script as Root". Each controller
// becomes a uinput gamepad that every app sees, like xow on Linux. It also brings back the dock's
// USB host, which the firmware drops as soon as a dock's DisplayPort starts, and turns off the
// Bluetooth controllers left untouched.

#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>

#include <dirent.h>
#include <fcntl.h>
#include <glob.h>
#include <poll.h>
#include <sys/eventfd.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <linux/input.h>
#include <linux/uinput.h>
#include <linux/usbdevice_fs.h>

#include "dongle.h"
#include "log.h"
#include "pads.h"

namespace {
constexpr const char* ROLE_PATH = "/sys/class/usb_role/a600000.ssusb-role-switch/role";
constexpr const char* DISPLAY_PORT_STATUS = "/sys/class/drm/card*-DP-*/status";
constexpr const char* USB_DEVICES = "/sys/bus/usb/devices";
constexpr const char* KEY_LAYOUT = "/system/usr/keylayout/Vendor_045e_Product_%04x.kl";

constexpr std::array<uint16_t, 4> PRODUCT_IDS = {DONGLE_PID_OLD, DONGLE_PID_NEW, 0x02f9,
                                                 DONGLE_PID_SURFACE};

// Android maps the triggers wrong on controllers it has no key layout for
constexpr uint16_t FALLBACK_PRODUCT_ID = 0x02ea;

constexpr int MAX_FEEDBACK_EFFECTS = 16;
constexpr int MAX_RUMBLE = 100;

// Polls the firmware takes to settle DisplayPort, it drops the USB host right after
constexpr int DISPLAY_PORT_SETTLE_POLLS = 2;

// Hamza's pick for turning off a Bluetooth controller nobody touches
constexpr std::chrono::minutes PAD_IDLE_LIMIT{30};

// GIP report bits and the keys they stand for, as xow reports them
constexpr std::array<std::pair<uint32_t, uint16_t>, 11> BUTTONS = {{
    {0x0004, BTN_START},
    {0x0008, BTN_SELECT},
    {0x0010, BTN_A},
    {0x0020, BTN_B},
    {0x0040, BTN_X},
    {0x0080, BTN_Y},
    {0x1000, BTN_TL},
    {0x2000, BTN_TR},
    {0x4000, BTN_THUMBL},
    {0x8000, BTN_THUMBR},
    {CONTROLLER_GUIDE_BUTTON, BTN_MODE},
}};
constexpr uint32_t DPAD_UP = 0x0100;
constexpr uint32_t DPAD_DOWN = 0x0200;
constexpr uint32_t DPAD_LEFT = 0x0400;
constexpr uint32_t DPAD_RIGHT = 0x0800;

std::atomic<bool> quit{false};
std::atomic<bool> usb_failed{false};

std::mutex dongle_mutex;
Dongle* dongle = nullptr;

// Rumble goes through its own thread: the dongle's lock can be held while a gamepad is removed
std::mutex rumble_mutex;
std::condition_variable rumble_condition;
std::map<uint8_t, uint8_t> pending_rumble;

std::string ReadLine(const std::string& path) {
    const int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        return {};
    }
    char buffer[64];
    const ssize_t size = read(fd, buffer, sizeof(buffer) - 1);
    close(fd);
    if (size <= 0) {
        return {};
    }
    std::string line(buffer, static_cast<size_t>(size));
    return line.substr(0, line.find('\n'));
}

bool WriteLine(const char* path, const char* value) {
    const int fd = open(path, O_WRONLY | O_CLOEXEC);
    if (fd < 0) {
        return false;
    }
    const ssize_t length = static_cast<ssize_t>(std::strlen(value));
    const bool written = write(fd, value, length) == length;
    close(fd);
    return written;
}

void RequestRumble(uint8_t slot, uint8_t power) {
    {
        std::lock_guard lock(rumble_mutex);
        pending_rumble[slot] = power;
    }
    rumble_condition.notify_one();
}

void ProcessRumble() {
    std::unique_lock lock(rumble_mutex);
    while (!quit) {
        rumble_condition.wait(lock, [] { return quit || !pending_rumble.empty(); });
        auto requests = std::exchange(pending_rumble, {});
        lock.unlock();
        {
            std::lock_guard dongle_lock(dongle_mutex);
            if (dongle != nullptr) {
                for (const auto& [slot, power] : requests) {
                    dongle->setRumble(slot, power);
                }
            }
        }
        lock.lock();
    }
}

class Gamepad {
public:
    Gamepad(uint8_t slot_, uint16_t vendor_id, uint16_t product_id) : slot(slot_) {
        fd = open("/dev/uinput", O_RDWR | O_CLOEXEC);
        if (fd < 0) {
            throw std::runtime_error(std::string("opening uinput: ") + std::strerror(errno));
        }
        for (const auto& [mask, code] : BUTTONS) {
            Check(ioctl(fd, UI_SET_EVBIT, EV_KEY), "adding a button");
            Check(ioctl(fd, UI_SET_KEYBIT, code), "adding a button");
        }
        AddAxis(ABS_X, -32768, 32767, 255, 4095);
        AddAxis(ABS_Y, -32768, 32767, 255, 4095);
        AddAxis(ABS_RX, -32768, 32767, 255, 4095);
        AddAxis(ABS_RY, -32768, 32767, 255, 4095);
        AddAxis(ABS_Z, 0, 1023, 3, 63);
        AddAxis(ABS_RZ, 0, 1023, 3, 63);
        AddAxis(ABS_HAT0X, -1, 1, 0, 0);
        AddAxis(ABS_HAT0Y, -1, 1, 0, 0);
        Check(ioctl(fd, UI_SET_EVBIT, EV_FF), "adding rumble");
        Check(ioctl(fd, UI_SET_FFBIT, FF_RUMBLE), "adding rumble");

        char key_layout[64];
        std::snprintf(key_layout, sizeof(key_layout), KEY_LAYOUT, product_id);
        uinput_setup setup{};
        setup.id.bustype = BUS_USB;
        setup.id.vendor = vendor_id;
        setup.id.product = access(key_layout, F_OK) == 0 ? product_id : FALLBACK_PRODUCT_ID;
        setup.id.version = 1;
        setup.ff_effects_max = MAX_FEEDBACK_EFFECTS;
        std::snprintf(setup.name, sizeof(setup.name), "Xbox Wireless Controller");
        Check(ioctl(fd, UI_DEV_SETUP, &setup), "setting up the gamepad");
        // Android merges input devices with the same ids and location into one device
        char phys[32];
        std::snprintf(phys, sizeof(phys), "xboxd/slot%d", slot);
        Check(ioctl(fd, UI_SET_PHYS, phys), "naming the gamepad's location");
        Check(ioctl(fd, UI_DEV_CREATE), "creating the gamepad");

        stop_fd = eventfd(0, EFD_CLOEXEC);
        thread = std::thread(&Gamepad::ReadEvents, this);
        Log::info("Controller %d plays as %04x:%04x", slot, setup.id.vendor, setup.id.product);
    }

    ~Gamepad() {
        const uint64_t stop = 1;
        write(stop_fd, &stop, sizeof(stop));
        thread.join();
        ioctl(fd, UI_DEV_DESTROY);
        close(fd);
        close(stop_fd);
        Log::info("Controller %d left", slot);
    }

    void Report(const ControllerState& state) {
        for (const auto& [mask, code] : BUTTONS) {
            Emit(EV_KEY, code, (state.buttons & mask) != 0);
        }
        // GIP sticks point up, evdev ones down
        Emit(EV_ABS, ABS_X, state.stickLeftX);
        Emit(EV_ABS, ABS_Y, ~state.stickLeftY);
        Emit(EV_ABS, ABS_RX, state.stickRightX);
        Emit(EV_ABS, ABS_RY, ~state.stickRightY);
        Emit(EV_ABS, ABS_Z, state.triggerLeft);
        Emit(EV_ABS, ABS_RZ, state.triggerRight);
        Emit(EV_ABS, ABS_HAT0X,
             ((state.buttons & DPAD_RIGHT) != 0) - ((state.buttons & DPAD_LEFT) != 0));
        Emit(EV_ABS, ABS_HAT0Y,
             ((state.buttons & DPAD_DOWN) != 0) - ((state.buttons & DPAD_UP) != 0));
        Emit(EV_SYN, SYN_REPORT, 0);
    }

private:
    static void Check(int result, const char* what) {
        if (result < 0) {
            throw std::runtime_error(std::string(what) + ": " + std::strerror(errno));
        }
    }

    void AddAxis(uint16_t code, int32_t minimum, int32_t maximum, int32_t fuzz, int32_t flat) {
        Check(ioctl(fd, UI_SET_EVBIT, EV_ABS), "adding an axis");
        Check(ioctl(fd, UI_SET_ABSBIT, code), "adding an axis");
        uinput_abs_setup setup{};
        setup.code = code;
        setup.absinfo.minimum = minimum;
        setup.absinfo.maximum = maximum;
        setup.absinfo.fuzz = fuzz;
        setup.absinfo.flat = flat;
        Check(ioctl(fd, UI_ABS_SETUP, &setup), "setting up an axis");
    }

    void Emit(uint16_t type, uint16_t code, int32_t value) {
        input_event event{};
        event.type = type;
        event.code = code;
        event.value = value;
        write(fd, &event, sizeof(event));
    }

    /// Serves the rumble effects apps upload and play through the gamepad
    void ReadEvents() {
        std::array<uint8_t, MAX_FEEDBACK_EFFECTS> effects{};
        std::array<pollfd, 2> fds = {{{fd, POLLIN, 0}, {stop_fd, POLLIN, 0}}};
        while (true) {
            if (poll(fds.data(), fds.size(), -1) < 0) {
                if (errno == EINTR) {
                    continue;
                }
                return;
            }
            if (fds[1].revents != 0) {
                return;
            }
            input_event event{};
            if (read(fd, &event, sizeof(event)) != sizeof(event)) {
                continue;
            }

            if (event.type == EV_UINPUT && event.code == UI_FF_UPLOAD) {
                uinput_ff_upload upload{};
                upload.request_id = event.value;
                if (ioctl(fd, UI_BEGIN_FF_UPLOAD, &upload) < 0) {
                    continue;
                }
                const ff_effect& effect = upload.effect;
                if (effect.type == FF_RUMBLE && effect.id >= 0 &&
                    effect.id < MAX_FEEDBACK_EFFECTS) {
                    const int magnitude =
                        std::max(effect.u.rumble.strong_magnitude, effect.u.rumble.weak_magnitude);
                    effects[effect.id] = static_cast<uint8_t>(magnitude * MAX_RUMBLE / 0xffff);
                }
                upload.retval = 0;
                ioctl(fd, UI_END_FF_UPLOAD, &upload);
            } else if (event.type == EV_UINPUT && event.code == UI_FF_ERASE) {
                uinput_ff_erase erase{};
                erase.request_id = event.value;
                if (ioctl(fd, UI_BEGIN_FF_ERASE, &erase) < 0) {
                    continue;
                }
                if (erase.effect_id < MAX_FEEDBACK_EFFECTS) {
                    effects[erase.effect_id] = 0;
                }
                erase.retval = 0;
                ioctl(fd, UI_END_FF_ERASE, &erase);
            } else if (event.type == EV_FF && event.code < MAX_FEEDBACK_EFFECTS) {
                RequestRumble(slot, event.value != 0 ? effects[event.code] : 0);
            }
        }
    }

    const uint8_t slot;
    int fd = -1;
    int stop_fd = -1;
    std::thread thread;
};

std::mutex gamepads_mutex;
std::array<std::unique_ptr<Gamepad>, MT_WCID_COUNT> gamepads;

ControllerEvents MakeEvents() {
    ControllerEvents events;
    events.connected = [](uint8_t slot, uint16_t vendor_id, uint16_t product_id) {
        std::lock_guard lock(gamepads_mutex);
        try {
            gamepads.at(slot) = std::make_unique<Gamepad>(slot, vendor_id, product_id);
        } catch (const std::exception& e) {
            Log::error("Could not add controller %d: %s", slot, e.what());
        }
    };
    events.disconnected = [](uint8_t slot) {
        std::lock_guard lock(gamepads_mutex);
        if (slot < gamepads.size()) {
            gamepads[slot].reset();
        }
    };
    events.input = [](uint8_t slot, const ControllerState& state) {
        std::lock_guard lock(gamepads_mutex);
        if (slot < gamepads.size() && gamepads[slot]) {
            gamepads[slot]->Report(state);
        }
    };
    return events;
}

bool DisplayPortConnected() {
    glob_t paths{};
    if (glob(DISPLAY_PORT_STATUS, 0, nullptr, &paths) != 0) {
        return false;
    }
    bool connected = false;
    for (size_t i = 0; i < paths.gl_pathc; ++i) {
        connected |= ReadLine(paths.gl_pathv[i]) == "connected";
    }
    globfree(&paths);
    return connected;
}

/// Puts the USB host back once a dock's DisplayPort is up, the firmware leaves it with no role
void KeepDockUsb() {
    static int connected_polls = 0;
    static bool reported = false;
    if (!DisplayPortConnected()) {
        connected_polls = 0;
        reported = false;
        return;
    }
    if (++connected_polls < DISPLAY_PORT_SETTLE_POLLS || ReadLine(ROLE_PATH) != "none") {
        return;
    }
    const bool restored = WriteLine(ROLE_PATH, "host");
    if (!reported) {
        reported = true;
        if (restored) {
            Log::info("Brought the dock's USB back");
        } else {
            Log::error("Could not bring the dock's USB back: %s", std::strerror(errno));
        }
    }
}

struct FoundAdapter {
    std::string path;
    uint16_t product_id;
};

bool FindAdapter(FoundAdapter& found) {
    DIR* devices = opendir(USB_DEVICES);
    if (devices == nullptr) {
        return false;
    }
    bool matched = false;
    while (dirent* entry = readdir(devices)) {
        // Interfaces have a colon in their name, devices don't
        if (entry->d_name[0] == '.' || std::strchr(entry->d_name, ':') != nullptr) {
            continue;
        }
        const std::string base = std::string(USB_DEVICES) + "/" + entry->d_name + "/";
        // A device going away mid-scan leaves some of its files empty
        try {
            if (std::stoi(ReadLine(base + "idVendor"), nullptr, 16) != DONGLE_VID) {
                continue;
            }
            const auto product_id =
                static_cast<uint16_t>(std::stoi(ReadLine(base + "idProduct"), nullptr, 16));
            if (std::find(PRODUCT_IDS.begin(), PRODUCT_IDS.end(), product_id) ==
                PRODUCT_IDS.end()) {
                continue;
            }
            char path[32];
            std::snprintf(path, sizeof(path), "/dev/bus/usb/%03d/%03d",
                          std::stoi(ReadLine(base + "busnum")),
                          std::stoi(ReadLine(base + "devnum")));
            found = {path, product_id};
            matched = true;
            break;
        } catch (const std::exception&) {
            continue;
        }
    }
    closedir(devices);
    return matched;
}

class Session {
public:
    explicit Session(const std::string& firmware_dir) : firmware_dir(firmware_dir) {}

    ~Session() {
        Stop();
    }

    /// Opens the adapter when one shows up, and lets go of it once it is gone
    void Update() {
        if (current && usb_failed) {
            Stop();
        }
        FoundAdapter found;
        if (current || !FindAdapter(found)) {
            if (!current) {
                failed_path.clear();
            }
            return;
        }
        if (found.path == failed_path) {
            return;
        }

        fd = open(found.path.c_str(), O_RDWR | O_CLOEXEC);
        unsigned int interface_number = 0;
        if (fd < 0 || ioctl(fd, USBDEVFS_CLAIMINTERFACE, &interface_number) < 0) {
            // Eden's own driver may hold it while a game runs
            if (!claim_reported) {
                claim_reported = true;
                Log::error("Could not claim the adapter: %s", std::strerror(errno));
            }
            CloseDevice();
            return;
        }
        claim_reported = false;

        char firmware[64];
        std::snprintf(firmware, sizeof(firmware), "/xone_dongle_%04x.bin", found.product_id);
        usb_failed = false;
        try {
            current = std::make_unique<Dongle>(
                std::make_unique<UsbDevice>(fd, [] { usb_failed = true; }),
                firmware_dir + firmware, MakeEvents());
        } catch (const std::exception& e) {
            Log::error("Could not start the adapter: %s", e.what());
            failed_path = found.path;
            CloseDevice();
            return;
        }
        std::lock_guard lock(dongle_mutex);
        dongle = current.get();
        Log::info("Adapter %04x opened", found.product_id);
    }

private:
    void Stop() {
        if (!current) {
            return;
        }
        {
            std::lock_guard lock(dongle_mutex);
            dongle = nullptr;
        }
        current.reset();
        CloseDevice();
        Log::info("Adapter closed");
    }

    void CloseDevice() {
        if (fd < 0) {
            return;
        }
        unsigned int interface_number = 0;
        ioctl(fd, USBDEVFS_RELEASEINTERFACE, &interface_number);
        close(fd);
        fd = -1;
    }

    const std::string firmware_dir;
    std::unique_ptr<Dongle> current;
    int fd = -1;
    std::string failed_path;
    bool claim_reported = false;
};
} // namespace

int main(int argc, char** argv) {
    const std::string firmware_dir = argc > 1 ? argv[1] : "/data/adb/odin3-xbox";
    std::signal(SIGTERM, [](int) { quit = true; });
    std::signal(SIGINT, [](int) { quit = true; });
    std::signal(SIGHUP, SIG_IGN);
    std::signal(SIGPIPE, SIG_IGN);
    setvbuf(stderr, nullptr, _IOLBF, 0);
    Log::info("Started, firmware in %s", firmware_dir.c_str());

    std::thread rumble_thread(ProcessRumble);
    {
        Session session(firmware_dir);
        BluetoothPads pads(PAD_IDLE_LIMIT);
        while (!quit) {
            KeepDockUsb();
            session.Update();
            pads.Update();
            std::this_thread::sleep_for(std::chrono::seconds(1));
        }
    }
    rumble_condition.notify_one();
    rumble_thread.join();
    Log::info("Stopped");
    return 0;
}
