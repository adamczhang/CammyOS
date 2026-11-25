# Builds build/c3/libc3aec.so: a C shim over the system WebRTC AudioProcessing 1.3
# (PipeWire's libwebrtc-audio-processing-1.so.3). No root: the -dev headers of the
# exact installed versions are unpacked under build/c3/deb by `make headers`.
ROOT   := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../../..)
OUT    := $(ROOT)/build/c3
DEB    := $(OUT)/deb
INC    := $(DEB)/x/usr/include
SYSLIB := /usr/lib/x86_64-linux-gnu/libwebrtc-audio-processing-1.so.3
CXXFLAGS := -O2 -fPIC -std=c++17 -Wall -DWEBRTC_POSIX -DWEBRTC_LINUX \
            -I$(INC)/webrtc-audio-processing-1 -I$(INC)

$(OUT)/libc3aec.so: c3aec.cc $(INC)/webrtc-audio-processing-1/modules/audio_processing/include/audio_processing.h
	g++ $(CXXFLAGS) -shared -o $@ c3aec.cc $(SYSLIB) -Wl,-rpath,/usr/lib/x86_64-linux-gnu -Wl,--no-undefined

$(INC)/webrtc-audio-processing-1/modules/audio_processing/include/audio_processing.h:
	mkdir -p $(DEB)
	cd $(DEB) && apt-get download \
	  libwebrtc-audio-processing-dev=$$(dpkg-query -W -f '$${Version}' libwebrtc-audio-processing-1-3) \
	  libabsl-dev=$$(dpkg-query -W -f '$${Version}' 'libabsl2*' | head -1)
	cd $(DEB) && for f in *.deb; do dpkg-deb -x $$f x; done

headers: $(INC)/webrtc-audio-processing-1/modules/audio_processing/include/audio_processing.h
clean:
	rm -f $(OUT)/libc3aec.so
.PHONY: headers clean
