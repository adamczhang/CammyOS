/* SPDX-License-Identifier: MIT. cammy-ui: Cammy's touch menu (0.7).
 *
 * Runs beside sdvcam as its own process, so menu logic never runs inside the camera app.
 * It publishes whole screens through CUI_FILE (cammy_ui_page.h); the face service inside
 * sdvcam only displays the published buffer. Taps come back through the face service's
 * status page (taps counter + last_tap). A tap in CammyOS mode opens the menu.
 *
 * Loading gate (ui_boot.h): the supervisor starts cammy-ui as soon as its payload is
 * verified, before the stock settle. While the face service's gate holds (CammyOS picked,
 * this boot not ready yet) cammy-ui shows the Loading screen with what is still waiting,
 * re-reading this boot's records once a second, and then reports CUI_BOOT_READY. It opens
 * no input device and polls nothing of stock's until the stock settle is over, and none at
 * all in Normal mode. Once the gate has opened, nothing here reads the records again.
 *
 * Usage: cammy-ui [--run-dir DIR] [--once-render FILE]
 */
#define CFS_CLIENT_ONLY
#include "../face_service/face_service.c"
#include "../face_service/ipc_file.c"
#include "ui_backend.h"
#include "ui_buttons.h"
#include "ui_face_boot.h"
#include "ui_tick.h"
#include <poll.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <linux/input.h>
#include <sys/ioctl.h>

static volatile sig_atomic_t stop;
static void on_signal(int s) { (void)s; stop = 1; }
static uint64_t ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000u + (uint64_t)t.tv_nsec / 1000000u;
}
/* A fresh page file each start (new inode): the face service maps it within a second. */
static struct cui_header *create_page(void) {
    char tmp[128];
    snprintf(tmp, sizeof tmp, "%s.%d", CUI_FILE, (int)getpid());
    int fd = open(tmp, O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) return NULL;
    if (ftruncate(fd, (off_t)CUI_FILE_BYTES)) { close(fd); unlink(tmp); return NULL; }
    void *m = mmap(NULL, CUI_FILE_BYTES, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (m == MAP_FAILED) { unlink(tmp); return NULL; }
    struct cui_header *h = m;
    atomic_store(&h->abi, CUI_ABI);
    atomic_store(&h->pid, (uint32_t)getpid());
    atomic_store(&h->heartbeat_ms, (uint32_t)ms());
    atomic_store(&h->boot, CUI_BOOT_LOADING);
    atomic_store_explicit(&h->magic, CUI_MAGIC, memory_order_release);
    if (rename(tmp, CUI_FILE)) { unlink(tmp); munmap(m, CUI_FILE_BYTES); return NULL; }
    return h;
}
/* Face status as the portal reads it: same boot, live GUI heartbeat. */
static int face_state(struct cfs_page **page, struct cfs_status *s) {
    if (!*page && cfs_map_file(CFS_FILE, 0, page)) return -1;
    struct cfs_page *p = *page;
    if (atomic_load_explicit(p->header, memory_order_acquire) != CFS_MAGIC || atomic_load(p->header + 1) != CFS_ABI) return -1;
    static struct cui_boot boot; /* read once per process (ui_face_boot.h) */
    if (!cui_boot_matches(&boot, p->header + 4, cfs_current_boot)) return -1;
    if (cfs_read_status(p, s)) return -1;
    uint64_t now = ms();
    return !s->gui_ms || now < s->gui_ms || now - s->gui_ms > 1000u ? -1 : 0;
}
/* The loading gate's view of the supervisor: cammy-ui is its child, so the supervisor has
 * gone exactly when the parent changes. -1 when cammy-ui was not started by it. */
static pid_t supervisor;
static void supervisor_init(void) {
    pid_t p = getppid();
    char path[48], cmd[512];
    snprintf(path, sizeof path, "/proc/%d/cmdline", (int)p);
    int fd = p > 1 ? open(path, O_RDONLY | O_CLOEXEC) : -1;
    ssize_t n = fd >= 0 ? read(fd, cmd, sizeof cmd - 1) : -1;
    if (fd >= 0) close(fd);
    if (n <= 0) return;
    for (ssize_t i = 0; i < n; i++) if (!cmd[i]) cmd[i] = ' ';
    cmd[n] = 0;
    if (strstr(cmd, "startup.sh")) supervisor = p;
}
static int supervisor_alive(void) { return supervisor ? getppid() == supervisor : -1; }
/* Buttons: event0/event1 (directional controls, OK, Settings, side circle/play, turn_180 and M). In CammyOS mode
 * cammy-ui holds both grabbed and routes each key (ui_buttons.h). It grabs and releases
 * only with no key held, so stock never sees half a press. Event times are CLOCK_MONOTONIC
 * (EVIOCSCLOCKID) so an event can be placed before or after an ungrab. */
#define BTN_DEVS 2
struct button_dev { int fd, grabbed, mono; uint32_t held, injected; uint64_t grab_ms, ungrab_ms, retry, down_ms; };
struct buttons { struct button_dev d[BTN_DEVS]; int live_view, tracking_known; };
static int key_bit(int code) {
    static const int codes[] = {UIB_KEY_LEFT, UIB_KEY_RIGHT, UIB_KEY_UP, UIB_KEY_DOWN, UIB_KEY_OK, UIB_KEY_TURN, UIB_KEY_PLAYBACK,
                                UIB_KEY_ZOOM_IN, UIB_KEY_ZOOM_OUT, UIB_KEY_MUTE, 114, UIB_KEY_MODE};
    for (int i = 0; i < (int)(sizeof codes / sizeof *codes); i++) if (codes[i] == code) return i;
    return -1;
}
/* The camera's 32-bit kernel uses 16-byte events (32-bit timeval) whatever this libc's
 * time_t is: sec@0, usec@4, type@8, code@10, value@12, little endian. */
static int key_write(int fd, int code, int32_t value) {
    struct { uint32_t sec, usec; uint16_t type, code; int32_t value; } ev[2] = {{0, 0, EV_KEY, (uint16_t)code, value}, {0, 0, EV_SYN, 0, 0}};
    _Static_assert(sizeof ev == 32, "evdev event layout");
    return write(fd, ev, sizeof ev) == (ssize_t)sizeof ev ? 0 : -1;
}
static void set_grab(struct button_dev *d, int want, uint64_t now) {
    if (want == d->grabbed) return;
    if (ioctl(d->fd, EVIOCGRAB, want ? 1 : 0)) {
        static unsigned failed;
        if (failed++ < 20u) fprintf(stderr, "key grab %d failed: errno=%d\n", want, errno);
        return;
    }
    d->grabbed = want;
    (void)now;
    if (want) d->grab_ms = ms(); else d->ungrab_ms = ms();
}
static void buttons_poll(struct buttons *b, struct ui_app *app, struct cfs_page *face,
                         int cammy, int recording, int *need, uint64_t now) {
    for (unsigned n = 0; n < BTN_DEVS; n++) {
        struct button_dev *d = &b->d[n];
        if (d->fd < 0) {
            if (now < d->retry) continue;
            d->retry = now + 2000u;
            char path[32];
            snprintf(path, sizeof path, "/dev/input/event%u", n);
            d->fd = open(path, O_RDWR | O_NONBLOCK | O_CLOEXEC);
            if (d->fd < 0) continue;
            int clock = CLOCK_MONOTONIC;
            d->mono = !ioctl(d->fd, EVIOCSCLOCKID, &clock);
            d->grabbed = 0; d->held = d->injected = 0; d->grab_ms = d->ungrab_ms = 0;
        }
        unsigned char ev[16 * 16];
        ssize_t got = read(d->fd, ev, sizeof ev);
        if (got < 0 && errno != EAGAIN) { close(d->fd); d->fd = -1; continue; }
        for (ssize_t i = 0; i + 16 <= got; i += 16) {
            unsigned type = ev[i + 8] | (unsigned)ev[i + 9] << 8;
            int code = ev[i + 10] | ev[i + 11] << 8;
            uint32_t value = ev[i + 12] | (uint32_t)ev[i + 13] << 8 | (uint32_t)ev[i + 14] << 16 | (uint32_t)ev[i + 15] << 24;
            if (type != EV_KEY) continue;
            int bit = key_bit(code);
            if (bit < 0) { /* not in the stock keymap: nothing to pass on; trace it */
                static unsigned odd;
                if (odd++ < 40u) fprintf(stderr, "key unmapped ev%u code=%d v=%u grab=%d\n", n, code, value, d->grabbed);
                continue;
            }
            uint32_t mask = 1u << bit;
            uint64_t t = (uint64_t)(ev[i] | (uint32_t)ev[i + 1] << 8 | (uint32_t)ev[i + 2] << 16 | (uint32_t)ev[i + 3] << 24) * 1000u +
                         (ev[i + 4] | (uint32_t)ev[i + 5] << 8 | (uint32_t)ev[i + 6] << 16 | (uint32_t)ev[i + 7] << 24) / 1000u;
            /* Without monotonic event times, fall back to the grab state at read time. */
            int exclusive = d->mono ? t >= d->grab_ms && (d->grabbed || t < d->ungrab_ms) : d->grabbed;
            if (value == 1) d->held |= mask;
            else if (value == 0) d->held &= ~mask;
            int live = b->live_view || recording || app->local_preview;
            enum uib_route route = uib_route(n, code, value, exclusive, cammy, app->screen != S_CLOSED, live, !!(d->injected & mask));
            /* Button trace for hardware checks (0.7.5): the first 400 key events of a run. */
            static unsigned traced;
            if (traced < 400u) {
                traced++;
                fprintf(stderr, "key t=%llu ev%u code=%d v=%u excl=%d grab=%d mono=%d cammy=%d menu=%d live=%d inj=%d route=%d\n",
                        (unsigned long long)t, n, code, value, exclusive, d->grabbed, d->mono, cammy, app->screen != S_CLOSED,
                        live, !!(d->injected & mask), (int)route);
            }
            switch (route) {
            case UIB_MUTE:
                if (face) {
                    _Atomic uint32_t *word = face->header + CFS_HEADER_CAPTURE;
                    uint32_t prior = atomic_load_explicit(word, memory_order_acquire);
                    if (prior >= 2u) {
                        uint32_t next = prior == UINT32_MAX ? 2u : prior + 1u;
                        atomic_store_explicit(word, next, memory_order_release);
                        app->capture_muted = (int)(next & 1u);
                        *need |= app->screen != S_CLOSED;
                    }
                }
                break;
            case UIB_MENU: *need |= ui_app_key(app, code, now); break;
            case UIB_OPEN: *need |= ui_app_key(app, code, now); break;
            case UIB_PRESS:
                set_grab(d, 0, now);
                if (!key_write(d->fd, code, 2)) { d->injected |= mask; d->down_ms = now; }
                else fprintf(stderr, "key pass-through write failed: ev%u code=%d errno=%d grab=%d\n", n, code, errno, d->grabbed);
                break;
            case UIB_RELEASE: {
                if (!(d->injected & mask)) { /* both halves arrived while grabbed */
                    if (key_write(d->fd, code, 2)) break;
                    d->injected |= mask; d->down_ms = now;
                }
                uint64_t since = ms() - d->down_ms;
                if (since < UIB_TAP_MS) usleep((useconds_t)(UIB_TAP_MS - since) * 1000u);
                (void)key_write(d->fd, code, 1); /* the kernel saw the physical release: 1 then 0 */
                (void)key_write(d->fd, code, 0);
                d->injected &= ~mask;
                /* Firmware without a tracking readout: a short DOWN flips the live view. */
                if (code == UIB_KEY_DOWN && !b->tracking_known && now - d->down_ms < UIB_SHORT_MS) b->live_view = !b->live_view;
                break;
            }
            default:
                /* Stock saw this release itself (we were ungrabbed); still note a short DOWN. */
                if (cammy && value == 0 && (d->injected & mask)) {
                    d->injected &= ~mask;
                    if (code == UIB_KEY_DOWN && !b->tracking_known && now - d->down_ms < UIB_SHORT_MS) b->live_view = !b->live_view;
                }
                break;
            }
        }
        if (!d->held && !d->injected) set_grab(d, cammy, now);
    }
}
/* Red record button (event3 code 28, never grabbed): stock records behind the face, so
 * after a press cammy-ui asks the stock app whether it is recording and shows the live view
 * while it is (Adam 2026-11-16). Stock answers on 127.0.0.1:8082; -1 when unknown. */
#define REC_KEY 28
struct record { int fd, active; uint64_t retry, check_until, next_poll; };
static int stock_recording(void) {
    int fd = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return -1;
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(8082)};
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    struct timeval tv = {0, 500000};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    static char buf[2048];
    size_t got = 0;
    if (!connect(fd, (struct sockaddr *)&a, sizeof a)) {
        static const char req[] = "GET /api/getdeviceinfo/?custom=1&cmd=2005 HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
        if (send(fd, req, sizeof req - 1, MSG_NOSIGNAL) == (ssize_t)(sizeof req - 1))
            for (ssize_t k; got + 1 < sizeof buf && (k = recv(fd, buf + got, sizeof buf - 1 - got, 0)) > 0;) got += (size_t)k;
    }
    close(fd);
    buf[got] = 0;
    const char *v = strstr(buf, "\"RecodStatus\"");
    if (!v || !(v = strchr(v, ':'))) return -1;
    while (*v == ':' || *v == ' ' || *v == '"') v++;
    return *v >= '0' && *v <= '9' ? *v != '0' : -1;
}
/* Touchscreen (SPD2010, event4) as a wake source only (0.9): opened read-only and never
 * grabbed, its events are drained and ignored. Taps still arrive through the face page;
 * the wake lets the closed menu tick once a second without making a tap wait for it. */
#define TOUCH_DEV "/dev/input/event4"
#define TOUCH_NAME "SPD2010"
static int touch_open(int *fd, uint64_t *retry, uint64_t now) {
    if (*fd >= 0) return 1;
    if (now < *retry) return 0;
    *retry = now + 10000u;
    *fd = open(TOUCH_DEV, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (*fd < 0) return 0;
    char name[32] = {0};
    if (ioctl(*fd, (int)EVIOCGNAME(sizeof name - 1), name) < 0 || strcmp(name, TOUCH_NAME)) { /* musl: int request */
        static unsigned odd;
        if (odd++ < 3u) fprintf(stderr, "cammy-ui: %s is not the touchscreen; idle tick %ums\n", TOUCH_DEV, UI_TICK_IDLE_NO_TOUCH_MS);
        close(*fd); *fd = -1; return 0;
    }
    return 1;
}
static void touch_drain(int *fd) {
    unsigned char ev[16 * 32];
    for (;;) {
        ssize_t got = read(*fd, ev, sizeof ev);
        if (got > 0) continue;
        if (got < 0 && (errno == EAGAIN || errno == EINTR)) return;
        close(*fd); *fd = -1; return; /* gone or EOF: reopened later */
    }
}
static void record_poll(struct record *r, int cammy, uint64_t now) {
    if (r->fd < 0) {
        if (now < r->retry) return;
        r->retry = now + 2000u;
        r->fd = open("/dev/input/event3", O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (r->fd < 0) return;
    }
    unsigned char ev[16 * 8];
    ssize_t got = read(r->fd, ev, sizeof ev);
    if (got < 0 && errno != EAGAIN) { close(r->fd); r->fd = -1; return; }
    for (ssize_t i = 0; i + 16 <= got; i += 16) {
        unsigned type = ev[i + 8] | (unsigned)ev[i + 9] << 8;
        int code = ev[i + 10] | ev[i + 11] << 8;
        if (type == EV_KEY && code == REC_KEY && cammy) { r->check_until = now + 4000u; r->next_poll = now + 400u; }
    }
    if (!cammy) { r->active = 0; return; }
    /* Poll after a press (recording starts within a few seconds) and once a second while on. */
    if ((r->active || now < r->check_until) && now >= r->next_poll) {
        r->next_poll = now + 1000u;
        int on = stock_recording();
        if (on >= 0 && on != r->active) { r->active = on; fprintf(stderr, "record: stock recording %s\n", on ? "on" : "off"); }
    }
}
/* Publish into the buffer the face is not showing. Only once the previous frame was
 * taken (ack), so LVGL never reads a buffer while it is being written. */
static int publish(struct cui_header *h, const struct ui_app *a) {
    uint32_t seq = atomic_load(&h->frame_seq);
    if (seq && atomic_load_explicit(&h->ack_seq, memory_order_acquire) != seq) return -1;
    uint32_t index = seq ? (atomic_load(&h->frame_index) ^ 1u) : 0u;
    struct ui_canvas c = {cui_frame(h, index)};
    ui_app_render(a, &c);
    atomic_store(&h->frame_index, index);
    atomic_store_explicit(&h->frame_seq, seq + 1u ? seq + 1u : 1u, memory_order_release);
    return 0;
}
int main(int argc, char **argv) {
    struct ui_real real = {"/tmp/cammy-runtime", "/etc/cammy/home.conf", "/etc/cammy/portal.token", "/etc/cammy/volume",
                           NULL, -1, -1, 0, 8787, 0, 0, {0}, {0}, "/etc/cammy/face-pack", "/etc/cammy", {0}};
    const char *render = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--run-dir") && i + 1 < argc) real.run_dir = argv[++i];
        else if (!strcmp(argv[i], "--once-render") && i + 1 < argc) render = argv[++i];
        else { fprintf(stderr, "usage: %s [--run-dir DIR] [--once-render FILE]\n", argv[0]); return 2; }
    }
    struct ui_backend be;
    struct ui_app app;
    ui_real_backend(&real, &be);
    ui_app_init(&app, &be, ui_real_volume(&real));
    app.pack = ui_real_pack(&real);
    if (render) { /* camera-side smoke test: menu with live data, raw BGRA to FILE */
        unsigned char *px = malloc(CUI_FRAME_BYTES);
        if (!px) return 1;
        struct ui_canvas c = {px};
        (void)ui_app_tap(&app, 178, 200, ms());
        ui_app_render(&app, &c);
        FILE *f = fopen(render, "wb");
        int ok = f && fwrite(px, 1, CUI_FRAME_BYTES, f) == CUI_FRAME_BYTES;
        if (f) fclose(f);
        free(px);
        return ok ? 0 : 1;
    }
    supervisor_init();
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_signal;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    signal(SIGPIPE, SIG_IGN);
    /* The face service creates its directory when sdvcam loads it; wait for it. */
    struct cui_header *page = NULL;
    for (unsigned tries = 0; !stop && !(page = create_page()); tries++) {
        if (tries == 0) perror("cammy-ui: waiting for the face service page directory");
        sleep(2);
    }
    if (!page) return 0;
    fprintf(stderr, "cammy-ui: page %s ready\n", CUI_FILE);
    real.page = page; /* camera mode and face pack go through the page */
    atomic_store_explicit(&page->face_pack, (uint32_t)app.pack + 1u, memory_order_release);
    struct cfs_page *face = NULL;
    uint32_t seen_taps = 0;
    struct buttons btn;
    memset(&btn, 0, sizeof btn);
    for (unsigned n = 0; n < BTN_DEVS; n++) btn.d[n].fd = -1;
    uint32_t live_shown = 0;
    struct record rec = {-1, 0, 0, 0, 0};
    int touch_fd = -1;
    uint64_t touch_retry = 0, fast_until = 0;
    int cammy = 0;
    int synced = 0, need = 0;
    /* Loading gate: records are read at most once a second, only while it holds. Inputs
     * stay closed until it is past the stock settle (released latches). */
    struct ub_input boot_in;
    struct ub_view boot_view;
    memset(&boot_view, 0, sizeof boot_view);
    uint64_t boot_read_at = 0;
    int released = 0;
    uint32_t boot_word = CUI_BOOT_LOADING;
    while (!stop) {
        uint64_t now = ms();
        atomic_store_explicit(&page->heartbeat_ms, (uint32_t)now, memory_order_release);
        struct cfs_status s;
        int face_fresh = !face_state(&face, &s);
        if (face_fresh) {
            cammy = s.mode == CFS_MODE_CAMMY;
            app.capture_muted = (int)(atomic_load_explicit(face->header + CFS_HEADER_CAPTURE, memory_order_acquire) & 1u);
            if (!synced) { seen_taps = s.taps; synced = 1; } /* taps before we started are not ours */
            if (s.mode == CFS_MODE_CAMMY && s.taps != seen_taps) {
                uint32_t x = s.last_tap >> 16, y = s.last_tap & 0xffffu;
                if (x < CUI_W && y < CUI_H) need |= ui_app_tap(&app, (int)x, (int)y, now);
                fast_until = now + UI_INPUT_HOLD_MS;
            }
            seen_taps = s.taps;
            /* The live view follows the stock face-follow flag when the firmware reports it. */
            btn.tracking_known = s.tracking != CFS_TRACK_UNKNOWN;
            if (btn.tracking_known) btn.live_view = s.tracking == CFS_TRACK_ON;
            /* The gate word is read after the status: the face stores it before the status
             * that first reports CammyOS mode. Without a holding gate (an older face library,
             * or the gate already opened or given to stock) there is nothing to wait for. */
            if (cammy && !app.boot_done) {
                uint32_t gate = atomic_load_explicit(face->header + CFS_HEADER_BOOT, memory_order_acquire);
                if (gate != CFS_BOOT_LOADING) app.boot_done = 1;
                else if (now >= boot_read_at) {
                    boot_read_at = now + 1000u;
                    ui_real_boot(&real, &boot_in);
                    boot_in.supervisor = supervisor_alive();
                    ub_eval(&boot_in, &boot_view);
                    if (ui_app_loading(&app, &boot_view, now)) { need = 1; fprintf(stderr, "cammy-ui: boot %d %s %s\n", boot_view.stage, boot_view.wait, boot_view.reason); }
                }
            }
        }
        if (cammy && (app.boot_done || boot_view.stage != UB_STOCK)) released = 1;
        uint32_t want_boot = app.boot_done ? CUI_BOOT_READY : CUI_BOOT_LOADING;
        if (want_boot != boot_word) { boot_word = want_boot; atomic_store_explicit(&page->boot, boot_word, memory_order_release); }
        if (!cammy) { btn.live_view = 0; app.local_preview = 0; }
        uint32_t shown = atomic_load_explicit(&page->face_shown, memory_order_acquire);
        int shown_pack = shown && shown <= CUI_PACK_COUNT ? (int)shown - 1 : -1;
        if (shown_pack != app.shown_pack) {
            app.shown_pack = shown_pack;
            app.dirty = 1;
            need |= app.screen == S_CONTROLS;
        }
        if (face_fresh) {
            uint32_t packs = atomic_load_explicit(face->header + CFS_HEADER_PACKS, memory_order_acquire);
            int available = (packs & (1u << CUI_PACK_CAMMY)) != 0;
            if (available != app.cammy_available) {
                app.cammy_available = available;
                app.dirty = 1;
                need |= app.screen == S_CONTROLS;
            }
        }
        /* Inputs: CammyOS mode once past the stock settle; otherwise only to release a grab. */
        int keys_read = cammy ? released : btn.d[0].grabbed || btn.d[1].grabbed;
        int rec_read = (cammy && released) || rec.active;
        if (keys_read)
            buttons_poll(&btn, &app, face_fresh ? face : NULL, cammy, rec.active, &need, now);
        if (rec_read) record_poll(&rec, cammy, now);
        need |= ui_app_tick(&app, now);
        uint32_t live = (uint32_t)(btn.live_view || rec.active || app.local_preview);
        if (live != live_shown) {
            live_shown = live;
            atomic_store_explicit(&page->live_view, live_shown, memory_order_release);
        }
        /* Show only once the first frame is ready. Closing needs no new frame. */
        if (app.screen == S_CLOSED) {
            atomic_store_explicit(&page->show, 0u, memory_order_release);
            need = 0;
        } else {
            if (need && !publish(page, &app)) need = 0;
            if (!need) atomic_store_explicit(&page->show, 1u, memory_order_release);
        }
        /* Wake on a button or a touch at once. Tick every 100 ms while the menu is open, something
         * loads or input was recent, else once a second (ui_tick.h). Only fds this loop reads are
         * polled: an unread device would keep poll returning at once. */
        int keys_busy = 0;
        for (unsigned n = 0; n < BTN_DEVS; n++) keys_busy |= btn.d[n].held || btn.d[n].injected;
        uint64_t after = ms();
        int touch = cammy && touch_open(&touch_fd, &touch_retry, after); /* stock mode: taps are not ours */
        struct ui_tick_in tick = {app.screen != S_CLOSED, cammy && !app.boot_done, keys_busy,
                                  rec.active || after < rec.check_until, need, app.volume != app.volume_saved,
                                  !cammy || touch, after, fast_until};
        unsigned wait = ui_tick_ms(&tick);
        struct pollfd pf[BTN_DEVS + 2];
        unsigned np = 0;
        if (keys_read) for (unsigned n = 0; n < BTN_DEVS; n++) if (btn.d[n].fd >= 0) pf[np++] = (struct pollfd){btn.d[n].fd, POLLIN, 0};
        if (rec_read && rec.fd >= 0) pf[np++] = (struct pollfd){rec.fd, POLLIN, 0};
        if (wait != UI_TICK_FAST_MS && touch) {
            touch_drain(&touch_fd); /* events from the fast ticks are old news */
            if (touch_fd >= 0) pf[np++] = (struct pollfd){touch_fd, POLLIN, 0};
        }
        if (np) { if (poll(pf, np, (int)wait) > 0) fast_until = ms() + UI_INPUT_HOLD_MS; }
        else usleep((useconds_t)wait * 1000u);
    }
    atomic_store(&page->show, 0u);
    atomic_store(&page->heartbeat_ms, 0u);
    unlink(CUI_FILE);
    ui_real_close(&real);
    atomic_store(&page->live_view, 0u);
    if (rec.fd >= 0) close(rec.fd);
    if (touch_fd >= 0) close(touch_fd);
    for (unsigned n = 0; n < BTN_DEVS; n++)
        if (btn.d[n].fd >= 0) { if (btn.d[n].grabbed) (void)ioctl(btn.d[n].fd, EVIOCGRAB, 0); close(btn.d[n].fd); }
    return 0;
}
