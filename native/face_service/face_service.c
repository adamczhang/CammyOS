/* SPDX-License-Identifier: MIT. Pure bounded GUI lifecycle and shared IPC. */
#include "face_service.h"
#include <string.h>
#include <limits.h>
static uint64_t pair(uint32_t lo,uint32_t hi) { return (uint64_t)lo|((uint64_t)hi<<32); }
/* Bounded32-step unsigned division keeps libgcc's signal-raising divide helper
 * out of the in-process library, including the ARM -Os build. */
#ifndef CFS_CLIENT_ONLY
static uint32_t quotient(uint32_t n,uint32_t d,uint32_t *remainder) {
    uint32_t q=0,r=0;
    for(unsigned i=32;i>0;i--){r=(r<<1)|((n>>(i-1))&1u);if(r>=d){r-=d;q|=1u<<(i-1);}}
    if(remainder)*remainder=r;
    return q;
}
#endif
static void put64(uint32_t *w,unsigned n,uint64_t v) { w[n]=(uint32_t)v;w[n+1]=(uint32_t)(v>>32); }
static int read_words(const _Atomic uint32_t *a,uint32_t w[24]) {
    for(unsigned attempt=0;attempt<3;attempt++) {
        uint32_t seq=atomic_load_explicit(a,memory_order_acquire);
        if(seq&1u)continue;
        for(unsigned i=1;i<24;i++)w[i]=atomic_load_explicit(a+i,memory_order_acquire);
        if(seq==atomic_load_explicit(a,memory_order_acquire))return 0;
    }
    return -1;
}
static int write_words(_Atomic uint32_t *a,const uint32_t w[24]) {
    uint32_t seq=atomic_load_explicit(a,memory_order_acquire);
    if(seq&1u || !atomic_compare_exchange_strong_explicit(a,&seq,seq+1u,memory_order_acq_rel,memory_order_acquire))return -1;
    for(unsigned i=1;i<24;i++)atomic_store_explicit(a+i,w[i],memory_order_release);
    atomic_store_explicit(a,seq+2u,memory_order_release);return 0;
}
void cfs_page_init(struct cfs_page *p,uint64_t instance,const uint32_t boot[4],uint32_t pid) {
    for(unsigned i=0;i<16;i++)atomic_init(p->header+i,0);
    for(unsigned i=0;i<24;i++){atomic_init(p->command+i,0);atomic_init(p->status+i,0);}
    atomic_store(p->header+1,CFS_ABI);atomic_store(p->header+2,CFS_PAGE_BYTES);atomic_store(p->header+3,pid);
    for(unsigned i=0;i<4;i++)atomic_store(p->header+4+i,boot[i]);
    atomic_store(p->header+8,(uint32_t)instance);atomic_store(p->header+9,(uint32_t)(instance>>32));
    atomic_store(p->header+10,1);atomic_store_explicit(p->header,CFS_MAGIC,memory_order_release);
}
int cfs_publish(struct cfs_page *p,const struct cfs_command *c) {
    if(!p || !c || atomic_load_explicit(p->header,memory_order_acquire)!=CFS_MAGIC ||
       atomic_load(p->header+1)!=CFS_ABI || atomic_load(p->header+2)!=CFS_PAGE_BYTES || !atomic_load(p->header+10))return -1;
    uint32_t w[24]={0};w[1]=c->op;put64(w,2,c->request);put64(w,4,c->owner);put64(w,6,c->generation);
    put64(w,8,c->deadline);put64(w,10,c->instance);for(unsigned i=0;i<4;i++)w[12+i]=c->boot[i];w[16]=c->expression;w[17]=c->pack;
    return write_words(p->command,w);
}
int cfs_read_command(const struct cfs_page *p,struct cfs_command *c) {
    uint32_t w[24]={0};if(read_words(p->command,w))return -1;
    if(w[17]>CFS_PACK_CHILD)return -1;
    for(unsigned i=18;i<24;i++)if(w[i])return -1;
    c->op=w[1];c->request=pair(w[2],w[3]);c->owner=pair(w[4],w[5]);c->generation=pair(w[6],w[7]);
    c->deadline=pair(w[8],w[9]);c->instance=pair(w[10],w[11]);for(unsigned i=0;i<4;i++)c->boot[i]=w[12+i];c->expression=w[16];c->pack=w[17];return 0;
}
int cfs_read_status(const struct cfs_page *p,struct cfs_status *s) {
    uint32_t w[24]={0};if(read_words(p->status,w))return -1;
    s->phase=w[1];s->expression=w[2];s->idle=w[3];s->request=pair(w[4],w[5]);s->owner=pair(w[6],w[7]);
    s->generation=pair(w[8],w[9]);s->deadline=pair(w[10],w[11]);s->gui_ms=pair(w[12],w[13]);
    s->draws=w[14];s->created=w[15];s->removed=w[16];s->error=w[17];s->suspended=w[18];s->applied_request=pair(w[20],w[21]);s->mode=w[19]&0xffffu;s->tracking=w[19]>>16;s->taps=w[22];s->last_tap=w[23];return 0;
}
#ifndef CFS_CLIENT_ONLY
static void status(struct cfs_engine *e,uint64_t now) {
    struct cfs_status *s=&e->state;uint32_t w[24]={0};s->gui_ms=now;s->suspended=e->suspended;
    if(s->error==CFS_OK && (s->phase==CFS_VISIBLE || s->phase==CFS_HIDDEN))s->applied_request=e->pending_request;
    if(s->phase!=CFS_VISIBLE || !e->object)e->visible_since=0;
    else if(!e->visible_since)e->visible_since=now;
    else if(now-e->visible_since>=10000u)e->faults=0;
    w[1]=s->phase;w[2]=s->expression;w[3]=s->idle;put64(w,4,s->request);put64(w,6,s->owner);
    put64(w,8,s->generation);put64(w,10,s->deadline);put64(w,12,now);
    w[14]=s->draws;w[15]=s->created;w[16]=s->removed;w[17]=s->error;w[18]=s->suspended;put64(w,20,s->applied_request);
    if(e->api.tracking){int t=e->api.tracking(e->api.context);s->tracking=t<0?CFS_TRACK_UNKNOWN:t?CFS_TRACK_ON:CFS_TRACK_OFF;}
    w[19]=(s->mode&0xffffu)|(s->tracking&0xffffu)<<16;w[22]=s->taps;w[23]=s->last_tap;
    (void)write_words(e->page->status,w);
}
int cfs_init(struct cfs_engine *e,struct cfs_page *p,const struct cfs_api *a,
             const unsigned char *const frames[CFS_FRAMES],uint64_t now,uint64_t delay) {
    if(!e||!p||!a||!a->screen||!a->top||!a->create||!a->pixels||!a->remove||delay>30000 || now>UINT64_MAX-delay)return -1;
    if(atomic_load(p->header)!=CFS_MAGIC || atomic_load(p->header+1)!=CFS_ABI || atomic_load(p->header+2)!=CFS_PAGE_BYTES)return -1;
    if(frames)for(unsigned i=0;i<CFS_FRAMES;i++)if(!frames[i])return -1;
    memset(e,0,sizeof(*e));e->page=p;e->api=*a;e->last_now=now;e->start_at=now+delay;e->idle_at=e->start_at;e->current_frame=UINT_MAX;
    e->instance=pair(atomic_load(p->header+8),atomic_load(p->header+9));if(!e->instance)return -1;
    for(unsigned i=0;i<4;i++)e->boot[i]=atomic_load(p->header+4+i);
    if(frames)for(unsigned i=0;i<CFS_FRAMES;i++)e->frames[i]=frames[i];
    e->sleep_at=e->start_at;e->blink_seed=(uint32_t)e->instance^(uint32_t)(e->instance>>32)^0x9e3779b9u;if(!e->blink_seed)e->blink_seed=1;
    e->state.phase=CFS_WAITING;e->state.idle=1;e->asleep=1;status(e,now);return 0;
}
static void remove_object(struct cfs_engine *e) {
    void *obj=e->object;e->object=NULL;e->current_frame=UINT_MAX;
    if(obj){e->api.remove(e->api.context,obj);e->state.removed++;}
}
/* The loading gate's state, mirrored to header[14] for cammy-ui and the portal. */
static void boot_state(struct cfs_engine *e,uint32_t v) {
    e->loading=v;atomic_store_explicit(e->page->header+CFS_HEADER_BOOT,v,memory_order_release);
}
static void fault(struct cfs_engine *e) {
    e->visible_since=0;
    if(e->faults<=CFS_MAX_RECREATE)e->faults++;
}
static unsigned may_recover(const struct cfs_engine *e) {return e->faults<=CFS_MAX_RECREATE;}
/* Only an object still owned here counts: remove_object clears e->object before LVGL's
 * delete event, so the face's own removals never reach the fault count. */
void cfs_deleted(struct cfs_engine *e,void *obj) {
    if(obj && e->object==obj){e->object=NULL;e->current_frame=UINT_MAX;e->state.removed++;e->suspended=1;e->state.phase=CFS_SUSPENDED;fault(e);}
}
/* Sleep shows the same loop whether no owner holds the face or an owner asked for
 * sleepy; moving between the two keeps the loop's phase. */
static unsigned sleeping(const struct cfs_engine *e) {return e->asleep || e->dozing;}
void cfs_adult_end(struct cfs_engine *e,uint32_t reason) {
    e->child_override=0;
    if(e->adult || (e->adult_end==CFS_END_LEASE && reason!=CFS_END_LEASE)){
        e->adult=0;e->adult_end=reason;
    }
}
static void idle(struct cfs_engine *e,uint64_t now) {
    cfs_adult_end(e,CFS_END_LEASE);
    if(!sleeping(e))e->sleep_at=now;
    e->speaking=0;e->dozing=0;
    e->state.owner=0;e->state.deadline=0;e->state.expression=0;e->state.idle=1;e->idle_at=now;e->want_hidden=0;e->asleep=1;
}
static void command(struct cfs_engine *e,const struct cfs_command *c,uint64_t now) {
    struct cfs_status *s=&e->state;
    if(!c->request || c->request<=e->seen)return;
    if(c->instance!=e->instance || memcmp(c->boot,e->boot,sizeof(e->boot))){s->error=CFS_BAD_BINDING;return;}
    e->seen=c->request;s->request=c->request;
    if(!c->generation || c->generation<s->generation){s->error=CFS_STALE;return;}
    if(c->deadline<=now || c->deadline-now>CFS_MAX_LEASE_MS){s->error=CFS_EXPIRED;return;}
    if(c->op<CFS_SHOW || c->op>CFS_SPEAK || c->expression>=CFS_MOODS || (c->op!=CFS_STOP && !c->owner)) {s->error=CFS_INVALID;return;}
    if(c->op!=CFS_STOP && (e->state.mode==CFS_MODE_CHOOSING || e->state.mode==CFS_MODE_NORMAL)){s->error=CFS_NOT_CAMMY;return;}
    if(c->op==CFS_STOP) {
        if(c->owner || c->generation<=s->generation){s->error=CFS_BAD_OWNER;return;}
        s->generation=c->generation;idle(e,now);s->error=CFS_OK;e->pending_request=c->request;return;
    }
    if(c->generation==s->generation && c->owner!=s->owner){s->error=CFS_BAD_OWNER;return;}
    if(c->op==CFS_RENEW && (c->generation!=s->generation || c->owner!=s->owner)){s->error=CFS_BAD_OWNER;return;}
    if(c->owner!=s->owner)cfs_adult_end(e,CFS_END_LEASE); /* a new owner never inherits a grant */
    if(c->pack==CFS_PACK_GRANT){e->adult=1;e->adult_end=CFS_END_NONE;e->child_override=0;}
    else if(c->pack==CFS_PACK_NONE || c->pack==CFS_PACK_CHILD){
        cfs_adult_end(e,CFS_END_CCC);
        e->child_override=c->pack==CFS_PACK_CHILD;
    }
    s->generation=c->generation;s->owner=c->owner;s->deadline=c->deadline;s->error=CFS_OK;e->pending_request=c->request;
    if(c->op==CFS_RENEW)return;
    unsigned was=sleeping(e);
    e->speaking=c->op==CFS_SPEAK;
    e->dozing=c->op==CFS_SHOW && c->expression==CFS_SLEEPY;
    if(!was && e->dozing)e->sleep_at=now;
    if(was && !e->dozing){e->blink_at=now;e->blink_end=now+CFS_WAKE_MS;e->blink_double=0;} /* wake: eyes open shortly */
    if(c->op==CFS_IDLE)e->front_base=e->front; /* idle turns start from whoever is in front */
    e->asleep=0;s->expression=c->expression;s->idle=c->op==CFS_IDLE;e->idle_at=now;e->want_hidden=c->op==CFS_HIDE;if(may_recover(e))e->suspended=0;e->start_at=now;
}
/* xorshift32: deterministic per instance, no division. */
static uint32_t blink_rand(struct cfs_engine *e) {
    uint32_t x=e->blink_seed;x^=x<<13;x^=x>>17;x^=x<<5;e->blink_seed=x;return x;
}
/* Awake eyes: 1 while a blink holds them closed. Gaps of 2.4-6.5 s; about one blink in
 * eight is followed by a quick second one. A long stall restarts the schedule. */
static unsigned eyes_closed(struct cfs_engine *e,uint64_t now) {
    if(now>e->blink_end && now-e->blink_end>CFS_BLINK_GAP_MS+4096u)e->blink_end=e->blink_at=now;
    while(now>=e->blink_end) {
        uint32_t r=blink_rand(e);uint64_t gap;
        if(e->blink_double){e->blink_double=0;gap=CFS_BLINK_AGAIN_MS+(r&63u);}
        else{gap=CFS_BLINK_GAP_MS+(r&4095u);e->blink_double=((r>>13)&7u)==0;}
        e->blink_at=e->blink_end+gap;e->blink_end=e->blink_at+CFS_BLINK_MS+((r>>20)&63u);
    }
    return now>=e->blink_at;
}
/* Sample on both sides of the stock timer. The delete observer owns lifetime if
 * stock has already deleted a child. Do not walk an old screen or parent. */
static void mark(struct cfs_engine *e,uint64_t now,uint32_t what,uint32_t value){
    if(e->state.mode!=CFS_MODE_CHOOSING || e->marks>=CFS_MARKS)return;
    struct cfs_mark *m=&e->mark[e->marks++];
    m->ms=now;m->what=what;m->window=e->window;m->overlay=e->overlay;m->value=value;
}
void cfs_usb(struct cfs_engine *e,uint64_t now,uint32_t state){
    if(e->detached || state==e->usb)return;
    e->usb=state;mark(e,now,CFS_MARK_USB,state);
}
static int usb_prompt(const struct cfs_engine *e){
    return e->window==CFS_STOCK_USB_WINDOW && e->first_window!=CFS_STOCK_USB_WINDOW;
}
/* While choosing, the first draw waits for the stock USB prompt's arrival window. 1 keeps
 * the chooser hidden. After the prompt arrives, only the existing transition guard applies. */
static int chooser_held(struct cfs_engine *e,uint64_t now){
    if(!e->eligible_at){
        e->eligible_at=now;mark(e,now,CFS_MARK_ELIGIBLE,e->usb);
        if(!e->state.draws && !e->prompt_seen){e->usb_hold=1;e->hold_until=now+CFS_FIRST_HOLD_MS;mark(e,now,CFS_MARK_HOLD,e->usb);}
    }
    if(e->usb_hold && !e->prompt_seen && now<e->hold_until)return 1;
    e->usb_hold=0;return 0;
}
void cfs_observe(struct cfs_engine *e,uint64_t now,uint32_t window,uint32_t overlay,int busy) {
    if(e->detached)return;
    void *screen=e->api.screen(e->api.context);
    void *parent=screen?e->api.top(e->api.context):NULL;
    if(!e->observed){e->first_window=window;e->first_overlay=overlay;}
    uint32_t bits=(uint32_t)busy;
    if(e->state.mode==CFS_MODE_CHOOSING && window==CFS_STOCK_USB_WINDOW && e->first_window!=CFS_STOCK_USB_WINDOW)
        busy=(int)(bits&~(uint32_t)CFS_BUSY_MODE);   /* see CFS_BUSY_MODE */
    if(busy || !screen || !parent || (e->observed &&
       (screen!=e->screen || parent!=e->parent || window!=e->window || overlay!=e->overlay || e->stock_busy))){
        remove_object(e);
        e->visible_since=0;
        e->tap_pending=0;
        e->resume_at=now+CFS_TRANSITION_MS;
    }
    int moved=!e->observed || window!=e->window || overlay!=e->overlay || bits!=e->busy_bits;
    e->stock_busy=busy!=0;e->busy_bits=bits;e->observed=1;e->screen=screen;e->parent=parent;e->window=window;e->overlay=overlay;
    if(moved)mark(e,now,CFS_MARK_STOCK,bits);
    if(usb_prompt(e) && !e->prompt_seen){e->prompt_seen=1;mark(e,now,CFS_MARK_PROMPT,0);}
    e->transition=busy || !screen || !parent || now<e->resume_at;
}
void cfs_tick(struct cfs_engine *e,uint64_t now) {
    if(e->detached)return;
    atomic_store_explicit(e->page->header+11,1,memory_order_release);
    if(now<e->last_now){cfs_detach(e);return;}e->last_now=now;
    if(e->state.owner && now>=e->state.deadline)idle(e,now);
    struct cfs_command c;if(!cfs_read_command(e->page,&c))command(e,&c,now);
    unsigned sticky=e->state.mode==CFS_MODE_CHOOSING || e->state.mode==CFS_MODE_CAMMY;
    if(e->state.mode==CFS_MODE_NORMAL){remove_object(e);e->state.phase=CFS_HIDDEN;status(e,now);return;}
    if(e->observed && e->transition){
        e->state.phase=CFS_SUSPENDED;status(e,now);return;
    }
    if(e->state.mode==CFS_MODE_CHOOSING && e->observed && now>=e->start_at && chooser_held(e,now)){
        remove_object(e);e->tap_pending=0;e->state.phase=CFS_SUSPENDED;status(e,now);return;
    }
    if(!e->observed && e->object && e->api.screen(e->api.context)!=e->screen){
        if(sticky)e->screen=e->api.screen(e->api.context);else{remove_object(e);e->suspended=1;}
    }
    if(sticky && e->suspended && !e->object && may_recover(e))e->suspended=0;
    /* Read and acknowledge menu frames even while the face is hidden. A local menu
     * can cover the preview without dropping another controller's hide request. */
    const unsigned char *menu=NULL;uint32_t seq=0,camera=0;
    if(e->state.mode==CFS_MODE_CAMMY && e->api.ui) {
        if(!e->api.ui(e->api.context,now,&menu,&seq,&camera))menu=NULL;
        if(camera){if(e->loading==CFS_BOOT_LOADING)boot_state(e,CFS_BOOT_STOCK);
            cfs_adult_end(e,CFS_END_CAMERA);e->state.mode=CFS_MODE_NORMAL;remove_object(e);e->state.phase=CFS_HIDDEN;status(e,now);return;}
    }
    /* Loading gate: only cammy-ui's readback of this boot's readiness opens it. A fresh
     * cammy-ui page (loading or failed) keeps it; none for the orphan bound gives the
     * screen to the stock camera, exactly as the menu's stock camera choice does. */
    if(e->state.mode==CFS_MODE_CAMMY && e->loading==CFS_BOOT_LOADING) {
        int b=e->api.boot?e->api.boot(e->api.context,now):1;
        if(b>0)boot_state(e,CFS_BOOT_READY);
        else if(!b)e->loading_seen=now;
        else if(now-e->loading_seen>=CFS_LOADING_ORPHAN_MS) {
            boot_state(e,CFS_BOOT_STOCK);cfs_adult_end(e,CFS_END_CAMERA);e->state.mode=CFS_MODE_NORMAL;
            remove_object(e);e->state.phase=CFS_HIDDEN;status(e,now);return;
        }
    }
    unsigned gate=e->state.mode==CFS_MODE_CAMMY && e->loading==CFS_BOOT_LOADING;
    /* While the gate holds, neither the live view nor an owner's hide uncovers stock. */
    int live=!gate && e->state.mode==CFS_MODE_CAMMY && !menu && e->api.live_view && e->api.live_view(e->api.context,now);
    if((!menu && e->want_hidden && !gate) || live || e->suspended){remove_object(e);e->state.phase=e->suspended?CFS_SUSPENDED:CFS_HIDDEN;status(e,now);return;}
    if(now<e->start_at){status(e,now);return;}
    if(!e->object) {
        e->screen=e->api.screen(e->api.context);void *parent=e->api.top(e->api.context);
        if(!e->screen || !parent){e->state.phase=CFS_WAITING;status(e,now);return;}
        e->object=e->api.create(e->api.context,parent);
        if(!e->object){fault(e);e->state.phase=CFS_FAILED;e->suspended=1;status(e,now);return;}
        e->state.created++;e->current_frame=UINT_MAX;
        if(e->api.clickable)e->api.clickable(e->api.context,e->object);
        if(!e->object){status(e,now);return;}
    }
    if(e->state.mode==CFS_MODE_CHOOSING) {
        if(e->tap_pending) {
            e->tap_pending=0;
            mark(e,now,CFS_MARK_PICK,e->tap_y<CFS_CHOOSER_SPLIT_Y?1u:2u);
            if(e->tap_y<CFS_CHOOSER_SPLIT_Y){e->state.mode=CFS_MODE_NORMAL;remove_object(e);e->state.phase=CFS_HIDDEN;status(e,now);return;}
            e->state.mode=CFS_MODE_CAMMY;e->asleep=1;e->current_frame=UINT_MAX;
            if(e->loading_px){boot_state(e,CFS_BOOT_LOADING);e->loading_seen=now;gate=1;}
        } else {
            unsigned id=CFS_CHOOSER_ID(e->chooser_focus);
            if(e->current_frame!=id){
                const unsigned char *px=e->packed?e->decode(e->decode_ctx,id):
                    e->chooser+(size_t)e->chooser_focus*CFS_PIXELS_BYTES;
                if(px){if(!e->state.draws)mark(e,now,CFS_MARK_DRAW,id);e->current_frame=id;e->api.pixels(e->api.context,e->object,px);e->state.draws++;}
            }
            if(e->object)e->state.phase=CFS_VISIBLE;
            status(e,now);return;
        }
    }
    if(!menu && gate) { /* the built-in Loading frame; a missing one keeps the picture */
        const unsigned char *px=e->loading_px(e->loading_ctx);
        if(px && e->current_frame!=CFS_LOADING_FRAME){e->current_frame=CFS_LOADING_FRAME;e->api.pixels(e->api.context,e->object,px);e->state.draws++;}
        if(e->object)e->state.phase=CFS_VISIBLE;
        status(e,now);return;
    }
    if(menu) {
        unsigned frame=CFS_MENU_FRAME|(seq&(CFS_MENU_FRAME-1u));
        if(e->current_frame!=frame){e->current_frame=frame;e->api.pixels(e->api.context,e->object,menu);e->state.draws++;}
        if(e->object)e->state.phase=CFS_VISIBLE;
        status(e,now);return;
    }
    if(e->current_frame!=UINT_MAX && (e->current_frame&CFS_MENU_FRAME))e->current_frame=UINT_MAX; /* menu closed */
    if(e->current_frame==CFS_LOADING_FRAME)e->current_frame=UINT_MAX; /* gate opened */
    /* Visual phase wraps after49days; ownership/deadlines remain64bit. */
    uint32_t age=(uint32_t)(now-e->idle_at);
    /* Asleep until a session owner commands the face (Adam 2026-11-16). Idle is a settled
     * neutral; the automatic mood carousel is gone (2026-11-17). */
    unsigned mood=e->asleep?CFS_SLEEPY:(e->state.idle?0u:e->state.expression);
    unsigned blink=sleeping(e)?1u:eyes_closed(e,now);unsigned frame=mood*2u+blink;
    /* Two-character packs take turns in front while idle; a commanded mood keeps whoever
     * is in front (Adam 2026-11-16). */
    if(e->turns && e->state.idle && !e->asleep)e->front=e->front_base^(quotient(age,CFS_TURN_MS,NULL)&1u);
    const unsigned char *px=e->packed?NULL:e->frames[frame];
    if(e->turns && e->front){if(!e->packed)px=e->turned[frame];frame+=CFS_TURN_ID;}
    if(e->speaking && (e->talk || e->packed)) {
        unsigned phase=quotient(age,180u,NULL)&1u;
        unsigned at=e->front*2u+phase;
        frame=0x08000000u+at;px=e->packed?NULL:e->talk+(size_t)at*CFS_PIXELS_BYTES;
    }
    if(sleeping(e) && (e->sleep || e->packed)) { /* sleep loop: breathing and a drifting z */
        unsigned i;(void)quotient(quotient((uint32_t)(now-e->sleep_at),CFS_SLEEP_PERIOD_MS/CFS_SLEEP_FRAMES,NULL),CFS_SLEEP_FRAMES,&i);
        frame=CFS_SLEEP_ID(i);px=e->packed?NULL:e->sleep+(size_t)i*CFS_PIXELS_BYTES;
    }
    e->state.expression=mood;
    if(e->current_frame!=frame) {
        if(e->packed)px=e->decode(e->decode_ctx,frame);
        e->current_frame=frame; /* a failed decode keeps the picture; the caller swaps packs */
        if(px){e->api.pixels(e->api.context,e->object,px);e->state.draws++;}
    }
    if(e->object)e->state.phase=CFS_VISIBLE;
    status(e,now);
}
void cfs_enable_chooser(struct cfs_engine *e,const unsigned char *pixels) {
    /* CammyOS starts selected: OK alone picks it, up moves to Normal (Adam 2026-11-16). */
    if(pixels || e->packed){e->chooser=pixels;e->state.mode=CFS_MODE_CHOOSING;e->chooser_focus=2;}
}
void cfs_enable_sleep(struct cfs_engine *e,const unsigned char *base) {e->sleep=base;}
void cfs_enable_loading(struct cfs_engine *e,const unsigned char *(*make)(void *),void *ctx) {e->loading_px=make;e->loading_ctx=ctx;}
int cfs_set_pack(struct cfs_engine *e,const unsigned char *const frames[CFS_FRAMES],
                 const unsigned char *const turned[CFS_FRAMES],const unsigned char *sleep,
                 const unsigned char *chooser) {
    if(!frames)return -1;
    for(unsigned i=0;i<CFS_FRAMES;i++)if(!frames[i] || (turned && !turned[i]))return -1;
    for(unsigned i=0;i<CFS_FRAMES;i++){e->frames[i]=frames[i];e->turned[i]=turned?turned[i]:NULL;}
    e->turns=turned!=NULL;e->front=0;e->sleep=sleep;e->packed=0;
    /* Keep the new pack's first character in front until the next idle turn. */
    e->front_base=e->turns?quotient((uint32_t)(e->last_now-e->idle_at),CFS_TURN_MS,NULL)&1u:0;
    if(chooser)e->chooser=chooser;
    e->current_frame=UINT_MAX; /* redraw from the new bank on the next tick */
    return 0;
}
int cfs_set_packed(struct cfs_engine *e,const unsigned char *(*decode)(void *,uint32_t),void *ctx) {
    if(!decode)return -1;
    e->decode=decode;e->decode_ctx=ctx;e->packed=1;e->turns=0;e->front=0;e->front_base=0;
    e->current_frame=UINT_MAX;return 0;
}
void cfs_tap(struct cfs_engine *e,int32_t x,int32_t y) {
    e->state.taps++;e->state.last_tap=((uint32_t)x&0xffffu)<<16|((uint32_t)y&0xffffu);
    if(e->state.mode==CFS_MODE_CHOOSING){e->tap_x=x;e->tap_y=y;e->tap_pending=1;}
}
/* Up/down focus a half (CammyOS is focused at the start); OK picks the focused half
 * exactly as a tap there would. */
void cfs_key(struct cfs_engine *e,uint32_t code) {
    /* A hidden chooser takes no keys: they belong to the stock window on screen. */
    if(e->state.mode!=CFS_MODE_CHOOSING || !e->object || e->state.phase!=CFS_VISIBLE)return;
    if(code==CFS_KEY_UP)e->chooser_focus=1;
    else if(code==CFS_KEY_DOWN)e->chooser_focus=2;
    else if(code==CFS_KEY_OK && e->chooser_focus){e->tap_x=178;e->tap_y=e->chooser_focus==1?0:399;e->tap_pending=1;}
}
void cfs_detach(struct cfs_engine *e) {
    remove_object(e);e->detached=1;e->state.phase=CFS_DETACHED;e->state.owner=0;e->state.deadline=0;
    atomic_store_explicit(e->page->header+10,0,memory_order_release);status(e,e->last_now);
    atomic_store_explicit(e->page->header+11,0,memory_order_release);
}
#endif
