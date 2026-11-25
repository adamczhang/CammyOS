ROOT := $(abspath ../..)
OUT := $(ROOT)/build/native-face
SYSROOT := $(ROOT)/build/native-loader/sysroot-1.1.24
DEVROOT := $(ROOT)/build/native-loader/device-rootfs
ARM := $(ROOT)/build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc
GCC_INC := $(shell $(ARM) -print-file-name=include)
LIBGCC := $(shell $(ARM) -print-libgcc-file-name)
WARN := -std=c11 -Wall -Wextra -Werror -Wformat=2 -Wshadow -Wconversion
FLAGS := -Os -marm -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard -nostdinc -isystem $(SYSROOT)/include -isystem $(GCC_INC) -fno-stack-protector -U_FORTIFY_SOURCE -fno-unwind-tables -fno-asynchronous-unwind-tables -fPIC -fvisibility=hidden
.PHONY: device host asset arm-fixture asan binding-fixture
device: $(OUT)/libcammy_native_face.so
asset:
	cd $(ROOT) && python3 -m tools.face_trial_asset
$(OUT)/trial_pixels.h:
	$(MAKE) asset
$(OUT)/libcammy_native_face.so: native_face.c cammy_face_surface.c cammy_face_surface.h cammy_face_host.h exports.map $(OUT)/trial_pixels.h
	mkdir -p $(OUT)
	$(ARM) $(FLAGS) $(WARN) -I$(OUT) -c native_face.c -o $(OUT)/native_face.o
	$(ARM) $(FLAGS) $(WARN) -c cammy_face_surface.c -o $(OUT)/surface-device.o
	$(ARM) -shared -nostdlib -Wl,-soname,libcammy_native_face.so -Wl,-z,defs,-z,now,-z,relro,-z,noexecstack,--hash-style=both,--build-id=none,--version-script,exports.map $(OUT)/native_face.o $(OUT)/surface-device.o -L$(DEVROOT)/lib -lc $(LIBGCC) -o $@
host:
	mkdir -p $(OUT)
	$(CC) -O2 $(WARN) -fPIC -shared cammy_face_surface.c -o $(OUT)/libsurface.so
	$(CC) -O2 $(WARN) cammy_face_surface.c surface_fixture.c -o $(OUT)/surface-host
arm-fixture:
	mkdir -p $(OUT)
	$(ARM) -Os -static -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard $(WARN) cammy_face_surface.c surface_fixture.c -o $(OUT)/surface-arm
asan:
	mkdir -p $(OUT)
	$(CC) -O1 -g $(WARN) -fno-omit-frame-pointer -fsanitize=address,undefined -fno-sanitize-recover=all cammy_face_surface.c surface_fixture.c -o $(OUT)/surface-asan
binding-fixture:
	mkdir -p $(OUT)
	$(ARM) $(FLAGS) $(WARN) -c binding_fixture.c -o $(OUT)/binding_fixture.o
	$(ARM) -nostdlib -no-pie -Wl,-E,-Ttext-segment=0x02000000,--dynamic-linker=/lib/ld-musl-armhf.so.1,--build-id=none $(SYSROOT)/lib/crt1.o $(SYSROOT)/lib/crti.o $(OUT)/binding_fixture.o -L$(DEVROOT)/lib -lc $(LIBGCC) $(SYSROOT)/lib/crtn.o -o $(OUT)/binding-fixture
