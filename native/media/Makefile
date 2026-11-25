# SPDX-License-Identifier: MIT
# WP3 owned-media core. Outputs go only to build/native-media-owned/ (git-ignored).
#   make -C native/media host        host fixture
#   make -C native/media host-asan   host fixture with ASan/UBSan
#   make -C native/media host-tsan   host fixture with ThreadSanitizer (mailbox stress)
#   make -C native/media arm         static ARMv7 hard-float musl fixture (standalone; NOT an in-process build)
# No download or installation is performed.
ROOT := $(abspath ../..)
OUT := $(ROOT)/build/native-media-owned
CC ?= cc
WARN := -std=c11 -Wall -Wextra -Werror -Wformat=2 -Wshadow -Wconversion -Wsign-conversion \
        -Wmissing-prototypes -Wstrict-prototypes -Wvla -Wundef -Wcast-qual -Wpointer-arith
CORE := cammy_media.c media_backend_native_unavailable.c media_clock_posix.c
TEST := media_backend_fake.c media_fixture.c
HDRS := cammy_media.h media_backend_fake.h
ARM_CC := $(ROOT)/build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc
ARM_STRIP := $(ROOT)/build/toolchain/bin/arm-buildroot-linux-musleabihf-strip
ARM_FLAGS := -Os -static -march=armv7-a -mfpu=neon-vfpv4 -mfloat-abi=hard

.PHONY: all host host-asan host-tsan arm core-obj clean
all: host

host: $(OUT)/host/media-fixture
host-asan: $(OUT)/host-asan/media-fixture
host-tsan: $(OUT)/host-tsan/media-fixture
arm: $(OUT)/arm/media-fixture $(OUT)/arm/cammy_media_core.o

$(OUT)/host/media-fixture: $(CORE) $(TEST) $(HDRS)
	mkdir -p $(@D)
	$(CC) -O2 -pthread $(WARN) $(CORE) $(TEST) -o $@

$(OUT)/host-asan/media-fixture: $(CORE) $(TEST) $(HDRS)
	mkdir -p $(@D)
	$(CC) -O1 -g -pthread -fno-omit-frame-pointer -fsanitize=address,undefined -fno-sanitize-recover=all $(WARN) $(CORE) $(TEST) -o $@

$(OUT)/host-tsan/media-fixture: $(CORE) $(TEST) $(HDRS)
	mkdir -p $(@D)
	$(CC) -O1 -g -pthread -fsanitize=thread $(WARN) $(CORE) $(TEST) -o $@

$(OUT)/arm/media-fixture: $(CORE) $(TEST) $(HDRS)
	@test -x $(ARM_CC) || { echo 'ARM toolchain missing: run top-level make arm first (no download here)'; exit 1; }
	mkdir -p $(@D)
	$(ARM_CC) $(ARM_FLAGS) -pthread $(WARN) $(CORE) $(TEST) -o $@
	$(ARM_STRIP) $@

# Core-only object, used solely to inventory undefined symbols (imports) for the
# in-process ABI gate. Built with the musl 1.2.5 toolchain: NOT camera-ABI proof.
$(OUT)/arm/cammy_media_core.o: $(CORE) cammy_media.h
	mkdir -p $(@D)
	$(ARM_CC) -Os -fPIC -march=armv7-a -mfpu=neon-vfpv4 -mfloat-abi=hard $(WARN) -c cammy_media.c -o $@

clean:
	rm -rf $(OUT)
