# SPDX-License-Identifier: MIT
# WP2A device-ABI loader proof. Outputs go to build/native-loader/ (git-ignored).
#   native/loader/fetch_musl_headers.sh   one-time: pinned musl 1.1.24 headers + crt (verified)
#   make -C native/loader device          release + hooks libraries, synthetic host, stub fixture
#   make -C native/loader time64-control  same library with the musl 1.2.5 toolchain (must NOT load)
#   make -C native/loader host-asan host-tsan   build-host sanitizer builds of library + host
# The release library links against the SAVED camera libc.so (never a second libc)
# with musl 1.1.24 headers. No download or install happens in this Makefile.
ROOT := $(abspath ../..)
OUT := $(ROOT)/build/native-loader
BRIDGE := $(ROOT)/native/bridge
DEVICE_ROOTFS_SRC := $(ROOT)/../Cammy Bot/M105 investigation/work/firmware-rootfs
DEVROOT := $(OUT)/device-rootfs
SYSROOT := $(OUT)/sysroot-1.1.24
ARM_PREFIX := $(ROOT)/build/toolchain/bin/arm-buildroot-linux-musleabihf-
ARM_CC := $(ARM_PREFIX)gcc
GCC_INC := $(shell $(ARM_CC) -print-file-name=include 2>/dev/null)
LIBGCC := $(shell $(ARM_CC) -print-libgcc-file-name 2>/dev/null)
CC ?= cc

WARN := -std=c11 -Wall -Wextra -Werror -Wformat=2 -Wshadow -Wconversion -Wsign-conversion \
        -Wmissing-prototypes -Wstrict-prototypes -Wvla -Wundef -Wcast-qual -Wpointer-arith
CORE := $(BRIDGE)/cammy_bridge.c
LIBSRC := cammy_native_status.c
HDRS := cammy_native_host.h $(BRIDGE)/cammy_bridge.h $(BRIDGE)/cammy_bridge_backend.h $(BRIDGE)/cammy_bridge_proto.h
# Device profile: ARMv7-A, hard-float EABI5, VFPv3 (subset of what sdvcam/libc declare),
# musl 1.1.24 headers only, no stack protector/fortify/unwind tables, hidden by default.
DEV_ARCH := -marm -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard
DEV_CFLAGS := -Os $(DEV_ARCH) -nostdinc -isystem $(SYSROOT)/include -isystem $(GCC_INC) \
        -fno-stack-protector -U_FORTIFY_SOURCE -fno-exceptions -fno-unwind-tables \
        -fno-asynchronous-unwind-tables -fno-common -DCNS_DEVICE_ABI
LIB_CFLAGS := $(DEV_CFLAGS) -fPIC -fvisibility=hidden
LIB_LDFLAGS := -shared -nostdlib -Wl,-soname,libcammy_native_status.so -Wl,-z,now -Wl,-z,relro \
        -Wl,-z,noexecstack -Wl,-z,defs -Wl,--hash-style=both -Wl,--exclude-libs,ALL -Wl,--build-id=none
DEV_LIBS := -L$(DEVROOT)/lib -lc $(LIBGCC)
EXE_LDFLAGS := -nostdlib -no-pie -Wl,--dynamic-linker=/lib/ld-musl-armhf.so.1 -Wl,-z,now \
        -Wl,-Ttext-segment=0x02000000 -Wl,--build-id=none

.PHONY: all device release hooks time64-control host-asan host-tsan clean
all: device
device: release hooks $(OUT)/arm/synth_host $(OUT)/arm/stub_fixture
release: $(OUT)/release/libcammy_native_status.so
hooks: $(OUT)/hooks/libcammy_native_status.so
time64-control: $(OUT)/time64-control/libcammy_native_status.so
host-asan: $(OUT)/host-asan/libcammy_native_status.so $(OUT)/host-asan/synth_host
host-tsan: $(OUT)/host-tsan/libcammy_native_status.so $(OUT)/host-tsan/synth_host

$(SYSROOT)/include/bits/alltypes.h:
	@echo 'musl 1.1.24 sysroot missing: run native/loader/fetch_musl_headers.sh (pinned, verified)'; exit 1

$(DEVROOT)/lib/libc.so:
	@test -f "$(DEVICE_ROOTFS_SRC)/lib/libc.so" || { echo 'saved camera rootfs not found'; exit 1; }
	mkdir -p $(OUT)
	ln -sfn "$(DEVICE_ROOTFS_SRC)" $(DEVROOT)

DEV_DEPS := $(SYSROOT)/include/bits/alltypes.h $(DEVROOT)/lib/libc.so

$(OUT)/release/libcammy_native_status.so: $(LIBSRC) $(CORE) $(HDRS) exports.map $(DEV_DEPS)
	mkdir -p $(@D)
	$(ARM_CC) $(LIB_CFLAGS) $(WARN) -c $(CORE) -o $(@D)/cammy_bridge.o
	$(ARM_CC) $(LIB_CFLAGS) $(WARN) -c $(LIBSRC) -o $(@D)/cammy_native_status.o
	$(ARM_CC) $(LIB_LDFLAGS) -Wl,--version-script,exports.map -Wl,-Map,$@.map $(@D)/cammy_native_status.o \
	    $(@D)/cammy_bridge.o $(DEV_LIBS) -o $@

$(OUT)/hooks/libcammy_native_status.so: $(LIBSRC) $(CORE) $(HDRS) exports-hooks.map $(DEV_DEPS)
	mkdir -p $(@D)
	$(ARM_CC) $(LIB_CFLAGS) $(WARN) -c $(CORE) -o $(@D)/cammy_bridge.o
	$(ARM_CC) $(LIB_CFLAGS) $(WARN) -DCNS_TEST_HOOKS -c $(LIBSRC) -o $(@D)/cammy_native_status.o
	$(ARM_CC) $(LIB_LDFLAGS) -Wl,--version-script,exports-hooks.map -Wl,-Map,$@.map $(@D)/cammy_native_status.o \
	    $(@D)/cammy_bridge.o $(DEV_LIBS) -o $@

# Negative control: the ordinary musl 1.2.5/time64 toolchain and sysroot.
$(OUT)/time64-control/libcammy_native_status.so: $(LIBSRC) $(CORE) $(HDRS)
	mkdir -p $(@D)
	$(ARM_CC) -Os $(DEV_ARCH) -fPIC -fvisibility=hidden -shared $(WARN) $(CORE) $(LIBSRC) -o $@

$(OUT)/arm/synth_host: synth_host.c cammy_native_host.h $(DEV_DEPS)
	mkdir -p $(@D)
	$(ARM_CC) $(DEV_CFLAGS) $(WARN) -c synth_host.c -o $(@D)/synth_host.o
	$(ARM_CC) $(EXE_LDFLAGS) $(SYSROOT)/lib/crt1.o $(SYSROOT)/lib/crti.o $(@D)/synth_host.o $(DEV_LIBS) \
	    $(SYSROOT)/lib/crtn.o -o $@

$(OUT)/arm/stub_fixture: stub_fixture.c stub_fixture_asm.S cammy_native_host.h $(DEV_DEPS)
	mkdir -p $(@D)
	$(ARM_CC) $(DEV_CFLAGS) $(WARN) -c stub_fixture.c -o $(@D)/stub_fixture.o
	$(ARM_CC) $(DEV_ARCH) -c stub_fixture_asm.S -o $(@D)/stub_fixture_asm.o
	$(ARM_CC) $(EXE_LDFLAGS) $(SYSROOT)/lib/crt1.o $(SYSROOT)/lib/crti.o $(@D)/stub_fixture.o \
	    $(@D)/stub_fixture_asm.o $(DEV_LIBS) $(SYSROOT)/lib/crtn.o -o $@

# Build-host sanitizer builds (glibc, x86-64): same library logic with test hooks,
# socket under build/ so no namespace is needed. Not an ABI proof.
HOST_SOCK = $(OUT)/hrun-$(1)/bridge.sock
$(OUT)/host-%/libcammy_native_status.so: $(LIBSRC) $(CORE) $(HDRS)
	mkdir -p $(@D) $(OUT)/hrun-$*
	chmod 700 $(OUT)/hrun-$*
	$(CC) -O1 -g -fPIC -shared -fvisibility=hidden -fno-omit-frame-pointer -pthread \
	    -fsanitize=$(if $(filter asan,$*),address$(comma)undefined,thread) -fno-sanitize-recover=all \
	    $(WARN) -DCNS_TEST_HOOKS -DCNS_SOCKET_PATH='"$(call HOST_SOCK,$*)"' $(CORE) $(LIBSRC) -o $@

$(OUT)/host-%/synth_host: synth_host.c cammy_native_host.h
	mkdir -p $(@D)
	$(CC) -O1 -g -fno-omit-frame-pointer -pthread \
	    -fsanitize=$(if $(filter asan,$*),address$(comma)undefined,thread) -fno-sanitize-recover=all \
	    $(WARN) synth_host.c -ldl -o $@

comma := ,

clean:
	rm -rf $(OUT)/release $(OUT)/hooks $(OUT)/time64-control $(OUT)/arm $(OUT)/host-asan $(OUT)/host-tsan
