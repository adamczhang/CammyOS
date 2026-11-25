/* SPDX-License-Identifier: MIT. Exact owned RAM IPC file, no native memory paths. */
#define _GNU_SOURCE
#include "ipc_file.h"
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/mman.h>
#include <unistd.h>
#include <string.h>
#include <errno.h>
int cfs_current_boot(uint32_t boot[4]) {
    char raw[37];unsigned char bytes[16];unsigned n=0,half=0,value=0;
    int fd=open("/proc/sys/kernel/random/boot_id",O_RDONLY|O_CLOEXEC);
    if(fd<0)return -1;
    ssize_t count=read(fd,raw,sizeof(raw));close(fd);if(count!=37 || raw[36]!='\n')return -1;
    for(unsigned i=0;i<36;i++){
        unsigned char c=(unsigned char)raw[i];
        if(i==8||i==13||i==18||i==23){if(c!='-')return -1;continue;}
        unsigned digit;if(c>='0'&&c<='9')digit=c-'0';else if(c>='a'&&c<='f')digit=c-'a'+10u;else return -1;
        value=(value<<4)|digit;half++;
        if(half==2){if(n>=16)return -1;bytes[n++]=(unsigned char)value;value=half=0;}
    }
    if(n!=16)return -1;
    memcpy(boot,bytes,sizeof(bytes));return 0;
}
int cfs_map_file(const char *path,int create,struct cfs_page **out) {
    char parent[256];size_t len=strlen(path);if(!len||len>=sizeof(parent))return -1;
    memcpy(parent,path,len+1);char *last=strrchr(parent,'/');if(!last||last==parent)return -1;*last=0;
    struct stat st;
    if(lstat(parent,&st) || !S_ISDIR(st.st_mode) || (st.st_mode&0777)!=0700 || st.st_uid!=geteuid())return -1;
    int flags=O_RDWR|O_CLOEXEC|O_NOFOLLOW;
    if(create)flags|=O_CREAT|O_EXCL;
    int fd=open(path,flags,0600);if(fd<0)return -1;
    int ok=0;
    if(create && ftruncate(fd,CFS_PAGE_BYTES))goto done;
    if(fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_nlink!=1 || st.st_uid!=geteuid() || (st.st_mode&0777)!=0600 || st.st_size!=CFS_PAGE_BYTES)goto done;
    void *map=mmap(NULL,CFS_PAGE_BYTES,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
    if(map==MAP_FAILED)goto done;
    *out=map;ok=1;
 done:close(fd);return ok?0:-1;
}
