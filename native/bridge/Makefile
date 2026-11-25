# SPDX-License-Identifier: MIT
# WP2 status bridge. Outputs go to build/native-bridge/ (git-ignored).
#   make -C native/bridge host        host daemon + self-test
#   make -C native/bridge host-asan   host daemon + self-test with ASan/UBSan
#   make -C native/bridge arm         static ARMv7 hard-float musl daemon + self-test
# No download or installation is performed; the ARM target needs the toolchain
# already prepared under build/toolchain by the top-level `make arm`.
ROOT := $(abspath ../..)
OUT := $(ROOT)/build/native-bridge
CC ?= cc
WARN := -std=c11 -Wall -Wextra -Werror -Wformat=2 -Wshadow -Wconversion -Wsign-conversion \
        -Wmissing-prototypes -Wstrict-prototypes -Wvla -Wundef -Wcast-qual -Wpointer-arith
CORE := cammy_bridge.c backend_fake.c backend_native_unavailable.c
HDRS := cammy_bridge.h cammy_bridge_backend.h cammy_bridge_proto.h
ARM_CC := $(ROOT)/build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc
ARM_STRIP := $(ROOT)/build/toolchain/bin/arm-buildroot-linux-musleabihf-strip
ARM_FLAGS := -Os -static -march=armv7-a -mfpu=neon-vfpv4 -mfloat-abi=hard
ARM_SHIM := $(OUT)/arm/arm_legacy_stat.o

.PHONY: all host host-asan arm clean
all: host

host: $(OUT)/host/cammy-bridge $(OUT)/host/bridge-selftest
host-asan: $(OUT)/host-asan/cammy-bridge $(OUT)/host-asan/bridge-selftest
arm: $(OUT)/arm/cammy-bridge $(OUT)/arm/bridge-selftest

$(OUT)/host/cammy-bridge: $(CORE) bridge_main.c $(HDRS)
	mkdir -p $(@D)
	$(CC) -O2 $(WARN) $(CORE) bridge_main.c -o $@

$(OUT)/host/bridge-selftest: $(CORE) bridge_selftest.c $(HDRS)
	mkdir -p $(@D)
	$(CC) -O2 -pthread $(WARN) $(CORE) bridge_selftest.c -o $@

$(OUT)/host-asan/cammy-bridge: $(CORE) bridge_main.c $(HDRS)
	mkdir -p $(@D)
	$(CC) -O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined -fno-sanitize-recover=all $(WARN) $(CORE) bridge_main.c -o $@

$(OUT)/host-asan/bridge-selftest: $(CORE) bridge_selftest.c $(HDRS)
	mkdir -p $(@D)
	$(CC) -O1 -g -pthread -fno-omit-frame-pointer -fsanitize=address,undefined -fno-sanitize-recover=all $(WARN) $(CORE) bridge_selftest.c -o $@

# The camera's 4.9 kernel rejects statx; reuse the project's legacy-stat shim.
$(ARM_SHIM): $(ROOT)/src/compat/arm_legacy_stat.c
	@test -x $(ARM_CC) || { echo 'ARM toolchain missing: run top-level make arm first (no download here)'; exit 1; }
	mkdir -p $(@D)
	$(ARM_CC) -Os -Wall -Wextra -Werror -c $< -o $@

$(OUT)/arm/cammy-bridge: $(CORE) bridge_main.c $(HDRS) $(ARM_SHIM)
	$(ARM_CC) $(ARM_FLAGS) $(WARN) $(ARM_SHIM) $(CORE) bridge_main.c -o $@
	$(ARM_STRIP) $@

$(OUT)/arm/bridge-selftest: $(CORE) bridge_selftest.c $(HDRS) $(ARM_SHIM)
	$(ARM_CC) $(ARM_FLAGS) -pthread $(WARN) $(ARM_SHIM) $(CORE) bridge_selftest.c -o $@

clean:
	rm -rf $(OUT)
