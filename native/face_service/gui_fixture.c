/* SPDX-License-Identifier: MIT. A SYNTHETIC GUI for loopback integration tests. */
#define _POSIX_C_SOURCE 200809L
#include "face_service.h"
#include "ipc_file.h"
#include <time.h>
#include <signal.h>
#include <unistd.h>
#include <stdio.h>
/* Pack readback as native_service.c publishes it (pack 0, or the CCC-only pack 2 while a
 * grant is held); "<control>.pick" stands for a physical pick on cammy-ui. */
static volatile sig_atomic_t alive=1,paused,changed;
static struct cfs_engine e;
static int obj,parent,screen_id[2];
static void sig(int n){if(n==SIGUSR1)paused=!paused;else if(n==SIGUSR2)changed=!changed;else alive=0;}
static void *screen(void *ctx){(void)ctx;return screen_id+changed;}
static void *top(void *ctx){(void)ctx;return &parent;}
static void *create(void *ctx,void *p){(void)ctx;(void)p;return &obj;}
static void pixels(void *ctx,void *o,const void *p){(void)ctx;(void)o;(void)p;}
static void remove_obj(void *ctx,void *o){(void)ctx;cfs_deleted(&e,o);}
static unsigned char packed[4];
static const unsigned char *decode(void *ctx,uint32_t id){(void)ctx;return packed+(id&3u);}
static uint64_t ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000u+(uint32_t)t.tv_nsec/1000000u;}
int main(int argc,char **argv){
    if(argc!=2)return 2;
    char pick[512];snprintf(pick,sizeof pick,"%s.pick",argv[1]);uint32_t shown=0;
    struct cfs_page *p;uint32_t boot[4];if(cfs_current_boot(boot)||cfs_map_file(argv[1],1,&p))return 3;
    cfs_page_init(p,12345,boot,(uint32_t)getpid());
    unsigned char fake_pixels[12]={0};const unsigned char *frames[12];for(unsigned i=0;i<12;i++)frames[i]=fake_pixels+i;
    const struct cfs_api api={NULL,screen,top,create,pixels,remove_obj,NULL,NULL,NULL,NULL,NULL};if(cfs_init(&e,p,&api,frames,ms(),0))return 4;
    struct sigaction action={0};action.sa_handler=sig;sigemptyset(&action.sa_mask);
    if(sigaction(SIGTERM,&action,NULL)||sigaction(SIGINT,&action,NULL)||sigaction(SIGUSR1,&action,NULL)||sigaction(SIGUSR2,&action,NULL))return 5;
    while(alive){
        if(!paused){
            if(!unlink(pick))cfs_adult_end(&e,CFS_END_PHYSICAL);
            cfs_tick(&e,ms());
            uint32_t want=(e.adult || (shown==2u && e.asleep && e.adult_end==CFS_END_LEASE))?2u:0u;
            if(want!=shown){if(want)(void)cfs_set_packed(&e,decode,NULL);else (void)cfs_set_pack(&e,frames,NULL,NULL,NULL);shown=want;}
            atomic_store(p->header+CFS_HEADER_PACK,(shown+1u)|e.adult<<8|(e.adult_end&0xffu)<<16);
            atomic_store(p->header+CFS_HEADER_PACKS,7u);
        }
        struct timespec dt={0,10000000};nanosleep(&dt,NULL);}
    cfs_detach(&e);return 0;
}
