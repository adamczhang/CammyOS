ROOT := ../..
UP := $(ROOT)/downloads/stackchan-face-editor/lib/ParamFace
OUT := $(ROOT)/build/paramface
SOURCES := cammy_paramface.cpp $(OUT)/ParamFace-cammy.cpp $(UP)/src/Renderer.cpp
FLAGS := -std=c++17 -O2 -Wall -Wextra -Werror -I. -I$(OUT)/include -I$(UP)/src
ARM := $(ROOT)/build/toolchain/bin/arm-buildroot-linux-musleabihf-g++
.PHONY: prepare host arm asan
prepare:
	python3 $(ROOT)/tools/paramface_source.py
host: prepare
	$(CXX) $(FLAGS) -fPIC -shared $(SOURCES) -o $(OUT)/libcammy_paramface.so.new
	mv $(OUT)/libcammy_paramface.so.new $(OUT)/libcammy_paramface.so
	$(CXX) $(FLAGS) $(SOURCES) render_cli.cpp -o $(OUT)/render-host
	$(CXX) -I$(UP)/host $(FLAGS) cammy_paramface.cpp $(UP)/src/ParamFace.cpp $(UP)/src/Renderer.cpp render_cli.cpp -o $(OUT)/render-upstream-shim
arm: prepare
	$(ARM) $(FLAGS) -static -march=armv7-a -mfpu=neon-vfpv4 -mfloat-abi=hard $(SOURCES) render_cli.cpp $(ROOT)/build/arm/arm_legacy_stat.o -o $(OUT)/render-arm
asan: prepare
	$(CXX) $(filter-out -O2,$(FLAGS)) -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -Wno-error=maybe-uninitialized -c $(OUT)/ParamFace-cammy.cpp -o $(OUT)/vendor-asan.o
	$(CXX) $(filter-out -O2,$(FLAGS)) -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer cammy_paramface.cpp $(UP)/src/Renderer.cpp render_cli.cpp $(OUT)/vendor-asan.o -o $(OUT)/render-asan

.PHONY: metadata
metadata: ../../build/paramface/fb-layout-arm
../../build/paramface/fb-layout-arm: fb_layout.c ../../build/arm/arm_legacy_stat.o
	mkdir -p $(@D)
	../../build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc -Os -static -march=armv7-a -mfpu=neon-vfpv4 -mfloat-abi=hard -std=c11 -Wall -Wextra -Werror -Wformat=2 -Wconversion $^ -o $@
