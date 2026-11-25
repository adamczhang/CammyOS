/* SPDX-License-Identifier: MIT
 * CammyOS native status bridge wire protocol, version 1.0 (WP2, status-only).
 * Normative description: docs/native-bridge/PROTOCOL.md.
 *
 * Transport: AF_UNIX SOCK_SEQPACKET; one request or response per packet.
 * Encoding: fixed little-endian layouts; no text parsing, no variable fields.
 * This header is shared by the bridge core and by future local clients (portal).
 */
#ifndef CAMMY_BRIDGE_PROTO_H
#define CAMMY_BRIDGE_PROTO_H

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define CB_PROTO_MAJOR 1u
#define CB_PROTO_MINOR 0u
#define CB_REQUEST_SIZE 64u
#define CB_RESPONSE_HEADER_SIZE 64u
#define CB_MAX_FRAME 256u
#define CB_MAX_DEADLINE_HORIZON_MS 60000u
#define CB_REQUEST_MAGIC "CBRQ"
#define CB_RESPONSE_MAGIC "CBRS"
#define CB_FIELD_NONE 0xFFFFu

/* Request layout (64 bytes). */
#define CB_RQ_MAGIC 0
#define CB_RQ_MAJOR 4
#define CB_RQ_MINOR 6
#define CB_RQ_HEADER_SIZE 8
#define CB_RQ_COMMAND 10
#define CB_RQ_REQUEST_ID 12
#define CB_RQ_FLAGS 16
#define CB_RQ_RESERVED 20
#define CB_RQ_EXPECT_INSTANCE 24
#define CB_RQ_BOOT_ID 32
#define CB_RQ_DEADLINE 48
#define CB_RQ_OWNER_GENERATION 56

/* Response header layout (64 bytes), followed by payload_size bytes. */
#define CB_RS_MAGIC 0
#define CB_RS_MAJOR 4
#define CB_RS_MINOR 6
#define CB_RS_HEADER_SIZE 8
#define CB_RS_COMMAND 10
#define CB_RS_REQUEST_ID 12
#define CB_RS_STATUS 16
#define CB_RS_BACKEND_KIND 18
#define CB_RS_PAYLOAD_SIZE 20
#define CB_RS_INSTANCE 24
#define CB_RS_BOOT_ID 32
#define CB_RS_MONO_NOW 48
#define CB_RS_OWNER_GENERATION 56

enum cb_command {
    CB_CMD_HELLO = 1,
    CB_CMD_STATUS = 2,
    CB_CMD_CAPABILITIES = 3,
    /* 0x10..0x1F: reserved for stage-3 mutating media commands. Always rejected
     * with CB_ST_MUTATION_DISABLED by protocol 1.0. */
    CB_CMD_MUTATING_FIRST = 0x10,
    CB_CMD_MUTATING_LAST = 0x1F
};
#define CB_COMMAND_MASK_V1 ((1u << CB_CMD_HELLO) | (1u << CB_CMD_STATUS) | (1u << CB_CMD_CAPABILITIES))

enum cb_status {
    CB_ST_OK = 0,
    CB_ST_BAD_MAGIC = 1,          /* framing: connection closes */
    CB_ST_BAD_LENGTH = 2,         /* framing: connection closes */
    CB_ST_TOO_LARGE = 3,          /* framing: connection closes */
    CB_ST_UNSUPPORTED_VERSION = 4,
    CB_ST_UNKNOWN_FIELD = 5,
    CB_ST_BAD_REQUEST_ID = 6,
    CB_ST_UNKNOWN_COMMAND = 7,
    CB_ST_MUTATION_DISABLED = 8,
    CB_ST_OWNER_UNSUPPORTED = 9,
    CB_ST_STALE_INSTANCE = 10,
    CB_ST_BOOT_MISMATCH = 11,
    CB_ST_CLOCK_DOMAIN = 12,
    CB_ST_EXPIRED = 13,
    CB_ST_BAD_DEADLINE = 14,
    CB_ST_BUSY = 15,              /* connection closes */
    CB_ST_PEER_DENIED = 16,       /* connection closes, identity withheld */
    CB_ST_INTERNAL = 17,
    CB_ST_BAD_ANCILLARY = 18      /* framing: connection closes */
};

enum cb_backend_kind { CB_BACKEND_NONE = 0, CB_BACKEND_FAKE = 1, CB_BACKEND_NATIVE = 2 };
enum cb_backend_state { CB_BSTATE_UNAVAILABLE = 0, CB_BSTATE_READY = 1, CB_BSTATE_ERROR = 2 };
enum cb_backend_reason {
    CB_BREASON_NONE = 0,
    CB_BREASON_FAKE_BACKEND = 1,
    CB_BREASON_NATIVE_TABLE_ABSENT = 2,
    CB_BREASON_NATIVE_TABLE_REJECTED = 3,
    CB_BREASON_INIT_FAILED = 4,
    CB_BREASON_QUERY_FAILED = 5,
    CB_BREASON_NATIVE_EXITING = 6,          /* native table: host is detaching */
    CB_BREASON_NATIVE_IDENTITY_MISMATCH = 7 /* native table: host record/hash mismatch */
};
#define CB_BREASON_MAX CB_BREASON_NATIVE_IDENTITY_MISMATCH

enum cb_capability_id {
    CB_CAP_STATUS_IPC = 1,        /* the bridge protocol itself; not physical */
    CB_CAP_VIDEO_STREAM = 2,
    CB_CAP_AUDIO_CAPTURE = 3,
    CB_CAP_AUDIO_PLAYBACK = 4,
    CB_CAP_GIMBAL = 5,
    CB_CAP_DISPLAY = 6,
    CB_CAP_LIFECYCLE_HANDOFF = 7
};
#define CB_CAP_COUNT 7u
enum cb_cap_state { CB_CAP_UNAVAILABLE = 0, CB_CAP_AVAILABLE = 1, CB_CAP_SIMULATED = 2 /* reserved; never sent by 1.0 */ };
enum cb_cap_reason {
    CB_CREASON_NONE = 0,
    CB_CREASON_FAKE_BACKEND = 1,
    CB_CREASON_NATIVE_TABLE_ABSENT = 2,
    CB_CREASON_NOT_IN_PROTOCOL_V1 = 3,
    CB_CREASON_BACKEND_ERROR = 4,
    CB_CREASON_NATIVE_TABLE_REJECTED = 5
};
#define CB_CAP_FLAG_PHYSICAL 0x01u

#define CB_STATUS_FLAG_BOOT_ID_AVAILABLE 0x0001u
#define CB_STATUS_FLAG_HARDWARE_ACTIONS_ENABLED 0x0002u /* never set by 1.0 */
#define CB_STATUS_FLAG_OWNER_PRESENT 0x0004u            /* never set by 1.0 */

/* Payload sizes and layouts. */
#define CB_ERROR_PAYLOAD_SIZE 16u     /* u16 major,u16 minor_max,u16 field_offset,u16 disposition,u32 request_size,u32 errors_remaining */
#define CB_DISPOSITION_KEEP 0u
#define CB_DISPOSITION_CLOSING 1u
#define CB_HELLO_PAYLOAD_SIZE 48u
#define CB_STATUS_PAYLOAD_SIZE 64u
#define CB_CAP_ENTRY_SIZE 8u
#define CB_CAPS_PAYLOAD_SIZE (8u + CB_CAP_COUNT * CB_CAP_ENTRY_SIZE)
#define CB_BRIDGE_VERSION_LEN 16u

/* Little-endian helpers; byte-wise so alignment and host order never matter. */
static inline void cb_put16(unsigned char *p, uint16_t v) { p[0] = (unsigned char)v; p[1] = (unsigned char)(v >> 8); }
static inline void cb_put32(unsigned char *p, uint32_t v) {
    for (unsigned i = 0; i < 4; i++) p[i] = (unsigned char)(v >> (8 * i));
}
static inline void cb_put64(unsigned char *p, uint64_t v) {
    for (unsigned i = 0; i < 8; i++) p[i] = (unsigned char)(v >> (8 * i));
}
static inline uint16_t cb_get16(const unsigned char *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static inline uint32_t cb_get32(const unsigned char *p) {
    uint32_t v = 0;
    for (unsigned i = 0; i < 4; i++) v |= (uint32_t)p[i] << (8 * i);
    return v;
}
static inline uint64_t cb_get64(const unsigned char *p) {
    uint64_t v = 0;
    for (unsigned i = 0; i < 8; i++) v |= (uint64_t)p[i] << (8 * i);
    return v;
}

struct cb_request {
    uint16_t major, minor, command;
    uint32_t request_id;
    uint64_t expect_instance;
    unsigned char boot_id[16];
    uint64_t deadline_mono_ms;
    uint64_t owner_generation;
};

/* Encode a protocol-1.0 request. flags/reserved are always zero. */
static inline void cb_encode_request(unsigned char out[CB_REQUEST_SIZE], const struct cb_request *r) {
    memset(out, 0, CB_REQUEST_SIZE);
    memcpy(out + CB_RQ_MAGIC, CB_REQUEST_MAGIC, 4);
    cb_put16(out + CB_RQ_MAJOR, r->major);
    cb_put16(out + CB_RQ_MINOR, r->minor);
    cb_put16(out + CB_RQ_HEADER_SIZE, (uint16_t)CB_REQUEST_SIZE);
    cb_put16(out + CB_RQ_COMMAND, r->command);
    cb_put32(out + CB_RQ_REQUEST_ID, r->request_id);
    cb_put64(out + CB_RQ_EXPECT_INSTANCE, r->expect_instance);
    memcpy(out + CB_RQ_BOOT_ID, r->boot_id, 16);
    cb_put64(out + CB_RQ_DEADLINE, r->deadline_mono_ms);
    cb_put64(out + CB_RQ_OWNER_GENERATION, r->owner_generation);
}

struct cb_response_header {
    uint16_t major, minor, command, status, backend_kind;
    uint32_t request_id, payload_size;
    uint64_t instance, mono_now_ms, owner_generation;
    unsigned char boot_id[16];
};

/* Validate and decode a response frame. Returns 0 on success, -1 if the frame is
 * not a well-formed protocol-1 response whose payload length matches. */
static inline int cb_decode_response_header(const unsigned char *buf, size_t len, struct cb_response_header *h) {
    if (len < CB_RESPONSE_HEADER_SIZE || len > CB_MAX_FRAME) return -1;
    if (memcmp(buf + CB_RS_MAGIC, CB_RESPONSE_MAGIC, 4) != 0) return -1;
    if (cb_get16(buf + CB_RS_HEADER_SIZE) != CB_RESPONSE_HEADER_SIZE) return -1;
    h->major = cb_get16(buf + CB_RS_MAJOR);
    h->minor = cb_get16(buf + CB_RS_MINOR);
    h->command = cb_get16(buf + CB_RS_COMMAND);
    h->request_id = cb_get32(buf + CB_RS_REQUEST_ID);
    h->status = cb_get16(buf + CB_RS_STATUS);
    h->backend_kind = cb_get16(buf + CB_RS_BACKEND_KIND);
    h->payload_size = cb_get32(buf + CB_RS_PAYLOAD_SIZE);
    h->instance = cb_get64(buf + CB_RS_INSTANCE);
    memcpy(h->boot_id, buf + CB_RS_BOOT_ID, 16);
    h->mono_now_ms = cb_get64(buf + CB_RS_MONO_NOW);
    h->owner_generation = cb_get64(buf + CB_RS_OWNER_GENERATION);
    if (h->major != CB_PROTO_MAJOR) return -1;
    if ((size_t)h->payload_size != len - CB_RESPONSE_HEADER_SIZE) return -1;
    return 0;
}

#endif
