CC ?= cc
CFLAGS ?= -O2 -std=c11 -Wall -Wextra -Werror -Wformat=2 -Wshadow -Wconversion
.PHONY: all native arm tools test test-arm-local image clean
all: native
native: build/host/cammy-portal
build/host/cammy-portal: src/cammy_portal.c src/cammy_probes.h src/cammy_preview.h src/cammy_audio_stream.h src/cammy_face_control.h src/cammy_input_guardian.h src/cammy_media_prepare.h src/cammy_speaker.h native/speaker_probe/speaker_stream.h native/face_service/face_service.c native/face_service/face_service.h native/face_service/ipc_file.c native/face_service/ipc_file.h native/ui/cammy_ui_page.h
	mkdir -p build/host
	$(CC) $(CFLAGS) $< -o $@
arm:
	bash scripts/build-arm.sh
tools: arm
	bash scripts/build-tools.sh
test: native
	python3 -m unittest discover -s tests -v
test-arm-local:
	python3 tools/test_arm_local.py
image: tools
	python3 scripts/build-fork.py
clean:
	@echo 'Build artifacts are under build/ and dist/; remove explicitly when appropriate.'

.PHONY: face-lab test-face-lab
REACHY_PYTHON ?= $(abspath ../Reachy-Mini-Control/runtime/venv/bin/python)
face-lab:
	python3 tools/prepare_face_lab.py --python "$(REACHY_PYTHON)"
test-face-lab:
	"$(REACHY_PYTHON)" -m unittest tests.test_paramface tests.test_cammy_face_app tests.test_cammy_live

.PHONY: webrtc-profile test-reachy-media
webrtc-profile:
	python3 tools/prepare_webrtc.py
test-reachy-media:
	python3 tools/with_webrtc.py "$(REACHY_PYTHON)" -m unittest tests.test_paramface tests.test_cammy_face_app tests.test_cammy_live tests.test_cammy_signalling tests.test_cammy_webrtc tests.test_native_image_abi tests.test_cammy_conversation

.PHONY: test-native-face
test-native-face:
	$(MAKE) -C native/paramface host
	"$(REACHY_PYTHON)" -m unittest tests.test_native_face tests.test_native_image_abi

.PHONY: test-rc06
test-rc06: native
	$(REACHY_PYTHON) -m unittest tests.test_portal tests.test_host tests.test_audio_stream tests.test_preview_readiness tests.test_face_service tests.test_input_guardian tests.test_native_media_boot tests.test_managed_media tests.test_rc06_runtime tests.test_cammy_control tests.test_cammy_live tests.test_cammy_face_app tests.test_cammy_conversation tests.test_companion tests.test_rc06_package
