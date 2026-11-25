# SPDX-License-Identifier: MIT
ROOT := $(abspath ../..)
OUT := $(ROOT)/build/native-audio
CC ?= cc
WARN := -std=c11 -Wall -Wextra -Werror -Wconversion -Wshadow -Wstrict-prototypes -Wmissing-prototypes
ARM_CC := $(ROOT)/build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc
ARM_FLAGS := -Os -march=armv7-a -mfpu=neon-vfpv4 -mfloat-abi=hard
.PHONY: host host-asan arm async-host async-asan async-tsan async-arm
host: $(OUT)/host/audio-fixture
host-asan: $(OUT)/host-asan/audio-fixture
arm: $(OUT)/arm/audio-fixture $(OUT)/arm/cammy_audio.o
$(OUT)/host/audio-fixture: cammy_audio.c cammy_audio.h audio_fixture.c
	mkdir -p $(@D)
	$(CC) $(WARN) -O2 cammy_audio.c audio_fixture.c -o $@
$(OUT)/host-asan/audio-fixture: cammy_audio.c cammy_audio.h audio_fixture.c
	mkdir -p $(@D)
	$(CC) $(WARN) -O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined -fno-sanitize-recover=all cammy_audio.c audio_fixture.c -o $@
$(OUT)/arm/audio-fixture: cammy_audio.c cammy_audio.h audio_fixture.c
	mkdir -p $(@D)
	$(ARM_CC) $(WARN) $(ARM_FLAGS) -static cammy_audio.c audio_fixture.c -o $@
$(OUT)/arm/cammy_audio.o: cammy_audio.c cammy_audio.h
	mkdir -p $(@D)
	$(ARM_CC) $(WARN) $(ARM_FLAGS) -fPIC -c cammy_audio.c -o $@

ASYNC := cammy_audio.c cammy_audio_async.c async_fixture.c
ASYNC_HDR := cammy_audio.h cammy_audio_async.h
async-host: $(OUT)/host/async-fixture
async-asan: $(OUT)/host-asan/async-fixture
async-tsan: $(OUT)/host-tsan/async-fixture
async-arm: $(OUT)/arm/async-fixture
$(OUT)/host/async-fixture: $(ASYNC) $(ASYNC_HDR)
	mkdir -p $(@D)
	$(CC) $(WARN) -O2 -pthread $(ASYNC) -o $@
$(OUT)/host-asan/async-fixture: $(ASYNC) $(ASYNC_HDR)
	mkdir -p $(@D)
	$(CC) $(WARN) -O1 -g -pthread -fno-omit-frame-pointer -fsanitize=address,undefined -fno-sanitize-recover=all $(ASYNC) -o $@
$(OUT)/host-tsan/async-fixture: $(ASYNC) $(ASYNC_HDR)
	mkdir -p $(@D)
	$(CC) $(WARN) -O1 -g -pthread -fsanitize=thread $(ASYNC) -o $@
$(OUT)/arm/async-fixture: $(ASYNC) $(ASYNC_HDR)
	mkdir -p $(@D)
	$(ARM_CC) $(WARN) $(ARM_FLAGS) -static -pthread $(ASYNC) -o $@

# Device ABI: saved musl 1.1.24, no new libc or SDK build/download. Synthetic
# driver only; this library has no native resolver, loader stub or deployment.
DEV_SYSROOT := $(ROOT)/build/native-loader/sysroot-1.1.24
DEV_ROOTFS := $(ROOT)/build/native-loader/device-rootfs
DEV_GCC_INC := $(shell $(ARM_CC) -print-file-name=include 2>/dev/null)
DEV_LIBGCC := $(shell $(ARM_CC) -print-libgcc-file-name 2>/dev/null)
DEV_FLAGS := -Os -marm -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard \
    -nostdinc -isystem $(DEV_SYSROOT)/include -isystem $(DEV_GCC_INC) \
    -fno-stack-protector -U_FORTIFY_SOURCE -fno-unwind-tables -fno-asynchronous-unwind-tables
DEV_LIBS := -L$(DEV_ROOTFS)/lib -lc $(DEV_LIBGCC)
.PHONY: async-device
async-device: $(OUT)/device/libcammy_audio_async.so $(OUT)/device/async-fixture
$(OUT)/device/libcammy_audio_async.so: cammy_audio.c cammy_audio_async.c $(ASYNC_HDR) async.exports.map
	@test -f $(DEV_SYSROOT)/include/bits/alltypes.h -a -f $(DEV_ROOTFS)/lib/libc.so || { echo 'Saved loader ABI profile missing; build native/loader device first'; exit 1; }
	mkdir -p $(@D)
	$(ARM_CC) $(WARN) $(DEV_FLAGS) -fPIC -c cammy_audio.c -o $(@D)/core.o
	$(ARM_CC) $(WARN) $(DEV_FLAGS) -fPIC -c cammy_audio_async.c -o $(@D)/async.o
	$(ARM_CC) -shared -nostdlib -Wl,-soname,libcammy_audio_async.so -Wl,-z,now -Wl,-z,relro \
	    -Wl,-z,noexecstack -Wl,-z,defs -Wl,--hash-style=both -Wl,--build-id=none \
	    -Wl,--version-script,async.exports.map $(@D)/core.o $(@D)/async.o $(DEV_LIBS) -o $@
$(OUT)/device/async-fixture: async_fixture.c $(ASYNC_HDR) $(OUT)/device/libcammy_audio_async.so
	$(ARM_CC) $(WARN) $(DEV_FLAGS) -c async_fixture.c -o $(@D)/fixture.o
	$(ARM_CC) -nostdlib -no-pie -Wl,--dynamic-linker=/lib/ld-musl-armhf.so.1 \
	    -Wl,-Ttext-segment=0x02000000 -Wl,--build-id=none $(DEV_SYSROOT)/lib/crt1.o \
	    $(DEV_SYSROOT)/lib/crti.o $(@D)/fixture.o -L$(@D) -lcammy_audio_async $(DEV_LIBS) \
	    $(DEV_SYSROOT)/lib/crtn.o -o $@
