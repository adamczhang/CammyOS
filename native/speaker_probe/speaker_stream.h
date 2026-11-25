/* SPDX-License-Identifier: MIT. Speaker stream protocol shared by the portal
 * (src/cammy_speaker.h) and the guardian feeder (guardian.c). Constants only.
 *
 * One job is the first file <job> plus appended chunks <job>.1 .. <job>.MAX_CHUNKS.
 * Both sides enforce the same total: first chunk plus appends never exceed
 * CAMMY_SPEAKER_STREAM_MAX_BYTES; a job of exactly that size is accepted.
 *
 * Close-vs-enqueue handshake: slot <job>.N has exactly one creator.
 *  - The portal admits chunk N by writing <job>.N.tmp and then link()ing it to
 *    <job>.N (fails with EEXIST if the name exists), then unlinking the .tmp.
 *  - The feeder closes the stream by creating <job>.N with O_CREAT|O_EXCL as an
 *    empty tombstone, N being the first slot it has not played.
 * Whichever call reaches the directory first wins. A successful link() means
 * the feeder can no longer close before slot N and will play it (barring
 * Stop/cancel). EEXIST on the portal means the stream is closed: 409, nothing
 * queued. The tombstone stays until the portal reaps the guardian.
 */
#ifndef CAMMY_SPEAKER_STREAM_H
#define CAMMY_SPEAKER_STREAM_H
#define CAMMY_SPEAKER_RATE_BYTES 32000                 /* 16 kHz mono S16_LE */
#define CAMMY_SPEAKER_CHUNK_MIN_BYTES 1600             /* 50 ms */
#define CAMMY_SPEAKER_CHUNK_MAX_BYTES 160000           /* 5 s */
#define CAMMY_SPEAKER_STREAM_MAX_SECONDS 60
#define CAMMY_SPEAKER_STREAM_MAX_BYTES (CAMMY_SPEAKER_STREAM_MAX_SECONDS*CAMMY_SPEAKER_RATE_BYTES)
#define CAMMY_SPEAKER_STREAM_MAX_CHUNKS 64             /* appended chunks after the first */
enum cammy_speaker_refusal {
    CAMMY_SPEAKER_REFUSED = 3,
    CAMMY_SPEAKER_PCM_CLOSED = 10,
    CAMMY_SPEAKER_PCM_FORMAT = 11,
    CAMMY_SPEAKER_MIXER_ROUTE = 12,
    CAMMY_SPEAKER_HUB_DISABLED = 13,
};
#endif
