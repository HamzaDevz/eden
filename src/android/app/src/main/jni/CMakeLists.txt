# SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

# SPDX-FileCopyrightText: 2023 yuzu Emulator Project
# SPDX-License-Identifier: GPL-3.0-or-later

add_library(yuzu-android SHARED
    emu_window/emu_window.cpp
    emu_window/emu_window.h
    native.cpp
    native.h
    native_config.cpp
    native_freedreno.cpp
    android_settings.cpp
    game_metadata.cpp
    native_log.cpp
    android_config.cpp
    android_config.h
    native_input.cpp
    native_post_processing.cpp
    xbox_dongle/bytes.h
    xbox_dongle/controller.cpp
    xbox_dongle/controller.h
    xbox_dongle/dongle.cpp
    xbox_dongle/dongle.h
    xbox_dongle/gip.cpp
    xbox_dongle/gip.h
    xbox_dongle/log.h
    xbox_dongle/mt76.cpp
    xbox_dongle/mt76.h
    xbox_dongle/usb.cpp
    xbox_dongle/usb.h
    xbox_dongle/xbox_dongle_jni.cpp
)

set_property(TARGET yuzu-android PROPERTY IMPORTED_LOCATION ${FFmpeg_LIBRARY_DIR})

target_link_libraries(yuzu-android PRIVATE audio_core common core input_common frontend_common video_core)
target_link_libraries(yuzu-android PRIVATE android camera2ndk EGL glad jnigraphics log GLESv2)
if (ARCHITECTURE_arm64)
    target_link_libraries(yuzu-android PRIVATE adrenotools)
endif()

target_link_libraries(yuzu-android PRIVATE ${FFmpeg_LIBRARIES} OpenSSL::SSL cpp-jwt::cpp-jwt)
if (ENABLE_UPDATE_CHECKER)
    target_compile_definitions(yuzu-android PUBLIC ENABLE_UPDATE_CHECKER)
endif()

set(CPACK_PACKAGE_EXECUTABLES ${CPACK_PACKAGE_EXECUTABLES} yuzu-android)

target_link_options(yuzu-android PRIVATE "-Wl,-Bsymbolic")
