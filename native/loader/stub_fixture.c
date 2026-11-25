/* SPDX-License-Identifier: MIT
 * WP2A ARM stub fixture. NOT sdvcam and not a boot: it maps the candidate's
 * exact patched word at 0x31DB0 and its exact cave bytes at 0x8E2E1C..0x8E3000
 * into a synthetic process, with trampolines at the dlopen/dlsym PLT addresses,
 * at 0xA4294 (fake original) and at 0x31DB4 (register probe).
 *
 *   stub_fixture <candidate> inert <expected-library-path>
 *       dlopen/dlsym/attach/detach are inert fakes; seven register-level cases.
 *   stub_fixture <candidate> real
 *       dlopen/dlsym are the SAVED camera musl's; attach/detach are the staged
 *       library's (or the fallback if it is absent/unloadable). The fake
 *       original blocks until the driver sends "close", like the queue run.
 */
#define _GNU_SOURCE
#include "cammy_native_host.h"

#include <dlfcn.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define CALL_SITE 0x31DB0u
#define RESUME 0x31DB4u
#define ORIGINAL 0xA4294u
#define DLOPEN_PLT 0x2CA70u
#define DLSYM_PLT 0x2D574u
#define CAVE_VA 0x8E2E1Cu
#define CAVE_FILE 0x8D2E1Cu
#define CAVE_LIMIT 0x8E3000u
#define STUB_VA 0x8E2E40u
#define LDR_PC_PC_M4 0xE51FF004u /* ldr pc, [pc, #-4] */

/* Shared with stub_fixture_asm.S. */
uint32_t site_in[12], probe_out[12], probe_sp, orig_in[6], driver_sp, main_sp;
uint64_t site_din[8], probe_dout[8];
void run_site(void);
void probe_entry(void);
void fake_original(void);
void wrap_dlopen(void);
void wrap_dlsym(void);
void wrap_attach(void);
void wrap_detach(void);
uint64_t fake_original_c(void);
void *fake_dlopen_c(const char *path, int flags);
void *fake_dlsym_c(void *handle, const char *name);
int fake_attach_c(const struct cammy_native_host_v1 *host);
void fake_detach_c(uint32_t reason);

static const uint8_t SRC_SHA[32] = {
    0x87, 0x71, 0x96, 0x1f, 0xdb, 0xa8, 0x92, 0x80, 0x57, 0x8c, 0xe5, 0x48, 0x2a, 0x6c, 0xbe, 0x4e,
    0xc3, 0xf6, 0xec, 0x20, 0x50, 0x68, 0x94, 0x4b, 0x34, 0xf9, 0x44, 0x63, 0x7d, 0x62, 0xb0, 0xec};

#define STACK_WORDS (64 * 1024)
#define CANARY_WORDS 64
static uint32_t main_stack[STACK_WORDS] __attribute__((aligned(16)));

static struct {
    int real;
    void *ret_dlopen;
    void *ret_attach_sym, *ret_detach_sym;
    int ret_attach;
    uint32_t ret_r0, ret_r1;
    int seq, s_dlopen, s_dlsym[2], s_attach, s_orig, s_detach;
    int n_dlopen, n_dlsym, n_attach, n_orig, n_detach;
    char path[256], names[2][48];
    int flags;
    void *sym_handle[2];
    struct cammy_native_host_v1 host;
    uintptr_t host_addr;
    uint32_t detach_reason;
} R;

void *fake_dlopen_c(const char *path, int flags) {
    R.s_dlopen = ++R.seq;
    R.n_dlopen++;
    snprintf(R.path, sizeof R.path, "%s", path);
    R.flags = flags;
    return R.ret_dlopen;
}

void *fake_dlsym_c(void *handle, const char *name) {
    int i = R.n_dlsym < 2 ? R.n_dlsym : 1;
    R.s_dlsym[i] = ++R.seq;
    R.n_dlsym++;
    R.sym_handle[i] = handle;
    snprintf(R.names[i], sizeof R.names[i], "%s", name);
    if (!strcmp(name, "cammy_native_attach_v1")) return R.ret_attach_sym;
    if (!strcmp(name, "cammy_native_detach_v1")) return R.ret_detach_sym;
    return (void *)0xDEAD;
}

int fake_attach_c(const struct cammy_native_host_v1 *host) {
    R.s_attach = ++R.seq;
    R.n_attach++;
    R.host_addr = (uintptr_t)host;
    memcpy(&R.host, host, sizeof R.host);
    return R.ret_attach;
}

void fake_detach_c(uint32_t reason) {
    R.s_detach = ++R.seq;
    R.n_detach++;
    R.detach_reason = reason;
}

uint64_t fake_original_c(void) {
    R.s_orig = ++R.seq;
    R.n_orig++;
    if (R.real) {
        printf("{\"event\":\"original-running\"}\n");
        fflush(stdout);
        char line[32];
        if (!fgets(line, sizeof line, stdin) || strcmp(line, "close\n") != 0) exit(9);
    }
    return ((uint64_t)R.ret_r1 << 32) | R.ret_r0;
}

static int map_fixed(uintptr_t start, size_t len) {
    void *p = mmap((void *)start, len, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
    return p == (void *)start ? 0 : -1;
}

static void put32(uintptr_t va, uint32_t v) { memcpy((void *)va, &v, 4); }

static void trampoline(uintptr_t va, void *target) {
    put32(va, LDR_PC_PC_M4);
    put32(va + 4, (uint32_t)(uintptr_t)target);
}

static int seal(uintptr_t start, size_t len) {
    __builtin___clear_cache((char *)start, (char *)(start + len));
    return mprotect((void *)start, len, PROT_READ | PROT_EXEC);
}

/* Lays out the candidate bytes and trampolines at their native addresses. */
static int install(const char *candidate, void *dlopen_target, void *dlsym_target) {
    int fd = open(candidate, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    uint32_t site;
    static unsigned char cave[CAVE_LIMIT - CAVE_VA];
    if (pread(fd, &site, 4, CALL_SITE - 0x10000u) != 4 ||
        pread(fd, cave, sizeof cave, CAVE_FILE) != (ssize_t)sizeof cave) { close(fd); return -1; }
    close(fd);
    if (map_fixed(0x2C000, 0x2000) || map_fixed(0x31000, 0x1000) || map_fixed(0xA4000, 0x1000) ||
        map_fixed(0x8E2000, 0x1000)) return -2;
    trampoline(DLOPEN_PLT, dlopen_target);
    trampoline(DLSYM_PLT, dlsym_target);
    put32(CALL_SITE, site);                     /* the candidate's own patched bl */
    trampoline(RESUME, (void *)probe_entry);
    trampoline(ORIGINAL, (void *)fake_original);
    memcpy((void *)CAVE_VA, cave, sizeof cave); /* the candidate's exact cave bytes */
    if (seal(0x2C000, 0x2000) || seal(0x31000, 0x1000) || seal(0xA4000, 0x1000) || seal(0x8E2000, 0x1000)) return -3;
    return 0;
}

static void reset(uint32_t salt) {
    int real = R.real;
    memset(&R, 0, sizeof R);
    R.real = real;
    for (unsigned i = 0; i < 12; i++) site_in[i] = 0x10101010u * (i + 1) ^ salt;
    for (unsigned i = 0; i < 8; i++) site_din[i] = 0x0123456789ABCDEFull * (i + 3) ^ salt;
    memset(probe_out, 0, sizeof probe_out);
    memset(probe_dout, 0, sizeof probe_dout);
    memset(orig_in, 0, sizeof orig_in);
    probe_sp = 0;
    /* Entry sp is 8-aligned; the words above it stand for main's frame. */
    main_sp = (uint32_t)(uintptr_t)&main_stack[STACK_WORDS - CANARY_WORDS];
    for (unsigned i = 0; i < CANARY_WORDS; i++) main_stack[STACK_WORDS - CANARY_WORDS + i] = 0x5A5A0000u | i;
    R.ret_r0 = 0x0BADF00Du ^ salt;
    R.ret_r1 = 0x12345678u ^ salt;
}

static int frame_intact(void) {
    for (unsigned i = 0; i < CANARY_WORDS; i++)
        if (main_stack[STACK_WORDS - CANARY_WORDS + i] != (0x5A5A0000u | i)) return 0;
    return 1;
}

/* Checks common to every path; returns a bit mask of failures (0 = pass). */
static unsigned check_common(int expect_attached) {
    unsigned f = 0;
    for (unsigned i = 4; i < 12; i++) if (probe_out[i] != site_in[i]) f |= 1u << 0;
    if (probe_sp != main_sp) f |= 1u << 1;
    if (memcmp(probe_dout, site_din, sizeof site_din) != 0) f |= 1u << 2;
    if (probe_out[0] != R.ret_r0 || probe_out[1] != R.ret_r1) f |= 1u << 3;
    for (unsigned i = 0; i < 4; i++) if (orig_in[i] != site_in[i]) f |= 1u << 4;
    if (R.n_orig != 1) f |= 1u << 5;
    if (!frame_intact()) f |= 1u << 6;
    if (expect_attached) {
        if (orig_in[4] != main_sp - 32 || (orig_in[4] & 7)) f |= 1u << 7;
        if (orig_in[5] <= STUB_VA || orig_in[5] >= 0x8E2F00u) f |= 1u << 8;
        if (!R.real && (R.n_detach != 1 || R.detach_reason != 1 || R.s_detach <= R.s_orig)) f |= 1u << 9;
    } else {
        if (orig_in[4] != main_sp || orig_in[5] != RESUME) f |= 1u << 10; /* exact stock tail */
        if (!R.real && R.n_detach != 0) f |= 1u << 11;
    }
    return f;
}

static int inert(const char *candidate, const char *expected_path) {
    R.real = 0;
    if (install(candidate, (void *)wrap_dlopen, (void *)wrap_dlsym) != 0) { puts("{\"event\":\"install-failed\"}"); return 3; }
    struct { const char *name; int dl_ok, attach_ok, detach_ok, attach_rc, attached; } cases[] = {
        {"dlopen-null", 0, 1, 1, 0, 0},
        {"dlsym-attach-null", 1, 0, 1, 0, 0},
        {"dlsym-detach-null", 1, 1, 0, 0, 0},
        {"attach-eproto", 1, 1, 1, -71, 0},
        {"attach-positive", 1, 1, 1, 1, 0},
        {"success", 1, 1, 1, 0, 1},
        {"success-again", 1, 1, 1, 0, 1},
    };
    int failed = 0;
    for (unsigned c = 0; c < sizeof cases / sizeof cases[0]; c++) {
        reset(0x9E3779B9u * (c + 1));
        R.ret_dlopen = cases[c].dl_ok ? (void *)0x7000F000 : NULL;
        R.ret_attach_sym = cases[c].attach_ok ? (void *)wrap_attach : NULL;
        R.ret_detach_sym = cases[c].detach_ok ? (void *)wrap_detach : NULL;
        R.ret_attach = cases[c].attach_rc;
        run_site();
        unsigned f = check_common(cases[c].attached);
        if (R.n_dlopen != 1 || strcmp(R.path, expected_path) != 0 || R.flags != 2) f |= 1u << 12;
        int want_syms = !cases[c].dl_ok ? 0 : !cases[c].attach_ok ? 1 : 2;
        if (R.n_dlsym != want_syms) f |= 1u << 13;
        if (want_syms >= 1 && (strcmp(R.names[0], "cammy_native_attach_v1") || R.sym_handle[0] != R.ret_dlopen)) f |= 1u << 14;
        if (want_syms == 2 && (strcmp(R.names[1], "cammy_native_detach_v1") || R.sym_handle[1] != R.ret_dlopen)) f |= 1u << 14;
        int want_attach = cases[c].dl_ok && cases[c].attach_ok && cases[c].detach_ok;
        if (R.n_attach != want_attach) f |= 1u << 15;
        if (want_attach) {
            if (R.host.abi_version != 1 || R.host.struct_size != 48 || R.host.profile != 1 ||
                memcmp(R.host.source_sha256, SRC_SHA, 32) != 0 ||
                (uint32_t)(uintptr_t)R.host.main_module != site_in[0]) f |= 1u << 16;
            if (R.host_addr != main_sp - 32 - 48) f |= 1u << 17; /* record lives in the stub frame */
            if (!(R.s_dlopen < R.s_dlsym[0] && R.s_dlsym[0] < R.s_dlsym[1] && R.s_dlsym[1] < R.s_attach &&
                  R.s_attach < R.s_orig)) f |= 1u << 18;
        }
        failed |= f != 0;
        printf("{\"event\":\"case\",\"name\":\"%s\",\"pass\":%s,\"fail_bits\":%u,\"orig_lr\":%u,\"orig_sp_delta\":%d,"
               "\"detach_calls\":%d,\"attach_calls\":%d}\n", cases[c].name, f ? "false" : "true", f, orig_in[5],
               (int)(main_sp - orig_in[4]), R.n_detach, R.n_attach);
    }
    fflush(stdout);
    return failed ? 1 : 0;
}

static int real(const char *candidate) {
    R.real = 1;
    if (install(candidate, (void *)dlopen, (void *)dlsym) != 0) { puts("{\"event\":\"install-failed\"}"); return 3; }
    reset(0x5bd1e995u);
    run_site();
    /* Only the original is a fake here: dlopen/dlsym/attach/detach are real. */
    unsigned f = check_common(orig_in[5] != RESUME);
    printf("{\"event\":\"real\",\"pass\":%s,\"fail_bits\":%u,\"attached_path\":%s,\"orig_lr\":%u,"
           "\"orig_sp_delta\":%d}\n", f ? "false" : "true", f, orig_in[5] != RESUME ? "true" : "false",
           orig_in[5], (int)(main_sp - orig_in[4]));
    fflush(stdout);
    return f ? 1 : 0;
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (argc >= 4 && !strcmp(argv[2], "inert")) return inert(argv[1], argv[3]);
    if (argc >= 3 && !strcmp(argv[2], "real")) return real(argv[1]);
    return 2;
}
