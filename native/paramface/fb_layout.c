/* Read-only Linux framebuffer metadata; no mapping, pixel access or set ioctl. */
#define _GNU_SOURCE
#include <fcntl.h>
#include <linux/fb.h>
#include <stdio.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(void) {
    struct fb_var_screeninfo variable = {0};
    struct fb_fix_screeninfo fixed = {0};
    int fd;
    alarm(2);
    fd = open("/dev/fb0", O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) { perror("read-only fb0 open"); return 1; }
    if (ioctl(fd, FBIOGET_VSCREENINFO, &variable) != 0 ||
        ioctl(fd, FBIOGET_FSCREENINFO, &fixed) != 0) {
        perror("framebuffer metadata query"); close(fd); return 1;
    }
    close(fd);
    printf("{\"read_only\":true,\"xres\":%u,\"yres\":%u,"
           "\"xres_virtual\":%u,\"yres_virtual\":%u,\"bits_per_pixel\":%u,"
           "\"stride_bytes\":%u,\"visual\":%u,\"red\":[%u,%u,%u],"
           "\"green\":[%u,%u,%u],\"blue\":[%u,%u,%u],\"alpha\":[%u,%u,%u]}\n",
           variable.xres,variable.yres,variable.xres_virtual,variable.yres_virtual,
           variable.bits_per_pixel,fixed.line_length,fixed.visual,
           variable.red.offset,variable.red.length,variable.red.msb_right,
           variable.green.offset,variable.green.length,variable.green.msb_right,
           variable.blue.offset,variable.blue.length,variable.blue.msb_right,
           variable.transp.offset,variable.transp.length,variable.transp.msb_right);
    return 0;
}
