ROOT := $(abspath ../..)
OUT := $(ROOT)/build/cammy-ui
WARN := -std=c11 -Wall -Wextra -Werror -Wformat=2 -Wshadow -Wconversion -D_DEFAULT_SOURCE
ARM := $(ROOT)/build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc
ARMFLAGS := -Os -static -march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=hard
SRC := ui_draw.c ui_app.c $(OUT)/ui_assets.c
.PHONY: all assets fixture asan arm
all: fixture asan arm
$(OUT)/ui_assets.c: $(ROOT)/tools/build_ui_assets.py
	cd $(ROOT) && python3 -m tools.build_ui_assets
assets: $(OUT)/ui_assets.c
fixture: $(OUT)/ui_assets.c
	$(CC) -O2 $(WARN) -I. $(SRC) ui_fixture.c -o $(OUT)/ui-fixture
	$(CC) -O1 -g $(WARN) -I. -fsanitize=address,undefined $(SRC) ui_backend.c backend_fixture.c -o $(OUT)/backend-fixture
asan: $(OUT)/ui_assets.c
	$(CC) -O1 -g $(WARN) -I. -fsanitize=address,undefined -fno-omit-frame-pointer $(SRC) ui_fixture.c -o $(OUT)/ui-asan
arm: $(OUT)/ui_assets.c
	# arm_legacy_stat.o: musl time64 stat/fstat return EINVAL on the camera kernel (2026-11-16)
	$(ARM) $(ROOT)/build/arm/arm_legacy_stat.o $(ARMFLAGS) $(WARN) -I. -I../face_service $(SRC) ui_backend.c cammy_ui.c -o $(OUT)/cammy-ui-arm
	$(ARM) $(ARMFLAGS) $(WARN) -I. $(SRC) ui_fixture.c -o $(OUT)/ui-fixture-arm
