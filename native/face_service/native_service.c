/* SPDX-License-Identifier: MIT. GUI-only operations; mapped Cammy IPC retained. */
#define _GNU_SOURCE
#include "face_service.h"
#include "ipc_file.h"
#include "../face/cammy_face_host.h"
#include "../ui/cammy_ui_page.h"
#include "face_pack_codec.h"
#include "frame_indexes.h"
#include <sys/mman.h>
#include <time.h>
#include <errno.h>
#include <string.h>
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/stat.h>
#define PUBLIC __attribute__((visibility("default")))
extern const unsigned char cfs_child_start[], cfs_child_end[];
extern const unsigned char cfs_cammy_pack_start[],cfs_cammy_pack_end[]; /* CPK1 bank (tools/cammy_pack.py) */
/* Loading frame (tools/build_face_service_assets.py): an alpha plane (one byte a pixel) and
 * one foreground colour per row (B,G,R,0), drawn over CFS_LOADING_BG. */
extern const unsigned char cfs_loading_alpha_start[],cfs_loading_rows_start[];
#define CFS_LOADING_BG_B 0x18u
#define CFS_LOADING_BG_G 0x13u
#define CFS_LOADING_BG_R 0x11u
static struct cfs_engine engine;
static const unsigned char *displayed;
static struct cfs_page *page;
static atomic_uint attached,published,detached;
static const unsigned char source_hash[32]={0x87,0x71,0x96,0x1f,0xdb,0xa8,0x92,0x80,0x57,0x8c,0xe5,0x48,0x2a,0x6c,0xbe,0x4e,0xc3,0xf6,0xec,0x20,0x50,0x68,0x94,0x4b,0x34,0xf9,0x44,0x63,0x7d,0x62,0xb0,0xec};
static int milliseconds(uint64_t *n){struct timespec t;if(clock_gettime(CLOCK_MONOTONIC,&t)||t.tv_sec<0||t.tv_nsec<0||t.tv_nsec>=1000000000L)return -1;*n=(uint64_t)t.tv_sec*1000u+(uint32_t)t.tv_nsec/1000000u;return 0;}
static void *screen(void *ctx){(void)ctx;return ((void *(*)(void *))(uintptr_t)0x48aad0)(NULL);}
static void *top(void *ctx){(void)ctx;return ((void *(*)(void *))(uintptr_t)0x48ab5c)(NULL);}
static void deleted(void *event){void *obj=((void *(*)(void *))(uintptr_t)0x4969bc)(event);
    if(obj==engine.object)displayed=NULL;
    cfs_deleted(&engine,obj);}

static void remove_obj(void *ctx,void *obj){(void)ctx;((void (*)(void *))(uintptr_t)0x496294)(obj);displayed=NULL;}
/* LVGL 8.3 in stock sdvcam (static analysis 2026-11-16): lv_indev_get_act 0x48bf60,
 * lv_indev_get_point(indev,int32_t[2]) 0x48c174, lv_obj_add_flag 0x48f068 (CLICKABLE=2),
 * event 7 = CLICKED. The callback only records the point; the next GUI tick acts. */
static void tapped(void *event){(void)event;int32_t pt[2]={0,0};
    void *indev=((void *(*)(void))(uintptr_t)0x48bf60)();
    if(indev)((void (*)(void *,int32_t *))(uintptr_t)0x48c174)(indev,pt);
    cfs_tap(&engine,pt[0],pt[1]);}
/* The GUI thread owns the mapping and copies each frame into its display
 * slots. A replacement can unmap the old page after mapping the new inode. */
static struct cui_header *ui_page;static uint64_t ui_retry;static ino_t ui_inode;static unsigned ui_maps;
static void ui_open(uint64_t now){
    if(now<ui_retry || ui_maps>=CUI_MAX_STARTS)return;
    ui_retry=now+1000u;
    int fd=open(CUI_FILE,O_RDWR|O_CLOEXEC|O_NOFOLLOW);
    if(fd<0)return;
    struct stat st;if(fstat(fd,&st)||!S_ISREG(st.st_mode)||st.st_size!=(off_t)CUI_FILE_BYTES||st.st_uid!=geteuid()||(ui_page&&st.st_ino==ui_inode)){close(fd);return;}
    /* Check the header before replacing the current mapping. */
    uint32_t head[2];
    if(pread(fd,head,sizeof head,0)!=(ssize_t)sizeof head||head[0]!=CUI_MAGIC||head[1]!=CUI_ABI){close(fd);return;}
    void *m=mmap(NULL,CUI_FILE_BYTES,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);close(fd);
    if(m==MAP_FAILED)return;
    if(ui_page && munmap(ui_page,CUI_FILE_BYTES)){munmap(m,CUI_FILE_BYTES);return;}
    ui_page=m;ui_inode=st.st_ino;ui_maps++;
}
/* The physical selection is the boot choice. CCC can override it during its face lease. */
static uint32_t saved_pack,pack_shown=UINT32_MAX;
/* All embedded art and UI frames share two process-lifetime slots. The slot
 * selected for writing is always different from the canvas' current source. */
static struct cpk_work cpk_work;
static unsigned char slots[2][CFS_PIXELS_BYTES];
static uint32_t slot_frame[2]={CPK_NONE,CPK_NONE},cammy_valid,cammy_broken,child_available,loading_broken;
static unsigned back_slot(void){return displayed==slots[0]?1u:0u;}
#define LOADING_KEY 0xfffffffeu
#define ADULT_KEY 0x80000000u
static const unsigned char *loading_frame(void *ctx){(void)ctx;
    for(unsigned k=0;k<2u;k++)if(slot_frame[k]==LOADING_KEY)return slots[k];
    unsigned k=back_slot();
    unsigned char *out=slots[k];
    unsigned char *a=out+(CPK_BGRA_BYTES-CPK_W*CPK_H);
    slot_frame[k]=CPK_NONE;
    if(cpk_lz(cfs_loading_alpha_start,LOADING_PACKED_BYTES,a,CPK_W*CPK_H)){
        loading_broken=1;
        return NULL;
    }
    for(uint32_t y=0,i=0;y<CPK_H;y++){
        const unsigned char *fg=cfs_loading_rows_start+(size_t)y*4u;
        for(uint32_t x=0;x<CPK_W;x++,i++){
            uint32_t alpha=a[i],bg[3]={CFS_LOADING_BG_B,CFS_LOADING_BG_G,CFS_LOADING_BG_R};
            for(unsigned c=0;c<3u;c++){uint32_t v=bg[c]*(255u-alpha)+fg[c]*alpha+128u;out[i*4u+c]=(unsigned char)((v+(v>>8))>>8);}
            out[i*4u+3u]=0xffu;
        }
    }
    slot_frame[k]=LOADING_KEY;
    return slots[k];
}
static int cammy_visible(void){
    return cammy_valid && !cammy_broken && !engine.child_override &&
           (saved_pack==CUI_PACK_CAMMY || engine.adult ||
            (engine.asleep && engine.adult_end==CFS_END_LEASE && pack_shown==CUI_PACK_CAMMY));
}
static const unsigned char *packed_frame(void *ctx,uint32_t id){(void)ctx;
    unsigned child=pack_shown==1u;
    uint32_t index;
    if(id-CFS_CHOOSER_ID(1)<2u){
        if(!(child_available&(1u<<child)))child^=1u;
        index=(child?CHILD_FINFUR_CHOOSER:CHILD_CHOOSER)+1u+(id-CFS_CHOOSER_ID(1));
    } else if(pack_shown==CUI_PACK_CAMMY){
        if(id<CFS_FRAMES && id/2u!=CFS_SLEEPY)index=(id&1u)?ADULT_BLINK:id>>1;
        else if(id-0x08000000u<2u)index=ADULT_TALK+(id-0x08000000u);
        else if(id-CFS_SLEEP_ID(0)<CFS_SLEEP_FRAMES)index=ADULT_SLEEP+(id-CFS_SLEEP_ID(0));
        else return NULL;
        index|=ADULT_KEY;
    } else if(id<CFS_FRAMES)index=(child?CHILD_FINFUR:CHILD_FRAMES_START)+id;
    else if(child && id-CFS_TURN_ID<CFS_FRAMES)index=CHILD_FINFUR+id;
    else if(id-0x08000000u<(child?4u:2u))index=(child?CHILD_FINFUR_TALK:CHILD_TALK)+(id-0x08000000u);
    else if(id-CFS_SLEEP_ID(0)<CFS_SLEEP_FRAMES)index=(child?CHILD_FINFUR_SLEEP:CHILD_SLEEP)+(id-CFS_SLEEP_ID(0));
    else return NULL;

    if(!(index&ADULT_KEY)){
        if(!(child_available&(1u<<child)))return NULL;
        index=child_frame_index[index];
        if(index==CPK_NONE)return NULL;
    }
    for(unsigned k=0;k<2u;k++)if(slot_frame[k]==index)return slots[k];
    unsigned k=back_slot();
    slot_frame[k]=CPK_NONE;
    if(index&ADULT_KEY){
        if(cpk_decode(cfs_cammy_pack_start,index&~ADULT_KEY,&cpk_work,slots[k])){
            cammy_broken=1;
            return NULL;
        }
    } else if(cpk_decode(cfs_child_start,index,&cpk_work,slots[k])){
        child_available&=~(1u<<child);
        return NULL;
    }
    slot_frame[k]=index;
    return slots[k];
}
static void pick_pack(void){
    if(engine.adult && (!cammy_valid || cammy_broken))cfs_adult_end(&engine,CFS_END_UNAVAILABLE);
    uint32_t want=cammy_visible()?CUI_PACK_CAMMY:saved_pack<CUI_CHILD_PACKS?saved_pack:0u;
    if(want<CUI_CHILD_PACKS && !(child_available&(1u<<want)))want^=1u;
    if(want<CUI_CHILD_PACKS && !(child_available&(1u<<want)))want=UINT32_MAX;
    if(want!=pack_shown){
        cfs_set_packed(&engine,packed_frame,NULL);
        engine.turns=want==1u;
        pack_shown=want;
    }
    uint32_t shown=pack_shown<CUI_PACK_COUNT?pack_shown+1u:0u;
    atomic_store_explicit(page->header+CFS_HEADER_PACK,shown|engine.adult<<8|(engine.adult_end&0xffu)<<16,memory_order_release);
    atomic_store_explicit(page->header+CFS_HEADER_PACKS,child_available|(cammy_valid&&!cammy_broken?1u<<CUI_PACK_CAMMY:0u),memory_order_release);
    if(ui_page)atomic_store_explicit(&ui_page->face_shown,shown,memory_order_release);
}
static uint32_t boot_pack(void){
    char id[40];int fd=open("/etc/cammy/face-pack",O_RDONLY|O_CLOEXEC|O_NOFOLLOW);if(fd<0)return 0;
    ssize_t n=read(fd,id,sizeof id);close(fd);
    while(n>0 && (id[n-1]=='\n'||id[n-1]==' '||id[n-1]=='\r'||id[n-1]=='\t'))n--;
    int i=n>0?cui_pack_index(id,(unsigned)n):-1;return i<0?0u:(uint32_t)i;
}
/* A physical pick ends the CCC override. A cammy-ui restart only sets the baseline. */
static uint32_t ui_seen_maps,ui_seen_seq,ui_seen_pack;
static void ui_pack(uint64_t now){
    if(!ui_page || (uint32_t)now-atomic_load_explicit(&ui_page->heartbeat_ms,memory_order_acquire)>CUI_STALE_MS)return;
    uint32_t q=atomic_load_explicit(&ui_page->pack_seq,memory_order_acquire);
    uint32_t v=atomic_load_explicit(&ui_page->face_pack,memory_order_acquire);
    if(ui_seen_maps!=ui_maps){ui_seen_maps=ui_maps;ui_seen_seq=q;ui_seen_pack=v;}
    else if(q!=ui_seen_seq || (ui_seen_pack && v!=ui_seen_pack)){ui_seen_seq=q;ui_seen_pack=v;cfs_adult_end(&engine,CFS_END_PHYSICAL);}
    if(v && v<=CUI_PACK_COUNT)saved_pack=v-1u;
}
static int ui(void *ctx,uint64_t now,const unsigned char **px,uint32_t *seq,uint32_t *camera){(void)ctx;
    uint32_t age=ui_page?(uint32_t)now-atomic_load_explicit(&ui_page->heartbeat_ms,memory_order_acquire):UINT32_MAX;
    if(age>CUI_STALE_MS){ui_open(now);return 0;}
    *camera=atomic_load_explicit(&ui_page->camera_mode,memory_order_acquire)==1u;
    /* A closed menu still acknowledges its latest frame: cammy-ui publishes again only
     * after an ack, and sets show only after a publish succeeds. */
    if(atomic_load_explicit(&ui_page->show,memory_order_acquire)!=1u){
        atomic_store_explicit(&ui_page->ack_seq,atomic_load_explicit(&ui_page->frame_seq,memory_order_acquire),memory_order_release);return 0;}
    uint32_t s=atomic_load_explicit(&ui_page->frame_seq,memory_order_acquire);
    if(!s)return 0;
    static unsigned menu_map;
    uint32_t id=CFS_MENU_FRAME|(s&(CFS_MENU_FRAME-1u));
    if(engine.current_frame!=id || menu_map!=ui_maps){
        unsigned k=back_slot();
        slot_frame[k]=CPK_NONE;
        memcpy(slots[k],cui_frame(ui_page,atomic_load_explicit(&ui_page->frame_index,memory_order_acquire)),CFS_PIXELS_BYTES);
        *px=slots[k];
        engine.current_frame=UINT32_MAX;
        menu_map=ui_maps;
    } else *px=displayed;
    *seq=s;
    atomic_store_explicit(&ui_page->ack_seq,s,memory_order_release);
    return 1;
}
static int live_view(void *ctx,uint64_t now){(void)ctx;
    if(!ui_page||(uint32_t)now-atomic_load_explicit(&ui_page->heartbeat_ms,memory_order_acquire)>CUI_STALE_MS)return 0;
    /* The menu wins: OK in the live view opens it over the camera picture. */
    return atomic_load_explicit(&ui_page->live_view,memory_order_acquire)==1u&&atomic_load_explicit(&ui_page->show,memory_order_acquire)!=1u;}
/* Loading gate readback: no fresh page, still loading, or ready. A fresh page without the
 * boot word comes from a cammy-ui that predates the gate (which started only once the
 * supervisor reached idle_ready), so it counts as ready. */
static int ui_boot(void *ctx,uint64_t now){(void)ctx;
    if(!ui_page||(uint32_t)now-atomic_load_explicit(&ui_page->heartbeat_ms,memory_order_acquire)>CUI_STALE_MS){ui_open(now);return -1;}
    return atomic_load_explicit(&ui_page->boot,memory_order_acquire)==CUI_BOOT_LOADING?0:1;}
/* Stock objects whose lifetimes are owned by another thread are read
 * through procfs. A stale, null or unmapped address returns unknown. */
static int memory_fd(uint64_t now){
    static uint64_t retry;
    static int fd=-1;
    if(fd<0 && now>=retry){
        fd=open("/proc/self/mem",O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
        if(fd<0)retry=now+5000u;
    }
    return fd;
}
static int read_stock_word(int fd,uint32_t base,uint32_t offset,uint32_t *out){
    if(fd<0 || !base || (base&3u) || base>INT32_MAX-offset)return -1;
    return pread(fd,out,sizeof(*out),(off_t)(base+offset))==(ssize_t)sizeof(*out)?0:-1;
}
/* SettingCtrl singleton -> settings -> stock face-follow flag. */
static int tracking(void *ctx){(void)ctx;
    static uint64_t due;
    static int tracked=-1;
    uint64_t now=engine.last_now;
    if(now<due)return tracked;
    due=now+250u;
    int fd=memory_fd(now);
    uint32_t inst,sys,value;
    if(read_stock_word(fd,0xaef24cu,0,&inst) || read_stock_word(fd,inst,92u,&sys) ||
       read_stock_word(fd,sys,44u,&value) || value>1u)tracked=-1;
    else tracked=(int)value;
    return tracked;
}
static void clickable(void *ctx,void *obj){(void)ctx;
    void *event=((void *(*)(void *,void (*)(void *),unsigned,void *))(uintptr_t)0x496a6c)(obj,tapped,7,NULL);
    if(event && engine.object==obj)((void (*)(void *,uint32_t))(uintptr_t)0x48f068)(obj,2u);
}
static void *create(void *ctx,void *parent){(void)ctx;void *obj=((void *(*)(void *))(uintptr_t)0x4aaa3c)(parent);if(!obj)return NULL;
    if(!((void *(*)(void *,void (*)(void *),unsigned,void *))(uintptr_t)0x496a6c)(obj,deleted,33,NULL)){remove_obj(NULL,obj);return NULL;}return obj;}
static void pixels(void *ctx,void *obj,const void *p){(void)ctx;
    if(!displayed){
        ((void (*)(void *,int))(uintptr_t)0x490838)(obj,0);if(engine.object!=obj)return;
        ((void (*)(void *,int))(uintptr_t)0x49088c)(obj,0);if(engine.object!=obj)return;
    }
    ((void (*)(void *,const void *,unsigned,unsigned,unsigned))(uintptr_t)0x4aaa7c)(obj,p,356,400,4);
    if(engine.object==obj)displayed=p;
}
PUBLIC int cmf_attach_v1(const struct cmf_host *host){
    if(!host||host->abi_version!=CMF_HOST_ABI||host->struct_size!=sizeof(*host)||host->profile!=CMF_HOST_PROFILE||memcmp(host->source_sha256,source_hash,32))return -EPROTO;
    if(atomic_exchange_explicit(&attached,1,memory_order_acq_rel))return -EALREADY;
    uint64_t now,instance;uint32_t boot[4];if(milliseconds(&now)||cfs_current_boot(boot))return -EIO;
    int fd=open("/dev/urandom",O_RDONLY|O_CLOEXEC);if(fd<0)return -EIO;
    ssize_t n=read(fd,&instance,sizeof(instance));close(fd);if(n!=(ssize_t)sizeof(instance)||!instance)return -EIO;
    if(mkdir(CFS_DIRECTORY,0700) && errno!=EEXIST)return -EIO;
    if(cfs_map_file(CFS_FILE,1,&page))return -EIO;
    cfs_page_init(page,instance,boot,(uint32_t)getpid());
    const struct cfs_api api={NULL,screen,top,create,pixels,remove_obj,clickable,ui,tracking,live_view,ui_boot};
    /* Let the first GUI tick with a valid screen and top layer draw the chooser. */
    if(cfs_init(&engine,page,&api,NULL,now,0)){atomic_store(page->header+10,0);return -EINVAL;}
    atomic_store_explicit(page->header+CFS_HEADER_CAPTURE,2u,memory_order_release);
    cfs_enable_loading(&engine,loading_frame,NULL);
    cpk_work.key_index=CPK_NONE;
    if(!cpk_check_count(cfs_child_start,(size_t)(cfs_child_end-cfs_child_start),CHILD_FRAMES))
        child_available=(1u<<CUI_CHILD_PACKS)-1u;
    if(!child_available){atomic_store(page->header+10,0);return -EINVAL;}
    cammy_valid=!cpk_check(cfs_cammy_pack_start,(size_t)(cfs_cammy_pack_end-cfs_cammy_pack_start));
    saved_pack=boot_pack();pick_pack();cfs_enable_chooser(&engine,NULL);
    atomic_store_explicit(&published,1,memory_order_release);return 0;
}
PUBLIC void cmf_detach_v1(uint32_t reason){(void)reason;atomic_store_explicit(&detached,1,memory_order_release);if(page)atomic_store_explicit(page->header+10,0,memory_order_release);}
/* Chooser keys: event0 read without a grab (stock keeps its own reader), only while
 * choosing; raw 16-byte time32 input_event: type@8 code@10 value@12. */
static int key_fd=-1;static uint64_t key_retry;
static void keys(uint64_t now){
    if(engine.state.mode!=CFS_MODE_CHOOSING){if(key_fd>=0){close(key_fd);key_fd=-1;}return;}
    if(key_fd<0){if(now<key_retry)return;key_retry=now+1000u;key_fd=open("/dev/input/event0",O_RDONLY|O_NONBLOCK|O_CLOEXEC);if(key_fd<0)return;}
    unsigned char ev[16*16];ssize_t n=read(key_fd,ev,sizeof ev);
    for(ssize_t i=0;i+16<=n;i+=16){
        unsigned type=(unsigned)ev[i+8]|(unsigned)ev[i+9]<<8,code=(unsigned)ev[i+10]|(unsigned)ev[i+11]<<8;
        if(type==1u && ev[i+12]==1u && !ev[i+13] && !ev[i+14] && !ev[i+15])cfs_key(&engine,code);
    }
}
/* Stock cmd 2008 uses HTTP singleton +0x6c -> context +4 -> presenter
 * +0xb4 (0x8701c..0x87030). These pointers are published by other threads.
 * Sample with kernel-checked reads instead of dereferencing their lifetimes.
 * If the chain cannot be read, the renderer must leave stock in control. */
static int stock_mode_blocked(uint64_t now,int force){
    static uint64_t sampled;
    static int blocked=1;
    /* With the 33 ms GUI cadence, a 65 ms threshold samples every second
     * cycle and remains within 100 ms. A WM transition invalidates the cache. */
    if(!force && sampled && now>=sampled && now-sampled<65u)return blocked;
    sampled=now;
    int fd=memory_fd(now);
    uint32_t http,ctx,presenter,mode;
    blocked=read_stock_word(fd,0x00af0000u,0,&http) || read_stock_word(fd,http,0x6cu,&ctx) ||
            read_stock_word(fd,ctx,4u,&presenter) || read_stock_word(fd,presenter,0xb4u,&mode) || mode!=0u;
    return blocked;
}

/* USB device state while choosing, sampled every 250 ms from the UDC (kernel sysfs,
 * nonblocking). Only the chooser timing reads it. */
#define UDC_STATE "/sys/class/udc/4100000.udc-controller/state"
static void usb_sample(uint64_t now){
    static uint64_t next;
    if(engine.state.mode!=CFS_MODE_CHOOSING || now<next)return;
    next=now+250u;
    char s[24]={0};uint32_t state=CFS_USB_UNKNOWN;
    int fd=open(UDC_STATE,O_RDONLY|O_CLOEXEC|O_NONBLOCK);
    if(fd>=0){ssize_t n=read(fd,s,sizeof s-1);close(fd);
        if(n>0){s[n]=0;state=!strncmp(s,"not attached",12)?CFS_USB_DETACHED:!strncmp(s,"configured",10)?CFS_USB_CONFIGURED:CFS_USB_ATTACHED;}}
    cfs_usb(&engine,now,state);
}
/* One small tmpfs write when the chooser is resolved: the boot's chooser timeline,
 * with both clocks so it lines up with the stock log. */
static void timeline(uint64_t now){
    static int written;
    if(written || (engine.state.mode==CFS_MODE_CHOOSING && !engine.detached))return;
    written=1;
    struct timespec rt;if(clock_gettime(CLOCK_REALTIME,&rt))rt.tv_sec=rt.tv_nsec=0;
    char b[64+CFS_MARKS*64];
    int n=snprintf(b,sizeof b,"format=cammy-chooser-timeline/1\nmono_ms=%llu\nreal_ms=%llu\nfirst=%u/%u\nusb=%u\n",
        (unsigned long long)now,(unsigned long long)rt.tv_sec*1000ull+(unsigned long long)(rt.tv_nsec/1000000),
        engine.first_window,engine.first_overlay,engine.usb);
    for(uint32_t i=0;i<engine.marks && n>0 && (size_t)n<sizeof b-64;i++){const struct cfs_mark *m=&engine.mark[i];
        n+=snprintf(b+n,sizeof b-(size_t)n,"mark=%llu %u %u %u %u\n",(unsigned long long)m->ms,m->what,m->window,m->overlay,m->value);}
    if(n<=0 || (size_t)n>=sizeof b)return;
    int fd=open(CFS_DIRECTORY "/chooser-timeline",O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC|O_NOFOLLOW,0600);
    if(fd>=0){if(write(fd,b,(size_t)n)<0){}close(fd);}
}
PUBLIC void cmf_gui_v1(void *manager,uint32_t phase){
    if(!atomic_load_explicit(&published,memory_order_acquire) || engine.detached)return;
    uint64_t now;if(atomic_load_explicit(&detached,memory_order_acquire)||milliseconds(&now)){cfs_detach(&engine);return;}
    /* Exact stock build: ChangeWindow (0xe45c8) sets +0xe1 before queuing the
     * work. +0x78 and +0x7c are its two window selectors. r5 at 0xe58c0
     * is the live WindowManager, under its own GUI lock. */
    if(!manager)return;
    const volatile unsigned char *wm=manager;
    uint32_t window=*(const volatile uint32_t *)(wm+0x78);
    uint32_t overlay=*(const volatile uint32_t *)(wm+0x7c);
    static uint32_t last_window,last_overlay;
    static unsigned last_busy;
    unsigned busy=wm[0xe1]!=0u;
    int changed=window!=last_window || overlay!=last_overlay || busy!=last_busy;
    last_window=window;last_overlay=overlay;last_busy=busy;
    cfs_observe(&engine,now,window,overlay,(busy?CFS_BUSY_WM:0)|(stock_mode_blocked(now,changed)?CFS_BUSY_MODE:0));
    if(!phase)return;
    usb_sample(now);keys(now);ui_pack(now);cfs_tick(&engine,now);pick_pack();timeline(now);
    if(loading_broken || pack_shown==UINT32_MAX ||
       (!child_available && engine.state.mode==CFS_MODE_CHOOSING))cfs_detach(&engine);
}
/* Native library, shared mapping and immutable frame bank are never freed or
 * dlclosed. Detach is an atomic request; physical removal requires GUI progress. */
