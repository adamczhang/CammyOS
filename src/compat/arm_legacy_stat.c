/* SPDX-License-Identifier: MIT
 * The camera's vendor 4.9 kernel returns EINVAL for statx, not ENOSYS.
 * Its legacy fstatat64 works. Adapt the ARM Linux UAPI stat64 layout to
 * musl's public time64 struct; do not modify the device kernel.
 * Kernel structure declaration: Linux UAPI asm/stat.h (syscall-note).
 */
#define _GNU_SOURCE
#define stat cammy_kernel_stat
#define stat64 cammy_kernel_stat64
#define st_atime raw_atime
#define st_mtime raw_mtime
#define st_ctime raw_ctime
#include <asm/stat.h>
#undef stat
#undef stat64
#undef st_atime
#undef st_mtime
#undef st_ctime
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <fcntl.h>
#include <stdint.h>
#include <string.h>
_Static_assert(sizeof(struct cammy_kernel_stat64)==104,"Unexpected ARM stat64 ABI");
static void convert(struct stat *out,const struct cammy_kernel_stat64 *in) {
    memset(out,0,sizeof(*out));
    out->st_dev=in->st_dev;out->st_ino=in->st_ino;out->st_mode=in->st_mode;
    out->st_nlink=in->st_nlink;out->st_uid=in->st_uid;out->st_gid=in->st_gid;
    out->st_rdev=in->st_rdev;out->st_size=in->st_size;out->st_blksize=in->st_blksize;out->st_blocks=in->st_blocks;
    out->st_atim.tv_sec=(int32_t)in->raw_atime;out->st_atim.tv_nsec=in->st_atime_nsec;
    out->st_mtim.tv_sec=(int32_t)in->raw_mtime;out->st_mtim.tv_nsec=in->st_mtime_nsec;
    out->st_ctim.tv_sec=(int32_t)in->raw_ctime;out->st_ctim.tv_nsec=in->st_ctime_nsec;
}
int fstatat(int fd,const char *path,struct stat *out,int flags) {
    struct cammy_kernel_stat64 raw;
    if(syscall(SYS_fstatat64,fd,path,&raw,flags)<0)return -1;
    convert(out,&raw);return 0;
}
int stat(const char *path,struct stat *out) {return fstatat(AT_FDCWD,path,out,0);}
int lstat(const char *path,struct stat *out) {return fstatat(AT_FDCWD,path,out,AT_SYMLINK_NOFOLLOW);}
int fstat(int fd,struct stat *out) {
    struct cammy_kernel_stat64 raw;
    if(syscall(SYS_fstat64,fd,&raw)<0)return -1;
    convert(out,&raw);return 0;
}
