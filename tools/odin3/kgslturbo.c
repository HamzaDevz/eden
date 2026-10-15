// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// kgslturbo <0|1>: same ioctl as adrenotools_set_turbo (1 = pin max clock, 0 = restore DCVS).
// The state is global to the GPU and outlives the process that set it.
#include <fcntl.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define KGSL_IOC_TYPE 0x09
#define KGSL_PROP_PWRCTRL 0xE
struct kgsl_device_getproperty {
    unsigned int type;
    void* value;
    size_t sizebytes;
};
#define IOCTL_KGSL_SETPROPERTY _IOW(KGSL_IOC_TYPE, 0x32, struct kgsl_device_getproperty)

int main(int argc, char** argv) {
    unsigned int enable = (argc > 1 && atoi(argv[1])) ? 0 : 1;
    struct kgsl_device_getproperty prop = {KGSL_PROP_PWRCTRL, &enable, sizeof(enable)};
    int fd = open("/dev/kgsl-3d0", O_RDWR);
    if (fd < 0) {
        perror("open");
        return 1;
    }
    int r = ioctl(fd, IOCTL_KGSL_SETPROPERTY, &prop);
    if (r)
        perror("ioctl");
    close(fd);
    return r ? 1 : 0;
}
