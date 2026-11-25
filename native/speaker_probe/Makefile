ROOT := $(abspath ../..)
OUT := $(ROOT)/build/speaker-probe
ARM := $(ROOT)/build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc
.PHONY: arm
arm:
	mkdir -p $(OUT)
	$(ARM) -Os -static -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard -std=c11 -Wall -Wextra -Werror -Wformat=2 -Wshadow -Wconversion guardian.c $(ROOT)/build/arm/arm_legacy_stat.o -o $(OUT)/speaker-probe-arm

SYSROOT := $(ROOT)/build/native-loader/sysroot-1.1.24
SAVED := $(ROOT)/../Cammy Bot/M105 investigation/work/firmware-rootfs
GCCINC := $(shell $(ARM) -print-file-name=include)
.PHONY: config-tools
config-tools:
	mkdir -p $(OUT)
	$(CC) -O2 -std=c11 -Wall -Wextra -Werror config_inspector.c -Wl,-l:libasound.so.2 -o $(OUT)/config-host
	$(ARM) -Os -std=c11 -Wall -Wextra -Werror -marm -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard -nostdinc -isystem $(SYSROOT)/include -isystem $(GCCINC) -c config_inspector.c -o $(OUT)/config.o
	$(ARM) -nostdlib -no-pie -Wl,--dynamic-linker=/lib/ld-musl-armhf.so.1,--build-id=none $(SYSROOT)/lib/crt1.o $(SYSROOT)/lib/crti.o $(OUT)/config.o -L"$(SAVED)/usr/lib" -l:libasound.so.2.0.0 -L"$(SAVED)/lib" -lc -lgcc_s $(SYSROOT)/lib/crtn.o -o $(OUT)/config-arm
