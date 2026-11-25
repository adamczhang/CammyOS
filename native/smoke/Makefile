.DEFAULT_GOAL := smoke
# Reuse only the frozen loader's saved-camera musl 1.1.24/time32 build profile.
include ../loader/Makefile
SMOKE_OUT := $(ROOT)/build/bridge-smoke
.PHONY: smoke
smoke: $(SMOKE_OUT)/smoke_host
$(SMOKE_OUT)/smoke_host: smoke_host.c $(ROOT)/native/loader/cammy_native_host.h $(ROOT)/native/bridge/cammy_bridge_proto.h ../loader/Makefile Makefile $(DEV_DEPS)
	mkdir -p $(@D)
	$(ARM_CC) $(DEV_CFLAGS) $(WARN) -c $< -o $(@D)/smoke_host.o
	$(ARM_CC) $(EXE_LDFLAGS) $(SYSROOT)/lib/crt1.o $(SYSROOT)/lib/crti.o $(@D)/smoke_host.o $(DEV_LIBS) $(SYSROOT)/lib/crtn.o -o $@
