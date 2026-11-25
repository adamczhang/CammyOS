ROOT := $(abspath ../..)
OUT := $(ROOT)/build/face-service
WARN := -std=c11 -Wall -Wextra -Werror -Wformat=2 -Wshadow -Wconversion
.PHONY: host
host:
	mkdir -p $(OUT)
	$(CC) -O2 $(WARN) -fPIC -shared face_service.c ipc_file.c -o $(OUT)/libface_core.so
	$(CC) -O2 $(WARN) -pthread face_service.c core_fixture.c -o $(OUT)/core-fixture
asan:
	mkdir -p $(OUT)
	$(CC) -O1 -g $(WARN) -pthread -fsanitize=address,undefined -fno-omit-frame-pointer face_service.c core_fixture.c -o $(OUT)/core-asan
arm:
	mkdir -p $(OUT)
	$(ROOT)/build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc -Os -static -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard $(WARN) -pthread face_service.c core_fixture.c -o $(OUT)/core-arm
ARM := $(ROOT)/build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc
SYSROOT := $(ROOT)/build/native-loader/sysroot-1.1.24
DEVROOT := $(ROOT)/build/native-loader/device-rootfs
GCC_INC := $(shell $(ARM) -print-file-name=include)
LIBGCC := $(shell $(ARM) -print-libgcc-file-name)
FLAGS := -Os -marm -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard -nostdinc -isystem $(SYSROOT)/include -isystem $(GCC_INC) -fno-stack-protector -U_FORTIFY_SOURCE -fno-unwind-tables -fno-asynchronous-unwind-tables -fPIC -fvisibility=hidden
.PHONY: assets
assets:
	mkdir -p $(OUT)
	cd $(ROOT) && python3 -m tools.build_face_service_assets > build/face-service/asset-build.log
device: $(OUT)/libcammy_face_service.so
$(OUT)/frames.S $(OUT)/frame_indexes.h &: $(ROOT)/tools/build_face_service_assets.py $(ROOT)/tools/cammy_pack.py $(ROOT)/tools/fin_fur_art.py $(ROOT)/host/cammy_face.py Makefile $(wildcard $(ROOT)/assets/faces/*)
	$(MAKE) assets
$(OUT)/libcammy_face_service.so: Makefile native_service.c face_service.c face_service.h face_pack_codec.h ../ui/cammy_ui_page.h ipc_file.c ipc_file.h $(OUT)/frames.S $(OUT)/frame_indexes.h
	mkdir -p $(OUT)
	$(ARM) $(FLAGS) $(WARN) -I$(OUT) -c native_service.c -o $(OUT)/native.o
	$(ARM) $(FLAGS) $(WARN) -c face_service.c -o $(OUT)/core-device.o
	$(ARM) $(FLAGS) $(WARN) -c ipc_file.c -o $(OUT)/ipc-device.o
	$(ARM) -c $(OUT)/frames.S -o $(OUT)/frames.o
	$(ARM) -s -shared -nostdlib -Wl,-soname,libcammy_face_service.so -Wl,-z,defs,-z,now,-z,relro,-z,noexecstack,--hash-style=both,--build-id=none,--version-script,../face/exports.map $(OUT)/native.o $(OUT)/core-device.o $(OUT)/ipc-device.o $(OUT)/frames.o -L$(DEVROOT)/lib -lc $(LIBGCC) -o $@
pack-fixture:
	mkdir -p $(OUT)
	$(CC) -O2 $(WARN) pack_fixture.c -o $(OUT)/pack-fixture
	$(CC) -O1 -g $(WARN) -fsanitize=address,undefined -fno-omit-frame-pointer pack_fixture.c -o $(OUT)/pack-asan
fixture:
	mkdir -p $(OUT)
	$(CC) -O2 $(WARN) face_service.c ipc_file.c gui_fixture.c -o $(OUT)/gui-fixture
binding:
	mkdir -p $(OUT)
	$(ARM) $(FLAGS) $(WARN) -c binding_fixture.c -o $(OUT)/binding-fixture.o
	$(ARM) -nostdlib -no-pie -Wl,-E,-Ttext-segment=0x02000000,--dynamic-linker=/lib/ld-musl-armhf.so.1,--build-id=none $(SYSROOT)/lib/crt1.o $(SYSROOT)/lib/crti.o $(OUT)/binding-fixture.o $(OUT)/core-device.o $(OUT)/ipc-device.o -L$(DEVROOT)/lib -lc $(LIBGCC) $(SYSROOT)/lib/crtn.o -o $(OUT)/binding-fixture
status-tool:
	mkdir -p $(OUT)
	$(CC) -O2 $(WARN) status_main.c -o $(OUT)/face-status-host
	$(ARM) $(ROOT)/build/arm/arm_legacy_stat.o -Os -static -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard $(WARN) status_main.c -o $(OUT)/face-status
