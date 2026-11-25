/* SPDX-License-Identifier: MIT. Synthetic native-address trampolines, NOT LVGL.
 * Runs only offline in qemu-arm. The real face DSO uses saved device libc.
 */
#define _GNU_SOURCE
#include "cammy_face_host.h"
#include <sys/mman.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <stdint.h>
#include <stdlib.h>
#define FAIL() do { fprintf(stderr,"fixture assertion line %d (created=%u installed=%u removed=%u)\n",__LINE__,created,installed,removed); return 1; } while (0)
static uint64_t fake_ms=1000;
static unsigned created,installed,removed,x_calls,y_calls;
static int mode;
static int changed_screen;
static int parent_token,screen_token,object_token,other_screen;
static void (*delete_cb)(void *);
static void *object;
/* Deliberate fixture-only clock interposition. Real app audit requires libc. */
__attribute__((visibility("default"))) int clock_gettime(clockid_t clock_id,struct timespec *ts) {
    if (clock_id!=CLOCK_MONOTONIC) return -1;
    ts->tv_sec=(time_t)(fake_ms/1000);ts->tv_nsec=(long)((fake_ms%1000)*1000000);
    return 0;
}
static void *screen(void *display) { (void)display; return changed_screen ? &other_screen : &screen_token; }
static void *top(void *display) { (void)display; return &parent_token; }
static void *create(void *parent) {
    if (parent!=&parent_token) abort();
    created++;object=&object_token;return object;
}
static void *add_event(void *obj,void (*cb)(void *),unsigned filter,void *data) {
    if (obj!=object || filter!=33 || data) abort();
    delete_cb=cb;return &delete_cb;
}
static void *get_target(void *event) { return event; }
static void remove_object(void *obj) {
    if (obj!=object) abort();
    removed++;delete_cb(object);object=NULL;
}
static void set_x(void *obj,int x) {
    if (obj!=object || x) abort();
    x_calls++;
    if (mode==4) { delete_cb(object);object=NULL; }
}
static void set_y(void *obj,int y) { if (obj!=object || y) abort();
    y_calls++; }
static void pixels(void *obj,const unsigned char *buf,unsigned w,unsigned h,unsigned cf) {
    if (obj!=object || !buf || w!=356 || h!=400 || cf!=4 || !delete_cb) abort();
    unsigned sum=0;for(unsigned i=0;i<w*h*4;i++)sum+=buf[i];
    if (!sum) abort();
    installed++;
}
static void trampoline(uintptr_t address,uintptr_t function) {
    uint32_t code[2]={0xe51ff004,(uint32_t)function};
    memcpy((void *)address,code,sizeof(code));
}
int main(int argc,char **argv) {
    if (argc!=3 && argc!=4) return 2;
    unsigned delay=argc==4?(unsigned)strtoul(argv[3],NULL,10):3000u;
    if (delay>90000u) return 2;
    mode=atoi(argv[2]);
    void *map=mmap((void *)0x480000,0x40000,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0);
    if (map==MAP_FAILED) { perror("mmap fixture");FAIL(); }
    trampoline(0x48aad0,(uintptr_t)screen);trampoline(0x48ab5c,(uintptr_t)top);
    trampoline(0x4aaa3c,(uintptr_t)create);trampoline(0x496a6c,(uintptr_t)add_event);
    trampoline(0x4969bc,(uintptr_t)get_target);trampoline(0x496294,(uintptr_t)remove_object);
    trampoline(0x490838,(uintptr_t)set_x);trampoline(0x49088c,(uintptr_t)set_y);
    trampoline(0x4aaa7c,(uintptr_t)pixels);
    __builtin___clear_cache(map,(char *)map+0x40000);
    if (mprotect(map,0x40000,PROT_READ|PROT_EXEC)) FAIL();
    void *lib=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);
    if (!lib) { fprintf(stderr,"%s\n",dlerror());FAIL(); }
    int (*attach)(const struct cmf_host *)=dlsym(lib,"cmf_attach_v1");
    void (*detach)(uint32_t)=dlsym(lib,"cmf_detach_v1");
    void (*tick)(void *,uint32_t)=dlsym(lib,"cmf_gui_v1");
    if (!attach || !detach || !tick) FAIL();
    const unsigned char hash[32]={0x87,0x71,0x96,0x1f,0xdb,0xa8,0x92,0x80,0x57,0x8c,0xe5,0x48,0x2a,0x6c,0xbe,0x4e,0xc3,0xf6,0xec,0x20,0x50,0x68,0x94,0x4b,0x34,0xf9,0x44,0x63,0x7d,0x62,0xb0,0xec};
    struct cmf_host host={1,sizeof(host),1,{0},NULL};memcpy(host.source_sha256,hash,32);
    if (attach(&host)!=-71) FAIL(); /* status profile must be refused */
    host.profile=2;if (attach(&host)) FAIL();
    if (attach(&host)!=-114) FAIL(); /* attach once per process */
    tick(NULL,0);if (created) FAIL();
    if (mode==1) detach(1);
    fake_ms=1000u+delay;tick(NULL,1);if (created) FAIL();
    tick(NULL,0);
    if (mode==1) { if (created) FAIL(); }
    else if (created!=1 || x_calls!=1 || installed!=(mode==4?0u:1u) || y_calls!=(mode==4?0u:1u)) FAIL();
    if (mode==2 && object) { delete_cb(object);object=NULL; }
    if (mode==3) changed_screen=1;
    if (mode==0) fake_ms=11000u+delay;
    else if (mode==5) detach(1);
    tick(NULL,0);tick(NULL,0);detach(1);detach(1);
    if (created>1 || removed>1) FAIL();
    if ((mode==0 || mode==3 || mode==5) && (object || removed!=1)) FAIL();
    if ((mode==1 || mode==2 || mode==4) && removed) FAIL();
    printf("{\"binding_fixture\":true,\"created\":%u,\"installed\":%u,\"removed\":%u,\"hardware_displayed\":false}\n",created,installed,removed);
    /* No dlclose: matches the loader's lifetime policy. */
    return 0;
}
