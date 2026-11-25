/* SPDX-License-Identifier: MIT
 * Standalone status-bridge daemon for host and ARM fixtures (WP2). Signal
 * handling, umask and stdio live here, never in the embeddable core.
 *
 * cammy-bridge --socket /abs/private-dir/bridge.sock --backend fake|native
 *              [--max-clients 1..8] [--idle-timeout-ms 100..60000]
 *              [--allow-uid UID] [--boot-id-file PATH]
 */
#define _GNU_SOURCE
#include "cammy_bridge.h"
#include "cammy_bridge_proto.h"

#include <errno.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static volatile sig_atomic_t stop_requested;

static void on_stop(int sig) {
    (void)sig;
    stop_requested = 1;
}

static int parse_uint(const char *s, unsigned long min, unsigned long max, unsigned long *out) {
    if (!s || !*s || *s == '-' || *s == '+') return -1;
    char *end = NULL;
    errno = 0;
    unsigned long v = strtoul(s, &end, 10);
    if (errno || !end || *end || v < min || v > max) return -1;
    *out = v;
    return 0;
}

static int usage(void) {
    fputs("usage: cammy-bridge --socket ABS_PATH --backend fake|native [--max-clients 1..8]\n"
          "                    [--idle-timeout-ms 100..60000] [--allow-uid UID] [--boot-id-file PATH]\n",
          stderr);
    return 2;
}

int main(int argc, char **argv) {
    struct cammy_bridge_config cfg = {
        .socket_path = NULL,
        .boot_id_path = NULL,
        .backend_kind = CB_BACKEND_NONE,
        .max_clients = CB_DEFAULT_MAX_CLIENTS,
        .idle_timeout_ms = CB_DEFAULT_IDLE_TIMEOUT_MS,
        .allowed_uid = geteuid(),
    };
    for (int i = 1; i < argc; i++) {
        const char *opt = argv[i], *val = i + 1 < argc ? argv[i + 1] : NULL;
        unsigned long v;
        if (!val) return usage();
        if (!strcmp(opt, "--socket")) cfg.socket_path = val;
        else if (!strcmp(opt, "--boot-id-file")) cfg.boot_id_path = val;
        else if (!strcmp(opt, "--backend")) {
            if (!strcmp(val, "fake")) cfg.backend_kind = CB_BACKEND_FAKE;
            else if (!strcmp(val, "native")) cfg.backend_kind = CB_BACKEND_NATIVE;
            else return usage();
        } else if (!strcmp(opt, "--max-clients")) {
            if (parse_uint(val, 1, CB_MAX_CLIENTS_LIMIT, &v)) return usage();
            cfg.max_clients = (unsigned)v;
        } else if (!strcmp(opt, "--idle-timeout-ms")) {
            if (parse_uint(val, CB_MIN_IDLE_TIMEOUT_MS, CB_MAX_IDLE_TIMEOUT_MS, &v)) return usage();
            cfg.idle_timeout_ms = (unsigned)v;
        } else if (!strcmp(opt, "--allow-uid")) {
            if (parse_uint(val, 0, UINT_MAX - 1, &v)) return usage();
            cfg.allowed_uid = (uid_t)v;
        } else return usage();
        i++;
    }
    if (!cfg.socket_path || cfg.backend_kind == CB_BACKEND_NONE) return usage();

    umask(077);
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_stop; /* no SA_RESTART: poll returns EINTR promptly */
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);
    signal(SIGPIPE, SIG_IGN);

    const struct cammy_bridge_backend *backend =
        cfg.backend_kind == CB_BACKEND_FAKE ? cammy_bridge_fake_backend() : cammy_bridge_native_backend();
    enum cammy_bridge_open_error err = CB_OPEN_OK;
    struct cammy_bridge *b = cammy_bridge_open(&cfg, backend, &err);
    if (!b) {
        fprintf(stderr, "cammy-bridge: open failed: %s\n", cammy_bridge_open_error_name(err));
        switch (err) {
        case CB_OPEN_BAD_CONFIG: return 2;
        case CB_OPEN_UNSAFE_DIR: return 3;
        case CB_OPEN_ALREADY_RUNNING: return 4;
        case CB_OPEN_PATH_OCCUPIED: return 5;
        default: return 1;
        }
    }
    printf("{\"event\":\"ready\",\"bridge_version\":\"%s\",\"protocol\":\"%u.%u\",\"backend\":\"%s\","
           "\"instance\":\"%016llx\",\"pid\":%ld}\n",
           CAMMY_BRIDGE_VERSION, CB_PROTO_MAJOR, CB_PROTO_MINOR,
           cfg.backend_kind == CB_BACKEND_FAKE ? "fake" : "native",
           (unsigned long long)cammy_bridge_instance(b), (long)getpid());
    fflush(stdout);
    int rc = 0;
    while (!stop_requested) {
        if (cammy_bridge_step(b, 250) != 0) {
            fputs("cammy-bridge: listener failed\n", stderr);
            rc = 1;
            break;
        }
    }
    cammy_bridge_close(b);
    printf("{\"event\":\"stopped\",\"code\":%d}\n", rc);
    fflush(stdout);
    return rc;
}
