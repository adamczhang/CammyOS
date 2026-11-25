/* SPDX-License-Identifier: MIT. Real (camera) backend for cammy-ui; see ui_backend.c. */
#ifndef CAMMY_UI_BACKEND_H
#define CAMMY_UI_BACKEND_H
#include "ui_app.h"
struct ui_real {
    const char *run_dir, *home_conf, *token_file, *volume_file;
    struct cui_header *page;
    int wpa_fd, pending_id, renewed, portal_port;
    uint64_t scan_at, renew_at;
    char pending_ssid[UI_SSID_MAX], pending_psk[UI_PSK_MAX];
    const char *pack_file;    /* /etc/cammy/face-pack */
    const char *cfg_dir;      /* /etc/cammy: startup.pending, runtime-health-fault */
    char boot_id[UB_BOOT_ID]; /* this boot, read once (preset by fixtures) */
};
void ui_real_backend(struct ui_real *, struct ui_backend *);
int ui_real_volume(const struct ui_real *);
int ui_real_pack(const struct ui_real *); /* chosen pack index; 0 (Pikachu) if unset */
void ui_real_close(struct ui_real *); /* drops the wpa client socket file */
/* The loading gate's records (ui_boot.h), all but supervisor (the caller's, left -1). */
void ui_real_boot(struct ui_real *, struct ub_input *);
#endif
