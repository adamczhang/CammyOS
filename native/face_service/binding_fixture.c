/* SPDX-License-Identifier: MIT. Synthetic native-address trampolines, NOT LVGL.
 * Runs only offline in qemu-arm. The real face DSO uses saved device libc.
 */
#define _GNU_SOURCE
#include "../face/cammy_face_host.h"
#include "ipc_file.h"
#include "../ui/cammy_ui_page.h"
#include <fcntl.h>
#include <unistd.h>
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
static uint32_t wm[0xe4/4];
static void (*gui_tick)(void *,uint32_t);
static void tick(void){gui_tick(wm,0);gui_tick(wm,1);}
static int no_screen,no_top;
static int parent_token,screen_token,object_token,other_screen;
static void (*delete_cb)(void *);
static void (*click_cb)(void *);
static unsigned flag_calls;static int indev_token;static int32_t tap_point[2];
static void *object;
static uint32_t last_fnv; /* FNV-1a of the last canvas buffer */
/* Deliberate fixture-only clock interposition. Real app audit requires libc. */
__attribute__((visibility("default"))) int clock_gettime(clockid_t clock_id,struct timespec *ts) {
    if (clock_id!=CLOCK_MONOTONIC) return -1;
    ts->tv_sec=(time_t)(fake_ms/1000);ts->tv_nsec=(long)((fake_ms%1000)*1000000);
    return 0;
}
static void *screen(void *display) { (void)display; return no_screen?NULL:changed_screen?&other_screen:&screen_token; }
static void *top(void *display) { (void)display; return no_top?NULL:&parent_token; }
static void *create(void *parent) {
    if (parent!=&parent_token) abort();
    created++;object=&object_token;return object;
}
static void *add_event(void *obj,void (*cb)(void *),unsigned filter,void *data) {
    if (obj!=object || data || (filter!=33 && filter!=7)) abort();
    if (filter==7) { click_cb=cb;return &click_cb; }
    delete_cb=cb;return &delete_cb;
}
static void *get_target(void *event) { return event; }
static void add_flag(void *obj,uint32_t flag) { if (obj!=object || flag!=2) abort(); flag_calls++; }
static void *indev_act(void) { return &indev_token; }
static void indev_point(void *indev,int32_t *pt) { if (indev!=&indev_token) abort(); pt[0]=tap_point[0];pt[1]=tap_point[1]; }
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
    uint32_t h32=2166136261u;for(unsigned i=0;i<w*h*4;i++)h32=(h32^buf[i])*16777619u;
    last_fnv=h32;installed++;
}
/* One cammy-ui start: a fresh page file (new inode) renamed into place, as cammy_ui.c does,
 * showing menu buffer 0 with sequence n. */
static struct cui_header *ui_start(unsigned n) {
    char tmp[64];snprintf(tmp,sizeof tmp,"%s.%u",CUI_FILE,n);
    int fd=open(tmp,O_RDWR|O_CREAT|O_EXCL|O_CLOEXEC,0600);if(fd<0)return NULL;
    if(ftruncate(fd,(off_t)CUI_FILE_BYTES)){close(fd);return NULL;}
    void *m=mmap(NULL,CUI_FILE_BYTES,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);close(fd);if(m==MAP_FAILED)return NULL;
    struct cui_header *h=m;memset(cui_frame(h,0),0x40,CUI_FRAME_BYTES);
    atomic_store(&h->abi,CUI_ABI);atomic_store(&h->frame_seq,n);atomic_store(&h->show,1);
    atomic_store_explicit(&h->magic,CUI_MAGIC,memory_order_release);
    return rename(tmp,CUI_FILE)?NULL:h;
}
static void trampoline(uintptr_t address,uintptr_t function) {
    uint32_t code[2]={0xe51ff004,(uint32_t)function};
    memcpy((void *)address,code,sizeof(code));
}
int main(int argc,char **argv) {
    if(argc<2 || argc>3)return 2;
    unsigned damaged=argc==3?(unsigned)atoi(argv[2]):0u;
    void *map=mmap((void *)0x480000,0x40000,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0);
    if(map==MAP_FAILED)FAIL();
    trampoline(0x48f068,(uintptr_t)add_flag);trampoline(0x48bf60,(uintptr_t)indev_act);trampoline(0x48c174,(uintptr_t)indev_point);
    trampoline(0x48aad0,(uintptr_t)screen);trampoline(0x48ab5c,(uintptr_t)top);
    trampoline(0x4aaa3c,(uintptr_t)create);trampoline(0x496a6c,(uintptr_t)add_event);
    trampoline(0x4969bc,(uintptr_t)get_target);trampoline(0x496294,(uintptr_t)remove_object);
    trampoline(0x490838,(uintptr_t)set_x);trampoline(0x49088c,(uintptr_t)set_y);trampoline(0x4aaa7c,(uintptr_t)pixels);
    __builtin___clear_cache(map,(char *)map+0x40000);if(mprotect(map,0x40000,PROT_READ|PROT_EXEC))FAIL();
    void *lib=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);if(!lib){fprintf(stderr,"%s\n",dlerror());FAIL();}
    int (*attach)(const struct cmf_host *)=dlsym(lib,"cmf_attach_v1");
    void (*detach)(uint32_t)=dlsym(lib,"cmf_detach_v1");gui_tick=dlsym(lib,"cmf_gui_v1");if(!attach||!detach||!gui_tick)FAIL();
    const unsigned char hash[32]={0x87,0x71,0x96,0x1f,0xdb,0xa8,0x92,0x80,0x57,0x8c,0xe5,0x48,0x2a,0x6c,0xbe,0x4e,0xc3,0xf6,0xec,0x20,0x50,0x68,0x94,0x4b,0x34,0xf9,0x44,0x63,0x7d,0x62,0xb0,0xec};
    struct cmf_host host={1,sizeof(host),1,{0},NULL};memcpy(host.source_sha256,hash,32);
    if(attach(&host)!=-71)FAIL();
    host.profile=2;if(attach(&host)||attach(&host)!=-114)FAIL();
    struct cfs_page *page;if(cfs_map_file(CFS_FILE,0,&page))FAIL();
    struct cfs_command c={0};c.instance=(uint64_t)atomic_load(page->header+8)|((uint64_t)atomic_load(page->header+9)<<32);
    for(unsigned i=0;i<4;i++)c.boot[i]=atomic_load(page->header+4+i);
    uint32_t *http_slot=mmap((void *)0x00af0000,4096,PROT_READ|PROT_WRITE,
                             MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0);
    if(http_slot==MAP_FAILED)FAIL();
    uint32_t *http=http_slot+64,*context=http_slot+128,*presenter=http_slot+192;
    *http_slot=(uint32_t)(uintptr_t)http;
    http[0x6c/4]=(uint32_t)(uintptr_t)context;
    context[1]=(uint32_t)(uintptr_t)presenter;
    no_screen=1;tick();if(created)FAIL();
    no_screen=0;no_top=1;fake_ms++;tick();if(created)FAIL();
    no_top=0;fake_ms++;tick();fake_ms+=CFS_TRANSITION_MS;tick();
    fake_ms+=CFS_FIRST_HOLD_MS;tick(); /* 0.9.6 first-draw hold */
    if(damaged){
        const uint32_t child_mask=(1u<<CUI_CHILD_PACKS)-1u;
        if(created!=1 || installed || (atomic_load(page->header+CFS_HEADER_PACKS)&child_mask)!=2u)FAIL();
        fake_ms++;tick();
        if(damaged==1u){
            if(installed!=1 || !object || (atomic_load(page->header+CFS_HEADER_PACK)&255u)!=2u)FAIL();
        } else if(installed || object || atomic_load(page->header+10))FAIL();
        return 0;
    }
    if(created!=1||installed!=1||flag_calls!=1||!click_cb)FAIL();
    { struct cfs_status s;if(cfs_read_status(page,&s)||s.mode!=CFS_MODE_CHOOSING)FAIL(); }
    tap_point[0]=170;tap_point[1]=320;click_cb(object);tick(); /* tap the CammyOS half */
    /* Loading gate: the pick shows the built-in Loading frame (never the sleepy face). */
    { struct cfs_status s;if(cfs_read_status(page,&s)||s.mode!=CFS_MODE_CAMMY||s.taps!=1||installed!=2||s.phase!=CFS_VISIBLE)FAIL(); }
    if(atomic_load(page->header+CFS_HEADER_BOOT)!=CFS_BOOT_LOADING)FAIL();
    uint32_t loading_fnv=last_fnv;
    c.owner=33;c.generation=1;c.op=CFS_SHOW;
    /* CCC is applied, not refused or waited on; only the picture waits. */
    c.request++;c.expression=2;c.deadline=fake_ms+4000;if(cfs_publish(page,&c))FAIL();fake_ms++;tick();
    { struct cfs_status s;if(cfs_read_status(page,&s)||s.expression!=2||s.applied_request!=c.request||s.error||installed!=2)FAIL(); }
    /* cammy-ui's first start: its status frame shows, then its ready word opens the gate. */
    struct cui_header *first=ui_start(1);if(!first)FAIL();
    atomic_store(&first->boot,CUI_BOOT_LOADING);atomic_store(&first->heartbeat_ms,(uint32_t)fake_ms);
    fake_ms+=1001;atomic_store(&first->heartbeat_ms,(uint32_t)fake_ms);tick(); /* maps the page (one try a second) */
    fake_ms++;atomic_store(&first->heartbeat_ms,(uint32_t)fake_ms);tick();
    if(atomic_load(&first->ack_seq)!=1||installed!=3||atomic_load(page->header+CFS_HEADER_BOOT)!=CFS_BOOT_LOADING)FAIL();
    atomic_store(&first->show,0);atomic_store(&first->boot,CUI_BOOT_READY);atomic_store(&first->heartbeat_ms,(uint32_t)fake_ms);
    fake_ms++;tick();
    if(atomic_load(page->header+CFS_HEADER_BOOT)!=CFS_BOOT_READY||installed!=4||last_fnv==loading_fnv||created!=1)FAIL();
    for(unsigned i=0;i<6;i++){
        c.request++;c.expression=i;c.deadline=fake_ms+2000;if(cfs_publish(page,&c))FAIL();tick();
        struct cfs_status s;if(cfs_read_status(page,&s)||s.expression!=i||s.applied_request!=c.request||s.error)FAIL();fake_ms+=100;
    }
    c.request++;c.op=CFS_HIDE;c.expression=0;c.deadline=fake_ms+2000;if(cfs_publish(page,&c))FAIL();tick();if(object||removed!=1)FAIL();
    c.request++;c.op=CFS_SHOW;if(cfs_publish(page,&c))FAIL();tick();if(!object||created!=2)FAIL();
    /* The stock timer changes the active screen. The post-timer hook drops
     * the top-layer canvas before drawing, then waits for a stable pair. */
    gui_tick(wm,0);
    changed_screen=1;fake_ms++;gui_tick(wm,1);if(object||removed!=2)FAIL();
    fake_ms+=CFS_TRANSITION_MS-1;tick();if(object)FAIL();
    fake_ms++;tick();if(!object||created!=3)FAIL();
    /* A mode/window change can keep the same active screen. */
    ((unsigned char *)wm)[0xe1]=1;
    gui_tick(wm,0);if(object||removed!=3)FAIL();
    wm[0x78/4]=4;fake_ms+=500;tick();if(object)FAIL();
    ((unsigned char *)wm)[0xe1]=0;
    tick();if(object)FAIL();
    fake_ms+=CFS_TRANSITION_MS;tick();if(!object||created!=4)FAIL();
    /* A CCC grant shows Cammy over the default choice, while a missing bank refuses it. */
    uint32_t packs=atomic_load(page->header+CFS_HEADER_PACKS);int have_cammy=(packs&4u)!=0;
    if((packs&3u)!=3u||(atomic_load(page->header+CFS_HEADER_PACK)&0x1ffu)!=1u)FAIL();
    c.request++;c.op=CFS_SHOW;c.expression=0;c.pack=CFS_PACK_GRANT;c.deadline=fake_ms+2000;if(cfs_publish(page,&c))FAIL();
    tick();fake_ms+=CFS_WAKE_MS+1;tick();
    uint32_t w=atomic_load(page->header+CFS_HEADER_PACK),cammy_face=0,cammy_talk=0;
    if(!have_cammy){if((w&0x1ffu)!=1u||(w>>16)!=CFS_END_UNAVAILABLE)FAIL();}
    else {
        if((w&0x1ffu)!=0x103u)FAIL();
        cammy_face=last_fnv;
        c.request++;c.op=CFS_SPEAK;c.pack=CFS_PACK_HOLD;c.deadline=fake_ms+2000;if(cfs_publish(page,&c))FAIL();tick();
        cammy_talk=last_fnv;
        fake_ms+=2001;tick();
        w=atomic_load(page->header+CFS_HEADER_PACK);
        if((w&0x1ffu)!=3u||(w>>16)!=CFS_END_LEASE||last_fnv==cammy_talk)FAIL();
        c.generation++;
        c.request++;c.op=CFS_SHOW;c.pack=CFS_PACK_NONE;c.deadline=fake_ms+2000;if(cfs_publish(page,&c))FAIL();tick();fake_ms++;tick();
        w=atomic_load(page->header+CFS_HEADER_PACK);if((w&0x1ffu)!=1u||(w>>16)!=CFS_END_CCC)FAIL();
    }
    /* A physical Cammy pick works on the real renderer, even without a CCC grant.
     * CCC can temporarily show a child face. A physical pick takes priority again. */
    atomic_store(&first->face_pack,CUI_PACK_CAMMY+1u);atomic_fetch_add(&first->pack_seq,1u);
    fake_ms++;atomic_store(&first->heartbeat_ms,(uint32_t)fake_ms);tick();fake_ms++;tick();
    if((atomic_load(page->header+CFS_HEADER_PACK)&0x1ffu)!=(have_cammy?3u:1u))FAIL();
    if(have_cammy){
        c.request++;c.op=CFS_RENEW;c.pack=CFS_PACK_CHILD;c.deadline=fake_ms+2000;if(cfs_publish(page,&c))FAIL();
        tick();fake_ms++;tick();if((atomic_load(page->header+CFS_HEADER_PACK)&0x1ffu)!=1u)FAIL();
        c.request++;c.pack=CFS_PACK_HOLD;if(cfs_publish(page,&c))FAIL();
        atomic_fetch_add(&first->pack_seq,1u);tick();fake_ms++;tick();
        if((atomic_load(page->header+CFS_HEADER_PACK)&0x1ffu)!=3u)FAIL();
    }
    atomic_store(&first->face_pack,2u);atomic_fetch_add(&first->pack_seq,1u);tick();fake_ms++;tick();
    if((atomic_load(page->header+CFS_HEADER_PACK)&0x1ffu)!=2u)FAIL();
    /* Every allowed cammy-ui start (first + supervisor restarts) gets its menu shown; the
     * face maps no page beyond CUI_MAX_STARTS. */
    unsigned ui_mapped=1; /* the first start ran the loading gate */
    for(unsigned n=2;n<=CUI_MAX_STARTS+1;n++){
        struct cui_header *h=ui_start(n);if(!h)FAIL();
        fake_ms+=CUI_STALE_MS+1000;atomic_store(&h->heartbeat_ms,(uint32_t)fake_ms);
        tick(); /* old heartbeat stale: the face looks for a new page */
        fake_ms++;atomic_store(&h->heartbeat_ms,(uint32_t)fake_ms);unsigned before=installed;tick();
        uint32_t ack=atomic_load(&h->ack_seq);
        if(n<=CUI_MAX_STARTS){if(ack!=n||installed!=before+1||!object)FAIL();ui_mapped++;}
        else if(ack)FAIL();
    }
    if(ui_mapped!=CUI_MAX_STARTS||created!=4)FAIL();
    /* Exercise cmd 2008's work-mode chain independently of WindowManager.
     * All addresses here are synthetic mappings in this qemu process. */
    presenter[0xb4/4]=4;
    fake_ms+=100;tick();if(object||removed!=4)FAIL();
    fake_ms+=1000;tick();if(object)FAIL();
    presenter[0xb4/4]=0;
    fake_ms+=100;tick();if(object)FAIL();
    fake_ms+=CFS_TRANSITION_MS;tick();if(!object||created!=5)FAIL();
    context[1]=0; /* invalid pointer after a successful video sample */
    fake_ms+=99;tick();if(object||removed!=5)FAIL();
    detach(1);tick();tick();if(object||removed!=5||atomic_load(page->header+10))FAIL();
    printf("{\"native_service_binding_fixture\":true,\"created\":%u,\"installed\":%u,\"removed\":%u,\"ui_pages_mapped\":%u,\"cammy_pack\":%s,\"cammy_fnv\":[%u,%u],\"loading_fnv\":%u,\"physical_display_verified\":false}\n",created,installed,removed,ui_mapped,have_cammy?"true":"false",cammy_face,cammy_talk,loading_fnv);return 0;
}
