/* SPDX-License-Identifier: MIT. Synthetic GUI objects; no hardware/device I/O. */
#include "face_service.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#define CHECK(x) do { checks++;if(!(x)){fprintf(stderr,"check %u line%d\n",checks,__LINE__);exit(1);} } while(0)
static unsigned checks;
static struct cfs_engine e;
static struct cfs_page page;
static unsigned created,removed,drawn,create_fails,failed_creates;
static int screen_id,parent,object,deleted_on_pixels,deleted_on_clickable;
static const unsigned char data[12]={1,2,3,4,5,6,7,8,9,10,11,12};
static const void *last_pixels;
static unsigned char menu[3u*CFS_PIXELS_BYTES]; /* chooser bank: 3 focus frames */
static unsigned clicks;
static int ui_show;static uint32_t ui_seq,ui_camera;static const unsigned char menubuf[2][4];
static int ui(void *ctx,uint64_t now,const unsigned char **px,uint32_t *seq,uint32_t *camera){(void)ctx;(void)now;*camera=ui_camera;if(!ui_show)return 0;*px=menubuf[ui_seq&1u];*seq=ui_seq;return 1;}
static int ui_live,ui_track=-1;
static int live(void *ctx,uint64_t now){(void)ctx;(void)now;return ui_live;}
static int track(void *ctx){(void)ctx;return ui_track;}
/* Loading gate stand-ins: cammy-ui's readback (-1 none, 0 loading, 1 ready) and the frame. */
static int ui_boot_word=-1;static unsigned loading_makes;static const unsigned char loading_buf[4];
static int boot_word(void *ctx,uint64_t now){(void)ctx;(void)now;return ui_boot_word;}
static const unsigned char *loading(void *ctx){CHECK(ctx==&loading_makes);loading_makes++;return loading_buf;}
static void clickable(void *ctx,void *obj){
    (void)ctx;CHECK(obj==&object);clicks++;
    if(deleted_on_clickable)cfs_deleted(&e,obj);
}
static void *screen(void *ctx){(void)ctx;return &screen_id+screen_id;}
static void *top(void *ctx){(void)ctx;return &parent;}
static void *create(void *ctx,void *par){(void)ctx;CHECK(par==&parent);if(create_fails){failed_creates++;return NULL;}created++;return &object;}
static void pixels(void *ctx,void *obj,const void *p){(void)ctx;CHECK(obj==&object);drawn++;last_pixels=p;if(deleted_on_pixels)cfs_deleted(&e,obj);}
static void remove_obj(void *ctx,void *obj){(void)ctx;CHECK(obj==&object);removed++;cfs_deleted(&e,obj);}
static struct cfs_command command(uint64_t id,unsigned op,unsigned mood) {
    struct cfs_command c={id,33,1,4000,123,{1,2,3,4},op,mood,CFS_PACK_NONE};return c;
}
static void reset(void) {
    created=removed=drawn=clicks=create_fails=failed_creates=0;ui_show=0;ui_boot_word=-1;loading_makes=0;ui_seq=0;ui_camera=0;ui_live=0;ui_track=-1;screen_id=deleted_on_pixels=deleted_on_clickable=0;last_pixels=NULL;
    const uint32_t boot[4]={1,2,3,4};const unsigned char *frames[12];for(unsigned i=0;i<12;i++)frames[i]=data+i;
    const struct cfs_api api={NULL,screen,top,create,pixels,remove_obj,clickable,ui,track,live,boot_word};
    cfs_page_init(&page,123,boot,42);CHECK(cfs_init(&e,&page,&api,frames,1000,100)==0);
}
static void put(struct cfs_command c,uint64_t now){CHECK(cfs_publish(&page,&c)==0);cfs_tick(&e,now);}
static unsigned char packed_px[4];static uint32_t decoded_id,decodes;static int decode_fail;
static const unsigned char *decode(void *ctx,uint32_t id){CHECK(ctx==&packed_px);decodes++;decoded_id=id;return decode_fail?NULL:packed_px+(id&3u);}
static struct cfs_command owned(uint64_t id,unsigned op,uint64_t owner,uint64_t generation,unsigned pack){
    struct cfs_command c=command(id,op,1);c.owner=owner;c.generation=generation;c.pack=pack;return c;}
static void *writer(void *arg){uint64_t base=(uint64_t)(uintptr_t)arg;
    for(uint64_t i=1;i<20001;i++){
        struct cfs_command c=command(base+i,CFS_SHOW,(unsigned)((base+i)%6));
        c.owner=c.request^0x55u;c.generation=c.request+1;c.deadline=c.request+5000;
        while(cfs_publish(&page,&c)){};
    }return NULL;
}
int main(void) {
    reset();cfs_tick(&e,1099);CHECK(!created);cfs_tick(&e,1100);CHECK(created==1 && drawn==1);
    cfs_tick(&e,1200);CHECK(drawn==1);CHECK(last_pixels==data+11); /* asleep: sleepy, eyes closed */
    struct cfs_command c=command(1,CFS_SHOW,2);put(c,1300);CHECK(created==1 && last_pixels==data+5); /* waking: eyes still shut */
    cfs_tick(&e,1300+CFS_WAKE_MS);CHECK(last_pixels==data+4);
    c=command(2,CFS_HIDE,0);put(c,1600);CHECK(!e.object && removed==1);
    c=command(3,CFS_SHOW,3);put(c,1700);CHECK(created==2 && last_pixels==data+6);
    c=command(4,CFS_STOP,0);c.owner=0;c.generation=2;put(c,1800);CHECK(e.state.idle && !e.state.owner);CHECK(last_pixels==data+11); /* Stop: back to sleep */
    c=command(5,CFS_SHOW,1);put(c,1900);CHECK(e.state.error==CFS_STALE && e.state.idle);
    cfs_detach(&e);CHECK(!e.object && e.state.phase==CFS_DETACHED && !atomic_load(page.header+10));
    CHECK(cfs_publish(&page,&c)!=0);
    reset();c=command(1,CFS_SHOW,1);c.deadline=1000;put(c,1100);CHECK(!e.state.owner && e.state.error==CFS_EXPIRED);
    reset();c=command(1,CFS_SHOW,1);put(c,1100);c=command(2,CFS_RENEW,0);c.deadline=8000;put(c,4000);
    CHECK(e.state.error==CFS_BAD_OWNER && !e.state.owner); /* no late renew resurrection */
    reset();c=command(1,CFS_SHOW,1);c.instance=999;put(c,1100);CHECK(e.state.error==CFS_BAD_BINDING && !e.state.owner);
    c=command(1,CFS_SHOW,1);c.boot[1]=999;put(c,1200);CHECK(e.state.error==CFS_BAD_BINDING);
    c=command(1,CFS_SHOW,1);put(c,1300);CHECK(e.state.owner==33); /* bad binding didn't consume id */
    c=command(1,CFS_HIDE,0);put(c,1400);CHECK(e.object); /* replay ignored */
    reset();cfs_tick(&e,1100);screen_id=1;cfs_tick(&e,1200);CHECK(e.suspended && !e.object && removed==1);
    cfs_tick(&e,1300);CHECK(created==1);c=command(1,CFS_SHOW,1);put(c,1400);CHECK(created==2 && !e.suspended);
    cfs_deleted(&e,e.object);cfs_tick(&e,1500);CHECK(e.suspended && !e.object && created==2);
    reset();deleted_on_pixels=1;cfs_tick(&e,1100);CHECK(!e.object && e.suspended && e.state.phase==CFS_SUSPENDED);
    /* No owner: asleep (sleepy, eyes closed) and no mood cycling. */
    reset();cfs_tick(&e,1100);CHECK(last_pixels==data+11);cfs_tick(&e,9100);CHECK(last_pixels==data+11 && drawn==1);
    /* An owner's explicit idle loop cycles the moods and blinks; renewals keep it awake. */
    /* An owner's idle is a settled neutral (no mood carousel): waking opens the eyes after
     * CFS_WAKE_MS, then varied blinks, now and then a double; renewals keep it awake. */
    reset();c=command(1,CFS_IDLE,0);c.deadline=6000;put(c,1100);CHECK(last_pixels==data+1 && e.state.idle && e.state.expression==0);
    cfs_tick(&e,1100+CFS_WAKE_MS);CHECK(last_pixels==data);
    { uint64_t last_close=0,min_gap=~0ull,max_gap=0;unsigned blinks=0,doubles=0,closed=0;uint64_t req=1;
      for(uint64_t t=1100+CFS_WAKE_MS+10;t<=1100+300000;t+=10){
        if(t%1000==0){c=command(++req,CFS_RENEW,0);c.deadline=t+4000;CHECK(cfs_publish(&page,&c)==0);}
        cfs_tick(&e,t);CHECK(last_pixels==data || last_pixels==data+1);CHECK(e.state.owner==33 && e.state.expression==0 && e.state.idle);
        unsigned now_closed=last_pixels==data+1;
        if(now_closed && !closed){blinks++;if(last_close){uint64_t gap=t-last_close;if(gap<1000)doubles++;else{if(gap<min_gap)min_gap=gap;if(gap>max_gap)max_gap=gap;}}last_close=t;}
        closed=now_closed;
      }
      CHECK(blinks>=40 && blinks<=130 && doubles>=2 && doubles<blinks/3);
      CHECK(min_gap>=CFS_BLINK_GAP_MS && max_gap<=CFS_BLINK_GAP_MS+4096+CFS_BLINK_MS+64+10 && max_gap-min_gap>=2000);
      fprintf(stderr,"idle 5 min: %u blinks, %u doubles, gaps %llu..%llu ms\n",blinks,doubles,(unsigned long long)min_gap,(unsigned long long)max_gap);
    }
    /* Lease expiry from neutral idle: back to the sleep pose. */
    cfs_tick(&e,1100+300000+5000);CHECK(!e.state.owner && last_pixels==data+11 && e.state.expression==CFS_SLEEPY);
    reset();cfs_tick(&e,1100);cfs_tick(&e,1099);CHECK(e.detached && !e.object);
    reset();atomic_store(page.command,1);struct cfs_command out;CHECK(cfs_read_command(&page,&out)!=0);
    cfs_tick(&e,1100);CHECK(e.object);atomic_store(page.command,0);
    reset();c=command(1,CFS_SHOW,6);put(c,1100);CHECK(e.state.error==CFS_INVALID && !e.state.owner);
    reset();e.talk=menu;c=command(1,CFS_SPEAK,0);put(c,1100);CHECK(last_pixels==menu);
    cfs_tick(&e,1280);CHECK(last_pixels==menu+CFS_PIXELS_BYTES);
    c=command(2,CFS_RENEW,0);put(c,1460);CHECK(last_pixels==menu && e.speaking);
    c=command(3,CFS_SHOW,0);put(c,1480);CHECK(last_pixels==data && !e.speaking);
    /* The vendor may run deletion while we register the canvas for clicks. */
    reset();cfs_enable_chooser(&e,menu);deleted_on_clickable=1;cfs_tick(&e,1100);
    CHECK(!e.object && clicks==1 && drawn==0 && e.state.phase==CFS_SUSPENDED);
    deleted_on_clickable=0;cfs_tick(&e,1200);
    CHECK(e.object && clicks==2 && drawn==1 && e.state.mode==CFS_MODE_CHOOSING);

    /* Boot chooser: drawn first and tappable; commands wait for a choice. */
    reset();cfs_enable_chooser(&e,menu);cfs_tick(&e,1100);CHECK(last_pixels==menu+2u*CFS_PIXELS_BYTES && clicks==1 && e.state.mode==CFS_MODE_CHOOSING);
    c=command(1,CFS_SHOW,1);put(c,1200);CHECK(e.state.error==CFS_NOT_CAMMY && !e.state.owner && last_pixels==menu+2u*CFS_PIXELS_BYTES);
    screen_id=1;cfs_tick(&e,1300);CHECK(e.object && removed==0 && last_pixels==menu+2u*CFS_PIXELS_BYTES); /* stock window change: stays */
    cfs_tap(&e,170,320);CHECK(e.state.taps==1 && e.state.last_tap==((170u<<16)|320u));
    cfs_tick(&e,1400);CHECK(e.state.mode==CFS_MODE_CAMMY && last_pixels==data+11 && e.object); /* CammyOS: asleep */
    cfs_tap(&e,10,10);cfs_tick(&e,1500);CHECK(e.state.mode==CFS_MODE_CAMMY && e.state.taps==2); /* later taps ignored */
    c=command(2,CFS_SHOW,1);put(c,1600);CHECK(e.state.error==CFS_OK && last_pixels==data+3); /* session wakes it */
    cfs_tick(&e,1600+CFS_WAKE_MS);CHECK(last_pixels==data+2);
    screen_id=0;cfs_tick(&e,1900);CHECK(e.object && !e.suspended); /* CammyOS keeps the screen */
    reset();cfs_enable_chooser(&e,menu);cfs_tick(&e,1100);cfs_tap(&e,170,40);cfs_tick(&e,1200);
    CHECK(e.state.mode==CFS_MODE_NORMAL && !e.object && removed==1 && e.state.phase==CFS_HIDDEN); /* Normal: stock */
    c=command(1,CFS_SHOW,1);put(c,1300);CHECK(e.state.error==CFS_NOT_CAMMY && !e.object && created==1);
    struct cfs_status st;CHECK(!cfs_read_status(&page,&st) && st.mode==CFS_MODE_NORMAL && st.taps==1);
    /* Chooser keys: CammyOS is focused at the start, so OK alone picks it; up+OK = Normal,
     * down+OK = CammyOS; keys outside the chooser are ignored. */
    reset();cfs_enable_chooser(&e,menu);cfs_tick(&e,1100);CHECK(last_pixels==menu+2u*CFS_PIXELS_BYTES);
    cfs_key(&e,CFS_KEY_OK);cfs_tick(&e,1150);CHECK(e.state.mode==CFS_MODE_CAMMY);
    reset();cfs_enable_chooser(&e,menu);cfs_tick(&e,1100);
    cfs_key(&e,CFS_KEY_DOWN);cfs_tick(&e,1200);CHECK(last_pixels==menu+2u*CFS_PIXELS_BYTES);
    cfs_key(&e,CFS_KEY_UP);cfs_tick(&e,1250);CHECK(last_pixels==menu+CFS_PIXELS_BYTES);
    cfs_key(&e,99);cfs_tick(&e,1260);CHECK(last_pixels==menu+CFS_PIXELS_BYTES && e.state.mode==CFS_MODE_CHOOSING);
    cfs_key(&e,CFS_KEY_OK);cfs_tick(&e,1300);CHECK(e.state.mode==CFS_MODE_NORMAL && !e.object);
    reset();cfs_enable_chooser(&e,menu);cfs_tick(&e,1100);cfs_key(&e,CFS_KEY_DOWN);cfs_key(&e,CFS_KEY_OK);cfs_tick(&e,1200);
    CHECK(e.state.mode==CFS_MODE_CAMMY && last_pixels==data+11);
    cfs_key(&e,CFS_KEY_UP);cfs_key(&e,CFS_KEY_OK);cfs_tick(&e,1300);CHECK(e.state.mode==CFS_MODE_CAMMY);
    /* Loading gate: picking CammyOS shows the opaque Loading frame (never the sleepy face),
     * then cammy-ui's published status while it has one, and opens only on cammy-ui's ready. */
    reset();cfs_enable_chooser(&e,menu);cfs_enable_loading(&e,loading,&loading_makes);cfs_tick(&e,1100);
    CHECK(atomic_load(page.header+CFS_HEADER_BOOT)==CFS_BOOT_NONE); /* choosing: no gate yet */
    cfs_key(&e,CFS_KEY_OK);cfs_tick(&e,1200);
    CHECK(e.state.mode==CFS_MODE_CAMMY && last_pixels==loading_buf && e.state.phase==CFS_VISIBLE);
    CHECK(atomic_load(page.header+CFS_HEADER_BOOT)==CFS_BOOT_LOADING);
    { CHECK(!cfs_read_status(&page,&st)&&st.mode==CFS_MODE_CAMMY); } /* health's GUI sample keeps running */
    { unsigned drawn0=drawn;cfs_tick(&e,1300);CHECK(drawn==drawn0); } /* same frame: no redraw */
    /* CCC is never refused: commands apply (lease, mood) but the picture waits. */
    c=command(1,CFS_SHOW,2);put(c,1400);CHECK(e.state.error==CFS_OK && e.state.owner==33 && last_pixels==loading_buf);
    c=command(2,CFS_HIDE,0);put(c,1500);CHECK(e.object && last_pixels==loading_buf); /* no hide uncovers stock */
    ui_live=1;cfs_tick(&e,1510);CHECK(e.object && last_pixels==loading_buf);ui_live=0;   /* nor the live view */
    ui_boot_word=0;ui_show=1;ui_seq=1;cfs_tick(&e,1600);CHECK(last_pixels==menubuf[1]); /* cammy-ui's status */
    ui_show=0;ui_boot_word=-1;cfs_tick(&e,1700);CHECK(last_pixels==loading_buf); /* cammy-ui restarting */
    ui_boot_word=0;cfs_tick(&e,1700+CFS_LOADING_ORPHAN_MS);CHECK(e.state.mode==CFS_MODE_CAMMY && last_pixels==loading_buf);
    c=command(3,CFS_SHOW,1);c.generation=2;c.deadline=1700+CFS_LOADING_ORPHAN_MS+4000;put(c,1700+CFS_LOADING_ORPHAN_MS+10);
    ui_boot_word=1;cfs_tick(&e,1700+CFS_LOADING_ORPHAN_MS+20); /* ready: the gate opens on the commanded face */
    CHECK(atomic_load(page.header+CFS_HEADER_BOOT)==CFS_BOOT_READY && e.state.owner==33 && last_pixels!=loading_buf && (const unsigned char *)last_pixels>=data && (const unsigned char *)last_pixels<data+12);
    ui_boot_word=0;ui_show=0;cfs_tick(&e,1700+CFS_LOADING_ORPHAN_MS+400);CHECK(last_pixels!=loading_buf); /* one-way */
    CHECK(atomic_load(page.header+CFS_HEADER_BOOT)==CFS_BOOT_READY && loading_makes>=1);
    c=command(4,CFS_HIDE,0);c.generation=2;c.deadline=1700+CFS_LOADING_ORPHAN_MS+4000;put(c,1700+CFS_LOADING_ORPHAN_MS+500);CHECK(!e.object); /* hides work again */
    /* Stale or missing cammy-ui for the whole orphan bound: the stock camera for this boot. */
    reset();cfs_enable_chooser(&e,menu);cfs_enable_loading(&e,loading,&loading_makes);cfs_tick(&e,1100);cfs_tap(&e,170,320);cfs_tick(&e,1200);
    CHECK(last_pixels==loading_buf);cfs_tick(&e,1200+CFS_LOADING_ORPHAN_MS-1);CHECK(e.state.mode==CFS_MODE_CAMMY && e.object);
    cfs_tick(&e,1200+CFS_LOADING_ORPHAN_MS);
    CHECK(e.state.mode==CFS_MODE_NORMAL && !e.object && e.state.phase==CFS_HIDDEN && atomic_load(page.header+CFS_HEADER_BOOT)==CFS_BOOT_STOCK);
    /* A failed startup's stock camera choice (cammy-ui camera mode) ends the gate too. */
    reset();cfs_enable_chooser(&e,menu);cfs_enable_loading(&e,loading,&loading_makes);cfs_tick(&e,1100);cfs_key(&e,CFS_KEY_OK);cfs_tick(&e,1200);
    ui_boot_word=0;ui_show=1;ui_seq=1;cfs_tick(&e,1300);ui_camera=1;cfs_tick(&e,1400);
    CHECK(e.state.mode==CFS_MODE_NORMAL && !e.object && atomic_load(page.header+CFS_HEADER_BOOT)==CFS_BOOT_STOCK);
    /* Normal is unchanged: no gate, no loading frame. */
    reset();cfs_enable_chooser(&e,menu);cfs_enable_loading(&e,loading,&loading_makes);cfs_tick(&e,1100);cfs_tap(&e,170,40);cfs_tick(&e,1200);
    CHECK(e.state.mode==CFS_MODE_NORMAL && !e.object && !loading_makes && atomic_load(page.header+CFS_HEADER_BOOT)==CFS_BOOT_NONE);
    /* A cammy-ui that predates the gate (its page has no boot word) reads as ready. */
    reset();cfs_enable_chooser(&e,menu);cfs_enable_loading(&e,loading,&loading_makes);cfs_tick(&e,1100);cfs_key(&e,CFS_KEY_OK);cfs_tick(&e,1200);
    ui_boot_word=1;cfs_tick(&e,1300);CHECK(last_pixels==data+11 && atomic_load(page.header+CFS_HEADER_BOOT)==CFS_BOOT_READY);
    /* Menu: CammyOS shows cammy-ui's published buffer, swaps on a new sequence, returns to
     * the face when closed, and camera mode hands the screen to stock. */
    reset();cfs_enable_chooser(&e,menu);cfs_tick(&e,1100);cfs_tap(&e,170,320);cfs_tick(&e,1200);CHECK(last_pixels==data+11);
    ui_show=1;ui_seq=1;cfs_tick(&e,1300);CHECK(last_pixels==menubuf[1]);
    unsigned before=drawn;cfs_tick(&e,1400);CHECK(drawn==before); /* same frame: no redraw */
    ui_seq=2;cfs_tick(&e,1500);CHECK(last_pixels==menubuf[0]);
    ui_show=0;cfs_tick(&e,1600);CHECK(last_pixels==data+11); /* closed: face again */
    { CHECK(!cfs_read_status(&page,&st)&&st.mode==CFS_MODE_CAMMY&&st.tracking==CFS_TRACK_UNKNOWN); }
    ui_track=1;ui_live=1;cfs_tick(&e,1610);CHECK(e.state.mode==CFS_MODE_CAMMY && !e.object); /* live view: face steps aside */
    { CHECK(!cfs_read_status(&page,&st)&&st.mode==CFS_MODE_CAMMY&&st.tracking==CFS_TRACK_ON); }
    ui_show=1;ui_seq=3;cfs_tick(&e,1620);CHECK(e.object && last_pixels==menubuf[1]); /* menu over local preview */
    ui_show=0;cfs_tick(&e,1630);CHECK(!e.object && e.state.mode==CFS_MODE_CAMMY); /* resumes preview */
    ui_track=0;ui_live=0;cfs_tick(&e,1640);CHECK(e.state.mode==CFS_MODE_CAMMY && e.object && last_pixels==data+11);
    { CHECK(!cfs_read_status(&page,&st)&&st.tracking==CFS_TRACK_OFF); }
    c=command(1,CFS_HIDE,0);put(c,1650);CHECK(!e.object && e.want_hidden);
    ui_show=1;ui_seq=4;cfs_tick(&e,1660);CHECK(e.object && e.want_hidden && last_pixels==menubuf[0]);
    ui_show=0;cfs_tick(&e,1670);CHECK(!e.object && e.want_hidden); /* CCC's preview stays active */
    ui_camera=1;cfs_tick(&e,1700);CHECK(e.state.mode==CFS_MODE_NORMAL && !e.object);
    reset();ui_show=1;ui_seq=1;cfs_tick(&e,1100);CHECK(last_pixels!=menubuf[1]); /* legacy mode ignores the menu */
    /* Sleep loop: no agent -> frames cycle every 437 ms; a session wakes; release sleeps again. */
    static unsigned char sleepbank[CFS_SLEEP_FRAMES*CFS_PIXELS_BYTES];const unsigned char *sl=sleepbank;
    reset();cfs_enable_sleep(&e,sl);cfs_tick(&e,1100);CHECK(last_pixels==sl);
    cfs_tick(&e,1100+437);CHECK(last_pixels==sl+CFS_PIXELS_BYTES);
    cfs_tick(&e,1100+437*7);CHECK(last_pixels==sl+(size_t)7*CFS_PIXELS_BYTES);
    cfs_tick(&e,1100+437*8);CHECK(last_pixels==sl);
    c=command(1,CFS_SHOW,1);c.deadline=8000;put(c,5000);CHECK(last_pixels==data+3);cfs_tick(&e,5000+CFS_WAKE_MS);CHECK(last_pixels==data+2);
    c=command(2,CFS_STOP,0);c.owner=0;c.generation=2;c.deadline=8000;put(c,5400);CHECK(last_pixels==sl);
#define OPEN(p) (last_pixels==(p) || last_pixels==(p)+1) /* open eyes, or mid-blink */
#define SL(i) (sl+(size_t)(i)*CFS_PIXELS_BYTES)
    /* Explicit show/replay sleepy: the owner keeps the lease and sees the sleep loop. */
    reset();cfs_enable_sleep(&e,sl);cfs_tick(&e,1100);CHECK(last_pixels==SL(0));
    c=command(1,CFS_SHOW,1);put(c,1200);CHECK(last_pixels==data+3);cfs_tick(&e,1500);CHECK(last_pixels==data+2);
    c=command(2,CFS_SHOW,CFS_SLEEPY);put(c,2000);CHECK(last_pixels==SL(0) && e.dozing && e.state.owner==33 && !e.state.idle && e.state.expression==CFS_SLEEPY);
    { CHECK(!cfs_read_status(&page,&st) && st.expression==CFS_SLEEPY && !st.idle && st.owner==33 && st.applied_request==2); }
    cfs_tick(&e,2000+437);CHECK(last_pixels==SL(1));
    c=command(3,CFS_RENEW,0);put(c,2500);CHECK(last_pixels==SL(1) && e.dozing); /* renew holds it */
    c=command(4,CFS_SHOW,CFS_SLEEPY);put(c,2900);CHECK(last_pixels==SL(2) && e.dozing); /* replay: no restart */
    for(uint64_t t=2900;t<3990;t+=10){cfs_tick(&e,t);const unsigned char *px=last_pixels;CHECK(px>=sl && px<SL(CFS_SLEEP_FRAMES));} /* never blinks or shows a mood frame */
    c=command(5,CFS_SHOW,CFS_SLEEPY);c.owner=44;put(c,3995);CHECK(e.state.error==CFS_BAD_OWNER && e.state.owner==33 && e.dozing);
    /* Lease expiry while dozing: owner gone, same loop, no phase jump. */
    cfs_tick(&e,4000);CHECK(!e.state.owner && e.asleep && !e.dozing && e.state.idle && e.state.expression==CFS_SLEEPY);
    cfs_tick(&e,2000+437*5);CHECK(last_pixels==SL(5));
    c=command(6,CFS_RENEW,0);c.deadline=9000;put(c,4300);CHECK(e.state.error==CFS_BAD_OWNER && !e.state.owner && e.asleep); /* no late renew */
    /* A new lease wakes it: show = eyes open after CFS_WAKE_MS; idle = settled neutral. */
    c=command(7,CFS_SHOW,0);c.generation=2;c.deadline=9000;put(c,4400);CHECK(last_pixels==data+1 && !e.asleep);
    cfs_tick(&e,4400+CFS_WAKE_MS);CHECK(last_pixels==data);
    c=command(8,CFS_SHOW,CFS_SLEEPY);c.generation=2;c.deadline=9000;put(c,4700);CHECK(last_pixels==SL(0));
    c=command(9,CFS_IDLE,0);c.generation=2;c.deadline=9000;put(c,5000);CHECK(last_pixels==data+1 && e.state.idle && !e.dozing);
    cfs_tick(&e,5000+CFS_WAKE_MS);CHECK(last_pixels==data);
    /* Speak wakes it straight into the unchanged talk loop. */
    e.talk=menu;c=command(10,CFS_SHOW,CFS_SLEEPY);c.generation=2;c.deadline=9000;put(c,5300);CHECK(last_pixels==SL(0));
    c=command(11,CFS_SPEAK,0);c.generation=2;c.deadline=9000;put(c,5400);CHECK(last_pixels==menu && e.speaking && !e.dozing);
    cfs_tick(&e,5580);CHECK(last_pixels==menu+CFS_PIXELS_BYTES);
    /* Stop while dozing: asleep with the same phase; hide then sleepy again resumes the loop. */
    c=command(12,CFS_SHOW,CFS_SLEEPY);c.generation=2;c.deadline=9000;put(c,5600);CHECK(last_pixels==SL(0));
    c=command(13,CFS_STOP,0);c.owner=0;c.generation=3;c.deadline=9000;put(c,5600+437*3);CHECK(e.asleep && !e.dozing && last_pixels==SL(3));
    c=command(14,CFS_SHOW,CFS_SLEEPY);c.generation=4;c.deadline=9900;put(c,5600+437*4);CHECK(e.dozing && e.state.owner==33 && last_pixels==SL(4)); /* asleep -> dozing: same loop */
    c=command(15,CFS_HIDE,CFS_SLEEPY);c.generation=4;c.deadline=9900;put(c,7500);CHECK(!e.object && !e.dozing);
    c=command(16,CFS_SHOW,CFS_SLEEPY);c.generation=4;c.deadline=9900;put(c,7600);CHECK(e.object && e.dozing && last_pixels==SL(0));
    /* Pack switch while dozing: the new pack's sleep loop, or its sleepy frame without one. */
    { static unsigned char sleep2[CFS_SLEEP_FRAMES*CFS_PIXELS_BYTES];static const unsigned char d3[24]={0};const unsigned char *g[12],*h[12];
      for(unsigned i=0;i<12;i++){g[i]=d3+i;h[i]=d3+12+i;}
      CHECK(cfs_set_pack(&e,g,h,sleep2,NULL)==0);cfs_tick(&e,7600+437);CHECK(last_pixels==sleep2+CFS_PIXELS_BYTES && e.dozing && e.state.owner==33);
      CHECK(cfs_set_pack(&e,g,NULL,NULL,NULL)==0);cfs_tick(&e,7600+437*2);CHECK(last_pixels==g[11]);
      before=drawn;cfs_tick(&e,9000);CHECK(drawn==before && last_pixels==g[11]); /* no sleep bank: still, eyes shut */
    }
    /* Fin and Fur: a second pack with a turned bank (the other one in front). Idle (neutral):
     * the front swaps every CFS_TURN_MS; a commanded mood keeps the current front; going idle
     * keeps whoever is in front; back to one look. */
    static const unsigned char data2[24]={0};const unsigned char *f1[12],*f2[12],*t2[12],*bad[12];
    for(unsigned i=0;i<12;i++){f1[i]=data+i;f2[i]=data2+i;t2[i]=bad[i]=data2+12+i;}bad[5]=NULL;
    reset();CHECK(cfs_set_pack(&e,f2,t2,NULL,NULL)==0);CHECK(e.turns==1);
    c=command(1,CFS_IDLE,0);c.deadline=5000;put(c,1100);CHECK(last_pixels==f2[1] && e.front==0);
    cfs_tick(&e,1100+CFS_WAKE_MS);CHECK(last_pixels==f2[0]);
    c=command(2,CFS_RENEW,0);c.deadline=9500;put(c,4900);CHECK(OPEN(f2[0]) && e.front==0);
    c=command(3,CFS_RENEW,0);c.deadline=13000;put(c,1100+CFS_TURN_MS);CHECK(e.front==1 && OPEN(t2[0])); /* 8 s: Fur in front, neutral */
    c=command(4,CFS_SHOW,3);c.deadline=13000;put(c,9200);CHECK(e.front==1 && OPEN(t2[6]));
    cfs_tick(&e,12000);CHECK(e.front==1 && OPEN(t2[6])); /* commanded: no turns */
    c=command(5,CFS_IDLE,0);c.deadline=16000;put(c,12010);CHECK(e.front==1 && OPEN(t2[0])); /* idle keeps Fur in front */
    c=command(60,CFS_RENEW,0);c.deadline=20800;put(c,15900);CHECK(e.front==1);
    c=command(61,CFS_RENEW,0);c.deadline=25000;put(c,12010+CFS_TURN_MS);CHECK(e.front==0 && OPEN(f2[0]));
    CHECK(cfs_set_pack(&e,f2,bad,NULL,NULL)==-1 && e.turns==1 && e.frames[0]==f2[0]); /* missing frame: unchanged */
    CHECK(cfs_set_pack(&e,f2,t2,NULL,NULL)==0);cfs_tick(&e,12010+CFS_TURN_MS+100);CHECK(e.front==0 && OPEN(f2[0])); /* switch: first one in front */
    c=command(70,CFS_RENEW,0);c.deadline=28900;put(c,24000);
    c=command(71,CFS_RENEW,0);c.deadline=29000;put(c,12010+2*CFS_TURN_MS);CHECK(e.front==1 && OPEN(t2[0])); /* turns resume */
    c=command(80,CFS_SHOW,3);c.deadline=29000;put(c,28100);
    before=drawn;CHECK(cfs_set_pack(&e,f1,NULL,NULL,NULL)==0);cfs_tick(&e,28200);CHECK(drawn==before+1 && OPEN(data+6) && !e.turns);
    reset();cfs_enable_chooser(&e,menu);
    cfs_observe(&e,1100,1,0,0);cfs_tick(&e,1100);CHECK(!e.object); /* first-draw hold */
    cfs_observe(&e,1100+CFS_FIRST_HOLD_MS,1,0,0);cfs_tick(&e,1100+CFS_FIRST_HOLD_MS);CHECK(e.object);
    cfs_observe(&e,1200+CFS_FIRST_HOLD_MS,1,0,1);CHECK(!e.object && e.faults==0);
    cfs_observe(&e,1900+CFS_FIRST_HOLD_MS,2,0,1);cfs_tick(&e,1900+CFS_FIRST_HOLD_MS);CHECK(!e.object);
    cfs_observe(&e,1901+CFS_FIRST_HOLD_MS,2,0,0);cfs_tick(&e,1901+CFS_FIRST_HOLD_MS);CHECK(!e.object);
    cfs_observe(&e,2251+CFS_FIRST_HOLD_MS,2,0,0);cfs_tick(&e,2251+CFS_FIRST_HOLD_MS);CHECK(e.object && e.faults==0);

    /* Boot chooser timing (0.9.6), from USB boot fbaa812c: wm+0x7c is the previous window,
     * the prompt loads 0.42 s after the first chance to draw and waits for a key. */
    reset();cfs_enable_chooser(&e,menu);cfs_usb(&e,1050,CFS_USB_DETACHED);
    cfs_observe(&e,1100,2,2,0);cfs_tick(&e,1100);CHECK(!e.object && e.usb_hold);
    cfs_key(&e,CFS_KEY_UP);cfs_key(&e,CFS_KEY_OK);cfs_tick(&e,1200);CHECK(!e.object && e.chooser_focus==2 && e.state.mode==CFS_MODE_CHOOSING);
    cfs_observe(&e,1520,6,2,0);cfs_tick(&e,1520);CHECK(!e.object && e.prompt_seen);
    cfs_observe(&e,1869,6,2,0);cfs_tick(&e,1869);CHECK(!e.object);
    cfs_observe(&e,1870,6,2,0);cfs_tick(&e,1870);CHECK(e.object && e.state.draws==1 && last_pixels==menu+2u*CFS_PIXELS_BYTES); /* over the prompt */
    /* One OK answers both: the chooser takes it while drawn over the prompt. */
    cfs_key(&e,CFS_KEY_OK);cfs_tick(&e,1900);CHECK(e.state.mode==CFS_MODE_CAMMY);
    {int prompt=0,draw=0,pick=0;for(unsigned i=0;i<e.marks;i++){prompt+=e.mark[i].what==CFS_MARK_PROMPT;draw+=e.mark[i].what==CFS_MARK_DRAW;pick+=e.mark[i].what==CFS_MARK_PICK;}CHECK(prompt==1 && draw==1 && pick==1);}
    /* The prompt closing (current 2, previous 6) is an ordinary transition, not a prompt. */
    reset();cfs_enable_chooser(&e,menu);
    cfs_observe(&e,1100,2,2,0);cfs_tick(&e,1100);cfs_observe(&e,1300,6,2,0);cfs_tick(&e,1300);
    cfs_observe(&e,1650,6,2,0);cfs_tick(&e,1650);CHECK(e.object);
    cfs_observe(&e,7000,2,6,0);cfs_tick(&e,7000);CHECK(!e.object);
    cfs_observe(&e,7350,2,6,0);cfs_tick(&e,7350);CHECK(e.object && e.state.draws==2);
    /* A boot that starts on window 6 is not a prompt: the hold runs its full length. */
    reset();cfs_enable_chooser(&e,menu);
    cfs_observe(&e,1100,6,2,0);cfs_tick(&e,1100);CHECK(!e.object && !e.prompt_seen);
    cfs_observe(&e,1100+CFS_FIRST_HOLD_MS-1,6,2,0);cfs_tick(&e,1100+CFS_FIRST_HOLD_MS-1);CHECK(!e.object);
    cfs_observe(&e,1100+CFS_FIRST_HOLD_MS,6,2,0);cfs_tick(&e,1100+CFS_FIRST_HOLD_MS);CHECK(e.object);
    /* No prompt (battery or charger): the hold ends on time, then the chooser stays. */
    reset();cfs_enable_chooser(&e,menu);
    cfs_observe(&e,1100,2,2,0);cfs_tick(&e,1100);
    cfs_observe(&e,1100+CFS_FIRST_HOLD_MS,2,2,0);cfs_tick(&e,1100+CFS_FIRST_HOLD_MS);CHECK(e.object && !e.usb_hold);
    cfs_observe(&e,9000,2,2,0);cfs_tick(&e,9000);CHECK(e.object && e.state.draws==1);
    /* The hold and prompt rules end with the choice. */
    cfs_key(&e,CFS_KEY_OK);cfs_tick(&e,9100);CHECK(e.state.mode==CFS_MODE_CAMMY);
    {unsigned n=e.marks;cfs_observe(&e,9200,6,0,0);cfs_tick(&e,9200);CHECK(e.marks==n);}
    /* 0.9.9, from 0.9.8 USB boot 65164e08: the stock work mode stayed non-zero from before the
     * prompt until 116 s. Over the prompt only a ChangeWindow in flight hides the chooser. */
    reset();cfs_enable_chooser(&e,menu);
    cfs_observe(&e,1100,2,2,CFS_BUSY_MODE);cfs_tick(&e,1100);CHECK(!e.object);
    cfs_observe(&e,1500,6,2,CFS_BUSY_MODE);cfs_tick(&e,1500);CHECK(!e.object && e.prompt_seen);
    cfs_observe(&e,1849,6,2,CFS_BUSY_MODE);cfs_tick(&e,1849);CHECK(!e.object);            /* 350 ms guard */
    cfs_observe(&e,1850,6,2,CFS_BUSY_MODE);cfs_tick(&e,1850);CHECK(e.object && e.state.draws==1);
    cfs_observe(&e,1900,6,2,CFS_BUSY_WM|CFS_BUSY_MODE);CHECK(!e.object);                   /* ChangeWindow */
    cfs_observe(&e,1950,6,2,CFS_BUSY_MODE);cfs_tick(&e,1950);CHECK(!e.object);
    cfs_observe(&e,2300,6,2,CFS_BUSY_MODE);cfs_tick(&e,2300);CHECK(e.object && e.state.draws==2);
    {int bits=0;for(unsigned i=0;i<e.marks;i++)if(e.mark[i].what==CFS_MARK_STOCK)bits|=(int)e.mark[i].value;CHECK(bits==(CFS_BUSY_WM|CFS_BUSY_MODE));}
    cfs_key(&e,CFS_KEY_OK);cfs_tick(&e,2400);CHECK(e.state.mode==CFS_MODE_CAMMY);
    /* Away from the prompt, a non-zero work mode still hides the chooser. */
    reset();cfs_enable_chooser(&e,menu);
    cfs_observe(&e,1100,2,2,0);cfs_tick(&e,1100);
    cfs_observe(&e,1100+CFS_FIRST_HOLD_MS,2,2,0);cfs_tick(&e,1100+CFS_FIRST_HOLD_MS);CHECK(e.object);
    cfs_observe(&e,5000,2,6,CFS_BUSY_MODE);cfs_tick(&e,5000);CHECK(!e.object);
    cfs_observe(&e,9000,2,6,CFS_BUSY_MODE);cfs_tick(&e,9000);CHECK(!e.object);
    /* A boot that starts on window 6 gets no exemption: it is not the prompt arriving. */
    reset();cfs_enable_chooser(&e,menu);
    cfs_observe(&e,1100,6,2,CFS_BUSY_MODE);cfs_tick(&e,1100+CFS_FIRST_HOLD_MS);CHECK(!e.object);

    /* Recovery budget (audit 2026-11-17): the face's own removals (live view, menu over
     * the preview, owner hide) never count; an unexpected LVGL deletion after hundreds of
     * them still recovers; persistent deletions stop after CFS_MAX_RECREATE recoveries,
     * and neither a later show nor a preview toggle revives the face past that bound. */
    reset();cfs_enable_chooser(&e,menu);cfs_tick(&e,1100);cfs_tap(&e,170,320);cfs_tick(&e,1200);
    CHECK(e.state.mode==CFS_MODE_CAMMY && e.object && created==1 && e.faults==0);
    { uint64_t t=1200;
      for(unsigned i=0;i<100;i++){ui_live=1;cfs_tick(&e,t+=10);CHECK(!e.object);ui_live=0;cfs_tick(&e,t+=10);CHECK(e.object);}
      for(unsigned i=0;i<20;i++){
        ui_live=1;cfs_tick(&e,t+=10);CHECK(!e.object);
        ui_show=1;ui_seq++;cfs_tick(&e,t+=10);CHECK(e.object && last_pixels==menubuf[ui_seq&1u]);
        ui_show=0;cfs_tick(&e,t+=10);CHECK(!e.object);ui_live=0;cfs_tick(&e,t+=10);CHECK(e.object);
      }
      c=command(1,CFS_HIDE,0);c.deadline=t+3000;put(c,t+=10);CHECK(!e.object);
      c=command(2,CFS_SHOW,1);c.deadline=t+3000;put(c,t+=10);CHECK(e.object && !e.suspended);
      CHECK(created==1+100+40+1 && created>CFS_MAX_RECREATE && e.faults==0);
      unsigned made=created;
      cfs_deleted(&e,e.object);CHECK(!e.object && e.suspended && e.faults==1);
      cfs_tick(&e,t+=10);CHECK(e.object && !e.suspended && created==made+1); /* recovered */
      for(unsigned i=1;i<CFS_MAX_RECREATE;i++){cfs_deleted(&e,e.object);cfs_tick(&e,t+=10);CHECK(e.object);}
      CHECK(e.faults==CFS_MAX_RECREATE && created==made+CFS_MAX_RECREATE);
      cfs_deleted(&e,e.object);for(unsigned i=0;i<50;i++)cfs_tick(&e,t+=10);
      CHECK(!e.object && e.suspended && e.state.phase==CFS_SUSPENDED && created==made+CFS_MAX_RECREATE);
      c=command(3,CFS_SHOW,2);c.deadline=t+3000;put(c,t+=10);CHECK(e.state.error==CFS_OK && !e.object && created==made+CFS_MAX_RECREATE);
      ui_live=1;cfs_tick(&e,t+=10);ui_live=0;cfs_tick(&e,t+=10);ui_show=1;ui_seq++;cfs_tick(&e,t+=10);ui_show=0;
      CHECK(!e.object && created==made+CFS_MAX_RECREATE && e.faults==CFS_MAX_RECREATE+1);
    }
    /* A persistently failing create is a fault too: bounded retries, not one per tick forever. */
    reset();cfs_enable_chooser(&e,menu);cfs_tick(&e,1100);cfs_tap(&e,170,320);cfs_tick(&e,1200);CHECK(e.object && created==1);
    create_fails=1;cfs_deleted(&e,e.object);
    for(uint64_t t=1210;t<1210+10*200;t+=10)cfs_tick(&e,t);
    CHECK(failed_creates==CFS_MAX_RECREATE && !e.object && e.suspended && e.state.phase==CFS_SUSPENDED);
    /* Legacy mode: a create failure leaves the face suspended until the next command. */
    reset();create_fails=1;cfs_tick(&e,1100);CHECK(failed_creates==1 && e.state.phase==CFS_FAILED && e.faults==1);
    cfs_tick(&e,1200);CHECK(failed_creates==1);create_fails=0;
    c=command(1,CFS_SHOW,1);put(c,1300);CHECK(e.object && created==1);
    /* CCC pack grant (0.7.11): GRANT starts it, HOLD keeps it, NONE ends it; renewals
     * never revive a grant something else ended. */
    reset();c=owned(1,CFS_SHOW,33,1,CFS_PACK_GRANT);put(c,1100);CHECK(e.adult && e.adult_end==CFS_END_NONE);
    c=owned(2,CFS_RENEW,33,1,CFS_PACK_HOLD);put(c,1200);CHECK(e.adult);
    cfs_adult_end(&e,CFS_END_PHYSICAL);CHECK(!e.adult && e.adult_end==CFS_END_PHYSICAL);
    c=owned(3,CFS_RENEW,33,1,CFS_PACK_HOLD);put(c,1300);CHECK(!e.adult && e.state.error==CFS_OK);
    c=owned(4,CFS_SHOW,33,1,CFS_PACK_HOLD);put(c,1400);CHECK(!e.adult);
    c=owned(5,CFS_SHOW,33,1,CFS_PACK_GRANT);put(c,1500);CHECK(e.adult);
    c=owned(6,CFS_SHOW,33,1,CFS_PACK_NONE);put(c,1600);CHECK(!e.adult && e.adult_end==CFS_END_CCC);
    c=owned(7,CFS_RENEW,33,1,CFS_PACK_GRANT);put(c,1700);CHECK(e.adult); /* a grant may ride a renewal */
    /* a rejected command changes nothing */
    c=owned(8,CFS_SHOW,33,1,CFS_PACK_NONE);c.instance=999;put(c,1800);CHECK(e.adult && e.state.error==CFS_BAD_BINDING);
    c=owned(9,CFS_SHOW,44,1,CFS_PACK_NONE);put(c,1850);CHECK(e.adult && e.state.error==CFS_BAD_OWNER);
    /* an out-of-range pack word makes the whole command unreadable */
    c=owned(10,CFS_SHOW,33,1,4);put(c,1900);CHECK(e.adult && e.state.request==9);
    /* lease expiry */
    cfs_tick(&e,4000);CHECK(!e.adult && e.adult_end==CFS_END_LEASE && !e.state.owner);
    c=owned(11,CFS_SHOW,33,2,CFS_PACK_HOLD);c.deadline=9000;put(c,4100);CHECK(!e.adult);
    c=owned(12,CFS_SHOW,33,2,CFS_PACK_GRANT);c.deadline=9000;put(c,4200);CHECK(e.adult);
    /* STOP */
    c=owned(13,CFS_STOP,0,3,CFS_PACK_NONE);c.deadline=9000;put(c,4300);CHECK(!e.adult && e.adult_end==CFS_END_LEASE);
    /* a new owner never inherits a grant, even with HOLD */
    c=owned(14,CFS_SHOW,33,4,CFS_PACK_GRANT);c.deadline=9000;put(c,4400);CHECK(e.adult);
    c=owned(15,CFS_SHOW,44,5,CFS_PACK_HOLD);c.deadline=9000;put(c,4500);CHECK(!e.adult && e.adult_end==CFS_END_LEASE && e.state.owner==44);
    c=owned(16,CFS_SHOW,44,5,CFS_PACK_GRANT);c.deadline=9000;put(c,4600);CHECK(e.adult);
    /* stock camera mode */
    e.state.mode=CFS_MODE_CAMMY;ui_camera=1;cfs_tick(&e,4700);CHECK(!e.adult && e.adult_end==CFS_END_CAMERA && e.state.mode==CFS_MODE_NORMAL);
    /* Packed pack: the same frame ids go through decode; sleep/talk need no raw banks. */
    reset();CHECK(cfs_set_packed(&e,NULL,NULL)==-1);CHECK(cfs_set_packed(&e,decode,packed_px)==0);
    cfs_tick(&e,1100);CHECK(decodes==1 && decoded_id==CFS_SLEEP_ID(0) && last_pixels==packed_px+0 && drawn==1);
    cfs_tick(&e,1100+CFS_SLEEP_PERIOD_MS/CFS_SLEEP_FRAMES);CHECK(decoded_id==CFS_SLEEP_ID(1) && drawn==2);
    c=command(1,CFS_SHOW,3);put(c,2000);cfs_tick(&e,2000+CFS_WAKE_MS);CHECK(decoded_id==6 && last_pixels==packed_px+2);
    c=command(2,CFS_SPEAK,0);put(c,2400);CHECK(decoded_id==0x08000000u);cfs_tick(&e,2400+180);CHECK(decoded_id==0x08000001u);
    unsigned kept=drawn,calls=decodes;decode_fail=1;cfs_tick(&e,2400+360);CHECK(drawn==kept && decodes==calls+1); /* kept picture */
    cfs_tick(&e,2400+361);CHECK(decodes==calls+1); /* no retry for the same frame */
    decode_fail=0;
    const unsigned char *raw[12];for(unsigned i=0;i<12;i++)raw[i]=data+i;
    CHECK(cfs_set_pack(&e,raw,NULL,NULL,NULL)==0 && !e.packed);c=command(3,CFS_SHOW,1);put(c,3000);cfs_tick(&e,3000+CFS_WAKE_MS);
    CHECK(last_pixels==data+2 || last_pixels==data+3);
    /* Two writers and a reader must never yield a torn accepted payload. */
    pthread_t a,b;CHECK(!pthread_create(&a,NULL,writer,(void *)(uintptr_t)100000));CHECK(!pthread_create(&b,NULL,writer,(void *)(uintptr_t)200000));
    for(unsigned i=0;i<30000;i++)if(!cfs_read_command(&page,&out) && out.request>100000){
        CHECK(out.owner==(out.request^0x55u));CHECK(out.generation==out.request+1);CHECK(out.deadline==out.request+5000);CHECK(out.expression==out.request%6);
    }
    CHECK(!pthread_join(a,NULL));CHECK(!pthread_join(b,NULL));
    printf("{\"synthetic\":true,\"checks\":%u,\"hardware_verified\":false}\n",checks);return 0;
}
