/* Host fixture for native/speaker_probe/guardian.c: ALSA control ioctls over a
 * text state file instead of /dev/snd/controlC0. Included with -DCAMMY_CTL_SHIM.
 * CAMMY_CTL_STATE lines: "<numid> <B|I|E> <min> <max> <value> <name...>".
 * CAMMY_CTL_LOG gets "read numid=N" per ELEM_READ and "set numid=N V" per ELEM_WRITE. */
#include <stdarg.h>
struct shim_control { unsigned numid; char type; long min, max, value; char name[44]; };
static int shim_load(struct shim_control *c, int cap) {
    const char *path = getenv("CAMMY_CTL_STATE"); FILE *f = path ? fopen(path, "r") : NULL; int n = 0;
    if (!f) return -1;
    while (n < cap && fscanf(f, "%u %c %ld %ld %ld %43[^\n]", &c[n].numid, &c[n].type, &c[n].min, &c[n].max, &c[n].value, c[n].name) == 6) n++;
    fclose(f); return n;
}
static void shim_save(const struct shim_control *c, int n) {
    FILE *f = fopen(getenv("CAMMY_CTL_STATE"), "w"); if (!f) return;
    for (int i = 0; i < n; i++) fprintf(f, "%u %c %ld %ld %ld %s\n", c[i].numid, c[i].type, c[i].min, c[i].max, c[i].value, c[i].name);
    fclose(f);
}
static void shim_log(const char *format, ...) {
    const char *path = getenv("CAMMY_CTL_LOG"); FILE *f = path ? fopen(path, "a") : NULL; if (!f) return;
    va_list args; va_start(args, format); vfprintf(f, format, args); va_end(args); fclose(f);
}
static int ctl_open(const char *path) { (void)path; return getenv("CAMMY_CTL_STATE") ? open("/dev/null", O_RDWR | O_CLOEXEC) : -1; }
static int ctl_ioctl(int fd, unsigned request, void *arg) {
    (void)fd; struct shim_control c[16]; int n = shim_load(c, 16), at = -1;
    if (n < 0) { errno = EIO; return -1; }
    unsigned numid = request == SNDRV_CTL_IOCTL_ELEM_INFO ? ((struct snd_ctl_elem_info *)arg)->id.numid : ((struct snd_ctl_elem_value *)arg)->id.numid;
    for (int i = 0; i < n; i++) if (c[i].numid == numid) at = i;
    if (at < 0) { errno = ENOENT; return -1; }
    if (request == SNDRV_CTL_IOCTL_ELEM_INFO) {
        struct snd_ctl_elem_info *info = arg;
        info->id.iface = SNDRV_CTL_ELEM_IFACE_MIXER; snprintf((char *)info->id.name, sizeof(info->id.name), "%s", c[at].name);
        info->access = SNDRV_CTL_ELEM_ACCESS_READWRITE; info->count = 1;
        info->type = c[at].type == 'B' ? SNDRV_CTL_ELEM_TYPE_BOOLEAN : c[at].type == 'I' ? SNDRV_CTL_ELEM_TYPE_INTEGER : SNDRV_CTL_ELEM_TYPE_ENUMERATED;
        if (c[at].type == 'E') info->value.enumerated.items = (unsigned)(c[at].max + 1);
        else { info->value.integer.min = c[at].min; info->value.integer.max = c[at].max; }
        return 0;
    }
    struct snd_ctl_elem_value *value = arg;
    if (request == SNDRV_CTL_IOCTL_ELEM_READ) {
        shim_log("read numid=%u\n", numid);
        if (c[at].type == 'E') value->value.enumerated.item[0] = (unsigned)c[at].value; else value->value.integer.value[0] = c[at].value;
        return 0;
    }
    if (request == SNDRV_CTL_IOCTL_ELEM_WRITE) {
        long v = value->value.integer.value[0];
        if (c[at].type == 'B') shim_log("set numid=%u %s\n", numid, v ? "on" : "off"); else shim_log("set numid=%u %ld\n", numid, v);
        c[at].value = v; shim_save(c, n); return 0;
    }
    errno = ENOTTY; return -1;
}
