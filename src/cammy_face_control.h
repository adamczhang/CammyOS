/* SPDX-License-Identifier: MIT. Authenticated HTTP -> bounded Cammy RAM IPC. */
#define CFS_CLIENT_ONLY
#include "../native/face_service/face_service.c"
#include "../native/face_service/ipc_file.c"
#include "../native/ui/cammy_ui_page.h"
#include <inttypes.h>
static const char *face_path;
static struct cfs_page *face_page;
static uint64_t face_generation,face_request,face_pending,face_owner_id;
static bool face_active;
static double face_renew_at;
/* Send the requested override until the GUI has read it, then send HOLD.
 * A physical pick can end the override without a renewal bringing it back. */
enum { FACE_PACK_DEFAULT, FACE_PACK_CAMMY, FACE_PACK_CAMMY_HELD, FACE_PACK_CHILD, FACE_PACK_CHILD_HELD };
static unsigned face_grant;static uint64_t face_grant_request;
static int face_snapshot(struct cfs_status *s) {
    if(!face_path)return -1;
    if(!face_page && cfs_map_file(face_path,0,&face_page))return -1;
    if(atomic_load_explicit(face_page->header,memory_order_acquire)!=CFS_MAGIC || atomic_load(face_page->header+1)!=CFS_ABI || atomic_load(face_page->header+2)!=CFS_PAGE_BYTES || !atomic_load(face_page->header+10) || !atomic_load(face_page->header+11))return -1;
    uint32_t boot[4];if(cfs_current_boot(boot))return -1;
    for(unsigned i=0;i<4;i++)if(boot[i]!=atomic_load(face_page->header+4+i))return -1;
    if(cfs_read_status(face_page,s))return -1;
    uint64_t now=(uint64_t)(monotime()*1000.0);
    if(!s->gui_ms || now<s->gui_ms || now-s->gui_ms>1000u || s->phase==CFS_DETACHED || s->phase==CFS_FAILED)return -1;
    return 0;
}
static int face_send(unsigned op,unsigned expression,bool no_owner) {
    struct cfs_status s;if(face_snapshot(&s))return -1;
    if(face_request<s.request)face_request=s.request;
    if(face_request==UINT64_MAX || s.generation==UINT64_MAX)return -1;
    if(no_owner || !face_generation || (s.owner==0 && s.generation>=face_generation)) {
        if(face_generation<s.generation)face_generation=s.generation;
        if(face_generation==UINT64_MAX)return -1;
        face_generation++;face_owner_id=face_generation;
    }
    if(op==CFS_RENEW && (!s.owner || s.generation!=face_generation || s.owner!=face_owner_id))return -1;
    uint64_t now=(uint64_t)(monotime()*1000.0),until=now+2000u;
    if(!no_owner && (uint64_t)(lease_end*1000.0)<until)until=(uint64_t)(lease_end*1000.0);
    struct cfs_command c={0};c.request=++face_request;c.owner=no_owner?0:face_owner_id;c.generation=face_generation;
    c.deadline=until;c.instance=pair(atomic_load(face_page->header+8),atomic_load(face_page->header+9));
    for(unsigned i=0;i<4;i++)c.boot[i]=atomic_load(face_page->header+4+i);
    if(face_grant_request && s.request>=face_grant_request){
        if(face_grant==FACE_PACK_CAMMY)face_grant=FACE_PACK_CAMMY_HELD;
        else if(face_grant==FACE_PACK_CHILD)face_grant=FACE_PACK_CHILD_HELD;
    }
    c.op=op;c.expression=expression;
    if(!no_owner){
        if(face_grant==FACE_PACK_CAMMY)c.pack=CFS_PACK_GRANT;
        else if(face_grant==FACE_PACK_CHILD)c.pack=CFS_PACK_CHILD;
        else if(face_grant!=FACE_PACK_DEFAULT)c.pack=CFS_PACK_HOLD;
    }
    if(cfs_publish(face_page,&c))return -1;
    if((c.pack==CFS_PACK_GRANT || c.pack==CFS_PACK_CHILD) && !face_grant_request)face_grant_request=c.request;
    face_pending=c.request;return 0;
}
static void face_cancel(void) {face_active=false;face_grant=0;face_grant_request=0;if(face_path)(void)face_send(CFS_STOP,0,true);}
static void face_tick(void) {
    if(!face_active || !owner[0] || monotime()<face_renew_at)return;
    face_renew_at=monotime()+0.25;
    struct cfs_status s;if(face_snapshot(&s)){face_active=false;face_grant=0;return;}
    if(s.request<face_pending)return; /* never overwrite an unapplied new intent */
    if(s.error || face_send(CFS_RENEW,0,false)){face_active=false;face_grant=0;return;}
}
static bool face_available(void){struct cfs_status s;return face_snapshot(&s)==0;}
static bool face_link_active(void){
    struct cfs_status s;
    return face_active && owner[0] && !face_snapshot(&s) && s.owner==face_owner_id &&
           s.deadline>(uint64_t)(monotime()*1000.0);
}
static const char *face_shown_id(void){struct cfs_status s;if(face_snapshot(&s))return "unknown";
    uint32_t v=atomic_load_explicit(face_page->header+CFS_HEADER_PACK,memory_order_acquire)&0xffu;return v&&v<=CUI_PACK_COUNT?cui_pack_id(v-1u):"unknown";}
static bool face_ccc_available(void){struct cfs_status s;return !face_snapshot(&s)&&(atomic_load_explicit(face_page->header+CFS_HEADER_PACKS,memory_order_acquire)&(1u<<CUI_PACK_CAMMY));}
/* Pack readback (CCC contract). shown/held/last_end come from the face library's header
 * words; "unknown" when the library is not live. */
/* Saved physical choice (an invalid ID falls back to Pikachu). */
static const char *face_pack_boot(void){const char *id=face_pack();int i=cui_pack_index(id,(unsigned)strlen(id));return cui_pack_id(i<0?0u:(uint32_t)i);}
static int face_pack_json(char *out,size_t cap) {
    static const char *const ends[]={"none","ccc","lease","physical","camera","unavailable"};
    struct cfs_status s;bool live=!face_snapshot(&s);
    uint32_t w=live?atomic_load_explicit(face_page->header+CFS_HEADER_PACK,memory_order_acquire):0u;
    uint32_t built=live?atomic_load_explicit(face_page->header+CFS_HEADER_PACKS,memory_order_acquire):0u;
    uint32_t shown=w&0xffu,end=(w>>16)&0xffu;
    const char *cammy_available=built&(1u<<CUI_PACK_CAMMY)?"true":"false";
    return snprintf(out,cap,"{\"live\":%s,\"ids\":[\"pikachu\",\"fin-fur\",\"cammy\"],\"child_ids\":[\"pikachu\",\"fin-fur\"],\"ccc_id\":\"cammy\",\"ccc_available\":%s,\"shown\":\"%s\",\"boot_choice\":\"%s\",\"ccc_grant\":{\"requested\":%s,\"held\":%s,\"last_end\":\"%s\"},\"physical_can_select_ccc\":%s,\"authorization\":false}",
        live?"true":"false",cammy_available,shown&&shown<=CUI_PACK_COUNT?cui_pack_id(shown-1u):"unknown",
        face_pack_boot(),(face_grant==FACE_PACK_CAMMY||face_grant==FACE_PACK_CAMMY_HELD)?"true":"false",
        live&&(w&0x100u)?"true":"false",end<sizeof(ends)/sizeof(*ends)?ends[end]:"unknown",cammy_available);
}
static void face_status(int fd) {
    struct cfs_status s;char out[1536],pack[640];(void)face_pack_json(pack,sizeof(pack));
    if(face_snapshot(&s)){respond(fd,200,"{\"available\":false,\"backend\":\"native-gui-ipc\",\"physical_display_confirmed\":false}\n");return;}
    snprintf(out,sizeof(out),"{\"available\":true,\"speaking_animation\":true,\"backend\":\"native-gui-ipc\",\"phase\":%u,\"expression\":%u,\"idle_loop\":%s,\"suspended\":%s,\"last_processed_request_id\":%" PRIu64 ",\"gui_applied_request_id\":%" PRIu64 ",\"last_error\":%u,\"gui_monotonic_ms\":%" PRIu64 ",\"draws\":%u,\"objects_created\":%u,\"objects_removed\":%u,\"mode\":\"%s\",\"taps\":%u,\"last_tap\":[%d,%d],\"tracking\":%s,\"physical_display_confirmed\":false,\"pack\":%s}\n",s.phase,s.expression,s.idle?"true":"false",s.suspended?"true":"false",s.request,s.applied_request,s.error,s.gui_ms,s.draws,s.created,s.removed,s.mode==CFS_MODE_CHOOSING?"choosing":s.mode==CFS_MODE_NORMAL?"normal":s.mode==CFS_MODE_CAMMY?"cammyos":"legacy",s.taps,(int)(int16_t)(s.last_tap>>16),(int)(int16_t)(s.last_tap&0xffffu),s.tracking==CFS_TRACK_ON?"true":s.tracking==CFS_TRACK_OFF?"false":"null",pack);respond(fd,200,out);
}
