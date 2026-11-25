/* SPDX-License-Identifier: MIT. Read the defined face-service IPC only. */
#define _POSIX_C_SOURCE 200809L
#define CFS_CLIENT_ONLY
#include "face_service.c"
#include "ipc_file.c"
#include <stdio.h>
#include <time.h>
int main(int argc,char **argv){
    const char *path=CFS_FILE;if(argc==3&&!strcmp(argv[1],"--file"))path=argv[2];else if(argc!=1)return 2;
    struct cfs_page *p;struct cfs_status s;uint32_t boot[4];
    if(cfs_map_file(path,0,&p)||atomic_load(p->header)!=CFS_MAGIC||atomic_load(p->header+1)!=CFS_ABI||atomic_load(p->header+2)!=CFS_PAGE_BYTES||!atomic_load(p->header+10)||!atomic_load(p->header+11)||cfs_current_boot(boot)||cfs_read_status(p,&s))return 1;
    for(unsigned i=0;i<4;i++)if(boot[i]!=atomic_load(p->header+4+i))return 1;
    struct timespec t;if(clock_gettime(CLOCK_MONOTONIC,&t))return 1;
    uint64_t now=(uint64_t)t.tv_sec*1000u+(uint32_t)t.tv_nsec/1000000u;
    if(!s.gui_ms||now<s.gui_ms||now-s.gui_ms>1000u||s.phase==CFS_FAILED||s.phase==CFS_DETACHED)return 1;
    printf("format=cammy-face-health/1\nnative_pid=%u\ngui_ready=1\nphase=%u\nphysical_display_verified=0\n",atomic_load(p->header+3),s.phase);return 0;
}
