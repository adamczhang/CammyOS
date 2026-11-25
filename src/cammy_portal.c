/* SPDX-License-Identifier: MIT */
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>

#ifndef VERSION
#define VERSION "0.5.0"
#endif
#define MAX_REQUEST 222192
#define MAX_BODY 214000
#define MAX_HEADERS 8192
#define REQUEST_READ_AHEAD 4096
#define INITIAL_REQUEST_CAPACITY (MAX_HEADERS+REQUEST_READ_AHEAD)
#define EVENT_COUNT 32
#define CLIENT_COUNT 8
#define PORTAL_FIXED_FDS 7 /* listener, preview, input, audio pipe, probe pipe, child wake, RTSP check */
/* A request's body stays in its client buffer: handlers run synchronously
 * before the slot can be reused, and no handler keeps the pointer. */
struct request { char method[8], path[128], auth[160], agent[49], boot[33], lease[33]; const char *body; };
/* header_len stays 0 until the header terminator has been seen; need is the
 * validated Content-Length once it has. */
struct client {int fd; bool deferred, streaming; size_t used, capacity, header_len, need; double deadline; struct request request; char *buf;};
static struct client clients[CLIENT_COUNT];
static int listener_fd=-1;
static volatile sig_atomic_t running = 1;
static char token[129], owner[49];
/* Who holds the lease, for Cammy's Agent screen (0.7): an optional free-text agent ID the
 * client declares (e.g. its T3 thread), its address and when it took the lease. */
static char owner_label[81], owner_address[16];static double owner_since;
/* Active face pack (0.7.1): the agent's persona follows it ("Hi, I'm Pikachu on CammyOS").
 * One ID, [a-z0-9-]{1,32}, in /etc/cammy/face-pack; absent or invalid: pikachu. */
static const char *face_pack_file="/etc/cammy/face-pack";
static const char *face_pack(void){static char id[33];char raw[40]={0};int f=open(face_pack_file,O_RDONLY|O_CLOEXEC);
    ssize_t n=f>=0?read(f,raw,sizeof(raw)-1):-1;if(f>=0)close(f);size_t k=0;
    while(n>0&&k<(size_t)n&&k<32&&(islower((unsigned char)raw[k])||isdigit((unsigned char)raw[k])||raw[k]=='-'))k++;
    if(n<=0||!k||(k<(size_t)n&&raw[k]!='\n'))return "pikachu";
    memcpy(id,raw,k);id[k]=0;return id;}
static double lease_end;
static char lease_id[33];
static bool strict_control;
static uint64_t input_client_sequence,face_client_sequence;
static int preview_fd = -1;
static bool vendor_backend;
static unsigned long event_seq;
struct event { unsigned long seq; char type[48]; };
static struct event events[EVENT_COUNT];
static double monotime(void) { struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9; }
static void stop_signal(int sig) { (void)sig; running = 0; }
/* SIGCHLD self-pipe (0.9): a child exit wakes poll at once, so active children need
 * no fixed 25 ms tick. Children close both ends (probe_child_close_fds). */
static int child_wake[2]={-1,-1};
static void child_signal(int sig) {
    (void)sig;int saved=errno;
    if(child_wake[1]>=0){ssize_t n=write(child_wake[1],"c",1);(void)n;}
    errno=saved;
}
static bool child_wake_start(void) {
    if(pipe(child_wake))return false;
    for(int i=0;i<2;i++)if(fcntl(child_wake[i],F_SETFD,FD_CLOEXEC)<0 || fcntl(child_wake[i],F_SETFL,O_NONBLOCK)<0){
        close(child_wake[0]);close(child_wake[1]);child_wake[0]=child_wake[1]=-1;return false;
    }
    struct sigaction action;memset(&action,0,sizeof(action));action.sa_handler=child_signal;
    sigemptyset(&action.sa_mask);action.sa_flags=SA_RESTART|SA_NOCLDSTOP;
    if(sigaction(SIGCHLD,&action,NULL)){close(child_wake[0]);close(child_wake[1]);child_wake[0]=child_wake[1]=-1;return false;}
    return true;
}
static void child_wake_drain(void) {char bytes[64];while(read(child_wake[0],bytes,sizeof(bytes))>0){}}
/* The loop's ticks call waitpid only after a SIGCHLD (always without the self-pipe).
 * Cleared before each poll; a SIGCHLD during the ticks leaves a byte that wakes the next poll. */
static bool child_event=true;
static bool child_reap_due(void) {return child_event || child_wake[0]<0;}
static void record_request(const char *kind) {
    if(!strict_control)return;
    int fd=open("/tmp/cammy-runtime/last-request",O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC|O_NOFOLLOW,0600);
    if(fd>=0){ssize_t written=write(fd,kind,strlen(kind));(void)written;close(fd);}
}
static void emit(const char *name) { struct event *e = &events[event_seq % EVENT_COUNT]; e->seq = ++event_seq; snprintf(e->type, sizeof(e->type), "%s", name); }
static void preview_stop(void);
static void probe_cancel_all(void);
static void audio_cancel(void);
static void audio_client_closed(int fd);
static void face_cancel(void);
static void input_cancel(void);
static void media_cancel(void);
static void speaker_cancel(void);
static void speaker_tick(void);
static int new_lease_id(void);
static void expire(void) { if (owner[0] && monotime() >= lease_end) { media_cancel();speaker_cancel();input_cancel();face_cancel();audio_cancel();preview_stop(); owner[0] = 0; emit("session.expired"); } }
static bool equal_token(const char *given) { size_t n = strlen(token), m = strlen(given); unsigned diff = (unsigned)(n ^ m); for (size_t i = 0; i < n; i++) diff |= (unsigned char)token[i] ^ (i < m ? (unsigned char)given[i] : 0); return diff == 0; }
static bool valid_agent(const char *s) { size_t n = strlen(s); if (!n || n > 48) return false; for (size_t i=0; i<n; i++) if (!isalnum((unsigned char)s[i]) && s[i]!='-' && s[i]!='_' && s[i]!='.') return false; return true; }
static int send_all(int fd, const char *buf, size_t n) { while (n) { ssize_t r = send(fd, buf, n, MSG_NOSIGNAL); if (r < 0 && errno == EINTR) continue; if (r <= 0) return -1; buf += r; n -= (size_t)r; } return 0; }
static const char *reason(int status) { switch(status) { case 200:return "OK";case 201:return "Created";case 202:return "Accepted";case 400:return "Bad Request";case 401:return "Unauthorized";case 404:return "Not Found";case 405:return "Method Not Allowed";case 409:return "Conflict";case 413:return "Content Too Large";case 501:return "Not Implemented";case 503:return "Service Unavailable";default:return "Error";} }
static void respond(int fd, int status, const char *body) { char h[384]; int n = snprintf(h,sizeof(h),"HTTP/1.1 %d %s\r\nContent-Type: application/json\r\nContent-Length: %zu\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n\r\n",status,reason(status),strlen(body)); if (n>0 && (size_t)n<sizeof(h)) { send_all(fd,h,(size_t)n); send_all(fd,body,strlen(body)); } }
static void error(int fd,int status,const char *message) { char b[256]; snprintf(b,sizeof(b),"{\"error\":\"%s\"}\n",message);respond(fd,status,b); }

#include "cammy_preview.h"

/* Fixed allowlist diagnostics: no shell, arbitrary paths, device ioctls or writes. */
static const char *diagnostic_prefix="";
static void diagnostic_text(const char *path, char *out, size_t cap) {
    char full[1024];int len=snprintf(full,sizeof(full),"%s%s",diagnostic_prefix,path);
    char raw[257]; FILE *f=len>0 && (size_t)len<sizeof(full)?fopen(full,"r"):NULL;
    if(!f) { snprintf(out,cap,"null");return; }
    size_t n=fread(raw,1,sizeof(raw)-1,f);fclose(f);
    while(n && (raw[n-1]=='\n' || raw[n-1]=='\r'))n--;
    size_t used=0;out[used++]='"';
    for(size_t i=0;i<n && used+7<cap;i++) {
        unsigned char c=(unsigned char)raw[i];
        if(c=='"' || c=='\\') {out[used++]='\\';out[used++]=(char)c;}
        else if(c<32 || c>=127) {int k=snprintf(out+used,cap-used,"\\u%04x",(unsigned)c);if(k>0)used+=(size_t)k;}
        else out[used++]=(char)c;
    }
    out[used++]='"';out[used]=0;
}
static void diagnostics_json(FILE *destination) {
    char release[1600],state[1600],native_video[1600],size[1600],bits[1600],cards[1600],kernel[1600],stride[1600],playback[1600],capture[1600],out[18000];
    diagnostic_text("/tmp/cammy-runtime/RELEASE",release,sizeof(release));
    diagnostic_text("/tmp/cammy-runtime/state",state,sizeof(state));
    diagnostic_text("/tmp/cammy-runtime/native-video.state",native_video,sizeof(native_video));
    diagnostic_text("/proc/sys/kernel/osrelease",kernel,sizeof(kernel));
    diagnostic_text("/sys/class/graphics/fb0/virtual_size",size,sizeof(size));
    diagnostic_text("/sys/class/graphics/fb0/bits_per_pixel",bits,sizeof(bits));
    diagnostic_text("/proc/asound/cards",cards,sizeof(cards));
    diagnostic_text("/sys/class/graphics/fb0/stride",stride,sizeof(stride));
    diagnostic_text("/proc/asound/card0/pcm0p/sub0/hw_params",playback,sizeof(playback));
    diagnostic_text("/proc/asound/card0/pcm0c/sub0/hw_params",capture,sizeof(capture));
    snprintf(out,sizeof(out),"{\"portal_version\":\"" VERSION "\",\"read_only\":true,\"kernel_release\":%s,\"runtime_release\":%s,\"runtime_state\":%s,\"native_video_boot_state\":%s,\"startup_pending\":%s,\"display\":{\"virtual_size\":%s,\"bits_per_pixel\":%s,\"stride_bytes\":%s},\"audio_cards\":%s,\"playback_hw_params\":%s,\"capture_hw_params\":%s,\"hardware_adapters_available\":false}\n",
             kernel,release,state,native_video,access("/etc/cammy/startup.pending",F_OK)==0?"true":"false",size,bits,stride,cards,playback,capture);
    fputs(out,destination);
}

static void close_client(int fd) {
    audio_client_closed(fd);
    for(int i=0;i<CLIENT_COUNT;i++)if(clients[i].fd==fd){close(fd);clients[i].fd=-1;clients[i].deferred=false;free(clients[i].buf);clients[i].buf=NULL;return;}
}

#include "cammy_probes.h"

static int input_fd;
static void probe_child_close_fds(void) {
    if(input_fd>=0)close(input_fd);
    if(listener_fd>=0)close(listener_fd);
    if(preview_fd>=0)close(preview_fd);
    if(preview_rtsp_fd>=0)close(preview_rtsp_fd);
    for(int i=0;i<CLIENT_COUNT;i++)if(clients[i].fd>=0)close(clients[i].fd);
    signal(SIGCHLD,SIG_DFL);
    for(int i=0;i<2;i++)if(child_wake[i]>=0){close(child_wake[i]);child_wake[i]=-1;}
}

#include "cammy_audio_stream.h"
#include "cammy_face_control.h"
#include "cammy_input_guardian.h"
#include "cammy_media_prepare.h"

/* Physical capture switch lives on the boot-bound face page. A sequence change
 * closes the old mic even if a second press unmuted before the portal tick. */
static uint32_t capture_sequence;
static void capture_switch(bool discover) {
    if(discover && !face_page && face_path)(void)face_available();
    if(!face_page)return;
    uint32_t seq=atomic_load_explicit(face_page->header+CFS_HEADER_CAPTURE,memory_order_acquire);
    if(seq!=capture_sequence){capture_sequence=seq;record_request("capture-mode\n");audio_cancel();}
}

/* Strict flat JSON object: sufficient for this version's small command schema.
 * Rejects escapes, duplicate keys, nesting, junk numbers and unknown fields. */
struct command { char agent[49], probe[32], label[81]; int ttl; bool has_agent, has_ttl, has_probe, has_label; };
static void ws(const char **p) { while (**p && isspace((unsigned char)**p)) ++*p; }
static bool string_value(const char **p, char *out, size_t cap) { if (*(*p)++ != '"') return false; size_t n=0; while (**p && **p!='"') { unsigned char c=(unsigned char)*(*p)++; if (c<32 || c=='\\' || n+1>=cap) return false;out[n++]=(char)c; } if (**p!='"') return false; ++*p;out[n]=0;return true; }
static bool parse_command(const char *p, struct command *c) {
    memset(c,0,sizeof(*c));c->ttl=30; ws(&p);if (*p++!='{') return false;ws(&p);
    if (*p=='}') {p++;ws(&p);return !*p;}
    for (;;) {
        char key[32]; if (!string_value(&p,key,sizeof(key))) return false;ws(&p);if (*p++!=':') return false;ws(&p);
        if (!strcmp(key,"agent")) { if(c->has_agent || !string_value(&p,c->agent,sizeof(c->agent)) || !valid_agent(c->agent)) return false;c->has_agent=true; }
        else if(!strcmp(key,"ttl")) { char *end; errno=0; if(c->has_ttl || !isdigit((unsigned char)*p)) return false;long v=strtol(p,&end,10);if(errno || end==p || v<1 || v>120 || (*p=='0' && end-p>1)) return false;c->ttl=(int)v;p=end;c->has_ttl=true; }
        else if(!strcmp(key,"probe")) {if(c->has_probe || !string_value(&p,c->probe,sizeof(c->probe)))return false;c->has_probe=true;}
        else if(!strcmp(key,"label")) {if(c->has_label || !string_value(&p,c->label,sizeof(c->label)) || !c->label[0])return false;c->has_label=true;}
        else return false;
        ws(&p);if(*p=='}') {p++;ws(&p);return !*p;}if(*p++!=',') return false;ws(&p);
    }
}

static bool control_sequence(const char **p,uint64_t *value){if(!isdigit((unsigned char)**p))return false;char *end;errno=0;unsigned long long v=strtoull(*p,&end,10);if(errno||!v||v>9007199254740991ULL||end==*p||(**p=='0'&&end-*p>1))return false;*value=(uint64_t)v;*p=end;return true;}
static bool parse_face(const char *p,unsigned *op,unsigned *expression,uint64_t *sequence) {
    const char *names[]={"neutral","happy","angry","sad","doubt","sleepy"};
    bool have_action=false,have_expression=false;*sequence=0;*op=CFS_SHOW;*expression=0;
    ws(&p);if(*p++!='{')return false;ws(&p);if(*p=='}')return false;
    for(;;){char key[24],value[24];if(!string_value(&p,key,sizeof(key)))return false;ws(&p);if(*p++!=':')return false;ws(&p);
        if(!strcmp(key,"sequence")){if(*sequence||!control_sequence(&p,sequence))return false;}
        else if(!string_value(&p,value,sizeof(value)))return false;
        else if(!strcmp(key,"expression")){if(have_expression)return false;have_expression=true;unsigned i;for(i=0;i<6;i++)if(!strcmp(value,names[i]))break;if(i==6)return false;*expression=i;}
        else if(!strcmp(key,"action")){if(have_action)return false;have_action=true;if(!strcmp(value,"show")||!strcmp(value,"replay"))*op=CFS_SHOW;else if(!strcmp(value,"speak"))*op=CFS_SPEAK;else if(!strcmp(value,"hide"))*op=CFS_HIDE;else if(!strcmp(value,"idle"))*op=CFS_IDLE;else return false;}
        else return false;
        ws(&p);if(*p=='}'){p++;ws(&p);return !*p&&*sequence;}if(*p++!=',')return false;ws(&p);
    }
}

/* POST /v1/face/pack: {"pack":"cammy"|"child","sequence":n}, strict like parse_face. */
static bool parse_face_pack(const char *p,bool *cammy,uint64_t *sequence) {
    bool have=false;*sequence=0;*cammy=false;
    ws(&p);if(*p++!='{')return false;ws(&p);
    for(;;){char key[24],value[24];if(!string_value(&p,key,sizeof(key)))return false;ws(&p);if(*p++!=':')return false;ws(&p);
        if(!strcmp(key,"sequence")){if(*sequence||!control_sequence(&p,sequence))return false;}
        else if(!strcmp(key,"pack")){if(have||!string_value(&p,value,sizeof(value)))return false;have=true;
            if(!strcmp(value,"cammy"))*cammy=true;else if(strcmp(value,"child"))return false;}
        else return false;
        ws(&p);if(*p=='}'){p++;ws(&p);return !*p&&*sequence&&have;}if(*p++!=',')return false;ws(&p);
    }
}

static bool parse_input(const char *p,unsigned *button,unsigned *duration,uint64_t *sequence){
    bool have_button=false,have_action=false;*sequence=0;char action[16]="tap";*button=0;*duration=150;
    ws(&p);if(*p++!='{')return false;ws(&p);if(*p=='}')return false;
    for(;;){char key[24],value[24];if(!string_value(&p,key,sizeof(key)))return false;ws(&p);if(*p++!=':')return false;ws(&p);
        if(!strcmp(key,"sequence")){if(*sequence||!control_sequence(&p,sequence))return false;}
        else if(!string_value(&p,value,sizeof(value)))return false;
        else if(!strcmp(key,"button")){if(have_button)return false;have_button=true;unsigned i;for(i=0;i<INPUT_BUTTONS;i++)if(!strcmp(value,input_names[i]))break;if(i==INPUT_BUTTONS)return false;*button=i;}
        else if(!strcmp(key,"action")){if(have_action||strlen(value)>=sizeof(action))return false;have_action=true;strcpy(action,value);}
        else return false;
        ws(&p);if(*p=='}'){p++;ws(&p);if(*p)return false;break;}if(*p++!=',')return false;ws(&p);
    }
    if(!*sequence)return false;
    if(!strcmp(action,"release")){*duration=0;return true;}
    if(!have_button)return false;
    if(!strcmp(action,"hold")){if(*button>1)return false;*duration=300;return true;}
    return !strcmp(action,"tap");
}

/* Parses one complete header block (header_len includes the blank line).
 * Called once per request; r->body is set by the caller. */
static int parse_headers(const char *buf, size_t header_len, struct request *r, size_t *need_out) {
    char copy[MAX_HEADERS+1];
    if(header_len>MAX_HEADERS)return 413;
    size_t need=0;bool have_length=false;
    memset(r,0,sizeof(*r));
    memcpy(copy,buf,header_len);copy[header_len]=0;
    char *save=NULL,*line=strtok_r(copy,"\r\n",&save);char version[16],extra;
    if(!line || sscanf(line,"%7s %127s %15s %c",r->method,r->path,version,&extra)!=3 || strcmp(version,"HTTP/1.1"))return 400;
    bool seen_auth=false,seen_agent=false,seen_boot=false,seen_lease=false;
    while((line=strtok_r(NULL,"\r\n",&save))) {
        char *colon=strchr(line,':');if(!colon)return 400;*colon=0;char *value=colon+1;while(*value==' ' || *value=='\t')value++;
        if(!strcasecmp(line,"Content-Length")) {char *e;if(have_length || !isdigit((unsigned char)*value))return 400;errno=0;unsigned long v=strtoul(value,&e,10);if(errno || *e || v>MAX_BODY)return 413;need=v;have_length=true;}
        else if(!strcasecmp(line,"Transfer-Encoding"))return 400;
        else if(!strcasecmp(line,"Authorization")) {if(seen_auth || strlen(value)>=sizeof(r->auth))return 400;strcpy(r->auth,value);seen_auth=true;}
        else if(!strcasecmp(line,"X-Cammy-Lease-ID")){if(seen_lease||strlen(value)!=32)return 400;for(unsigned i=0;i<32;i++)if(!((value[i]>='0'&&value[i]<='9')||(value[i]>='a'&&value[i]<='f')))return 400;strcpy(r->lease,value);seen_lease=true;}
        else if(!strcasecmp(line,"X-Cammy-Boot-ID")){if(seen_boot||strlen(value)!=32)return 400;for(unsigned i=0;i<32;i++)if(!((value[i]>='0'&&value[i]<='9')||(value[i]>='a'&&value[i]<='f')))return 400;strcpy(r->boot,value);seen_boot=true;}
        else if(!strcasecmp(line,"X-Agent-ID")) {if(seen_agent || !valid_agent(value))return 400;strcpy(r->agent,value);seen_agent=true;}
    }
    if(!strcmp(r->method,"POST") && !have_length)return 400;
    if((need>1024 && strcmp(r->path,"/v1/speaker/play")) || header_len+need>MAX_REQUEST)return 413;
    *need_out=need;return 0;
}
/* Incremental parser: incomplete clients never block the control loop. Only
 * the bytes received since `old` are checked for NUL and for the header
 * terminator, and headers are parsed once. Returns 1 while incomplete, 0
 * with c->request ready (body NUL-terminated by the caller's buf[used]=0),
 * or an HTTP status. */
static int client_parse(struct client *c, size_t old) {
    if(memchr(c->buf+old,0,c->used-old))return 400;
    if(!c->header_len) {
        size_t end=0;
        for(size_t i=old>3?old-3:0;i+4<=c->used;i++) {
            const char *cr=memchr(c->buf+i,'\r',c->used-i-3);if(!cr)break;
            i=(size_t)(cr-c->buf);if(!memcmp(cr,"\r\n\r\n",4)){end=i+4;break;}
        }
        if(!end)return c->used>=MAX_HEADERS?413:1;
        int status=parse_headers(c->buf,end,&c->request,&c->need);if(status)return status;
        c->header_len=end;
        if(end+c->need>c->capacity){
            size_t capacity=end+c->need+REQUEST_READ_AHEAD;
            if(capacity>MAX_REQUEST)capacity=MAX_REQUEST;
            char *buf=realloc(c->buf,capacity+1);
            if(!buf)return 503;
            c->buf=buf;c->capacity=capacity;
        }
    }
    if(c->used<c->header_len+c->need)return 1;
    if(c->used!=c->header_len+c->need)return 400;
    c->request.body=c->buf+c->header_len;return 0;
}
static bool authorized_owner(int fd,const struct request *r) { if(!owner[0] || !r->agent[0] || strcmp(owner,r->agent)) { error(fd,409,"active control lease required for X-Agent-ID");return false; }
 if((r->lease[0]&&strcmp(r->lease,lease_id)) || (strict_control&&!r->lease[0]&&strcmp(owner,"cammy-boot"))){error(fd,409,"current control lease binding required");return false;}return true; }
static int new_lease_id(void){unsigned char bytes[16];int fd=open("/dev/urandom",O_RDONLY|O_CLOEXEC);if(fd<0)return -1;ssize_t n=read(fd,bytes,sizeof(bytes));close(fd);if(n!=(ssize_t)sizeof(bytes))return -1;for(unsigned i=0;i<16;i++)(void)snprintf(lease_id+i*2,3,"%02x",bytes[i]);return 0;}

static const char *portal_boot_id(void){static char hex[33];if(hex[0])return hex;uint32_t boot[4];if(cfs_current_boot(boot))return "";unsigned char bytes[16];memcpy(bytes,boot,sizeof(bytes));for(unsigned i=0;i<16;i++)(void)snprintf(hex+i*2,3,"%02x",bytes[i]);return hex;}
#include "cammy_speaker.h"

static void route(int fd,const struct request *r) {
    char out[4096],address[INET_ADDRSTRLEN]="127.0.0.1";
    struct sockaddr_in local; socklen_t local_size=sizeof(local);
    if(!getsockname(fd,(struct sockaddr*)&local,&local_size))
        (void)inet_ntop(AF_INET,&local.sin_addr,address,sizeof(address));
    expire();child_event=true;probe_tick();
    if(!strcmp(r->method,"GET") && !strcmp(r->path,"/health")) {respond(fd,200,"{\"service\":\"cammy-portal\",\"version\":\"" VERSION "\",\"status\":\"ok\"}\n");return;}
    if(strncmp(r->auth,"Bearer ",7) || !equal_token(r->auth+7)) {error(fd,401,"valid bearer token required");return;}
    if(strict_control && !strcmp(r->method,"POST") && strcmp(r->agent,"cammy-boot") && !r->boot[0]){error(fd,400,"control requires current boot binding");return;}
    if(r->boot[0] && strcmp(r->boot,portal_boot_id())){error(fd,409,"stale camera boot binding");return;}
    if(!strcmp(r->method,"GET") && !strcmp(r->path,"/v1/preview/status")){preview_status(fd);return;}
    if(!strcmp(r->method,"GET") && !strcmp(r->path,"/v1/probes")){probe_catalogue(fd);return;}
    if(!strncmp(r->path,"/v1/probes/",11)) {
        bool cancel=false;struct probe_job *job=probe_find(r->path+11,&cancel);
        if(!job){error(fd,404,"unknown or expired probe job");return;}
        if(!cancel && !strcmp(r->method,"GET")){probe_reply(fd,200,job);return;}
        if(cancel && !strcmp(r->method,"POST")) {
            struct command c;
            if(!parse_command(r->body,&c) || c.has_agent || c.has_ttl || c.has_probe){error(fd,400,"expected empty object");return;}
            if(!r->agent[0] || strcmp(job->agent,r->agent)){error(fd,409,"probe job owner required");return;}
            if(job==probe_active)probe_finish("cancelled");
            probe_reply(fd,200,job);return;
        }
        error(fd,404,"unknown probe endpoint or method");return;
    }
    if(!strcmp(r->method,"POST") && !strcmp(r->path,"/v1/probes")) {
        struct command c;
        if(!parse_command(r->body,&c) || !c.has_probe || c.has_agent || c.has_ttl || !r->agent[0]){error(fd,400,"expected probe and X-Agent-ID");return;}
        for(size_t i=0;i<sizeof(probe_names)/sizeof(probe_names[0]);i++)if(!strcmp(c.probe,probe_names[i])){probe_start_job(fd,(int)i,r->agent);return;}
        error(fd,400,"unknown probe ID");return;
    }
    if(!strcmp(r->method,"GET") && !strcmp(r->path,"/v1/diagnostics")) {probe_start_compat(fd);return;}
    if(!strcmp(r->method,"GET") && !strcmp(r->path,"/v1/identity")) {
        snprintf(out,sizeof(out),"{\"os\":\"CammyOS\",\"portal_version\":\"" VERSION "\",\"image_marker_present\":%s,\"profile\":\"m105-stock-fork\",\"kernel_replaced\":false}\n",access("/etc/cammy-release",R_OK)==0?"true":"false");respond(fd,200,out);return;
    }
    if(!strcmp(r->method,"GET") && !strcmp(r->path,"/v1/capabilities")) {
        snprintf(out,sizeof(out),"{\"api_version\":\"1\",\"control_leases\":true,\"events\":true,\"metadata_probes\":true,\"preview\":{\"available\":%s,\"backend\":\"stock-sdvcam\",\"status_available\":true,\"rtsp_url\":\"rtsp://%s:8554/ch00\"},\"audio_stream\":{\"configured\":%s,\"profile_ready\":%s,\"format\":\"S16_LE\",\"sample_rate\":16000,\"channels\":1},\"audio_capture\":{\"available\":false},\"speech\":{\"available\":false},\"speaker_pcm\":{\"configured\":%s,\"hardware_verified\":false},\"face\":{\"available\":%s,\"backend\":\"native-gui-ipc\",\"packs\":[\"pikachu\",\"fin-fur\",\"cammy\"],\"ccc_pack\":\"cammy\",\"ccc_pack_control\":\"/v1/face/pack\",\"ccc_pack_available\":%s},\"gimbal\":{\"available\":false},\"arbitrary_shell\":false}\n",vendor_backend?"true":"false",address,audio_enabled?"true":"false",audio_profile_ready(true)?"true":"false",speaker_available()?"true":"false",face_available()?"true":"false",face_ccc_available()?"true":"false");respond(fd,200,out);return;
    }
    if(!strcmp(r->method,"GET") && !strcmp(r->path,"/v1/audio/stream/status")){audio_status(fd);return;}
    if(!strcmp(r->method,"GET") && !strcmp(r->path,"/v1/state")) {
        capture_switch(true);
        snprintf(out,sizeof(out),"{\"version\":\"" VERSION "\",\"owner\":%s%s%s,\"lease_remaining_seconds\":%.3f,\"preview_active\":%s,\"event_sequence\":%lu,\"sensing_default\":\"off\",\"boot_id\":\"%s\",\"owner_label\":\"%s\",\"owner_address\":\"%s\",\"owner_since_seconds\":%ld,\"face_pack\":\"%s\",\"face_pack_shown\":\"%s\",\"capture_sequence\":%u}\n",owner[0]?"\"":"",owner[0]?owner:"null",owner[0]?"\"":"",owner[0]?lease_end-monotime():0,preview_fd>=0?"true":"false",event_seq,portal_boot_id(),owner[0]?owner_label:"",owner[0]?owner_address:"",owner[0]?(long)(monotime()-owner_since):0L,face_pack(),face_shown_id(),capture_sequence);respond(fd,200,out);return;
    }
    if(!strcmp(r->method,"GET") && !strcmp(r->path,"/v1/events")) {
        size_t n=(size_t)snprintf(out,sizeof(out),"{\"latest\":%lu,\"events\":[",event_seq);unsigned long first=event_seq>EVENT_COUNT?event_seq-EVENT_COUNT+1:1;
        for(unsigned long seq=first;seq<=event_seq;seq++) {struct event *e=&events[(seq-1)%EVENT_COUNT];n+=(size_t)snprintf(out+n,sizeof(out)-n,"%s{\"sequence\":%lu,\"type\":\"%s\"}",seq==first?"":",",e->seq,e->type);}snprintf(out+n,sizeof(out)-n,"]}\n");respond(fd,200,out);return;
    }
    if(!strcmp(r->method,"GET") && !strcmp(r->path,"/v1/speaker/status")){speaker_status(fd);return;}
    if(!strcmp(r->method,"GET") && !strcmp(r->path,"/v1/media/status")){media_status(fd);return;}
    if(!strcmp(r->method,"GET") && !strcmp(r->path,"/v1/input/status")){input_status(fd);return;}
    if(!strcmp(r->method,"GET") && !strcmp(r->path,"/v1/face/status")){face_status(fd);return;}
    /* Pack readback for CCC: boot, link and pack together. Display state, not authorization. */
    if(!strcmp(r->method,"GET") && !strcmp(r->path,"/v1/face/pack")){char pack[640];(void)face_pack_json(pack,sizeof(pack));
        snprintf(out,sizeof(out),"{\"version\":\"" VERSION "\",\"boot_id\":\"%s\",\"link\":{\"owner\":%s%s%s,\"lease_remaining_seconds\":%.3f,\"face_lease\":%s},\"pack\":%s}\n",
            portal_boot_id(),owner[0]?"\"":"",owner[0]?owner:"null",owner[0]?"\"":"",owner[0]?lease_end-monotime():0,face_link_active()?"true":"false",pack);respond(fd,200,out);return;}
    if(strcmp(r->method,"POST")) {error(fd,404,"unknown endpoint or method");return;}
    if(!strcmp(r->path,"/v1/speaker/play")){
        if(!authorized_owner(fd,r))return;
        if(!r->boot[0]||!r->lease[0]){error(fd,409,"speaker requires boot and lease binding");return;}
        record_request("speaker\n");speaker_play(fd,r->body);return;
    }
    if(!strcmp(r->path,"/v1/speaker/cancel")){
        if(!authorized_owner(fd,r))return;
        struct command c;if(!parse_command(r->body,&c)||c.has_agent||c.has_ttl||c.has_probe){error(fd,400,"expected empty object");return;}
        speaker_cancel();respond(fd,202,"{\"cancel_requested\":true,\"hardware_cleanup_confirmed\":false}\n");return;
    }
    if(!strcmp(r->path,"/v1/media/cancel")){struct command c;if(!parse_command(r->body,&c)||c.has_agent||c.has_ttl||c.has_probe){error(fd,400,"expected empty object");return;}media_cancel_owned(fd,r->agent);return;}
    if(!strcmp(r->path,"/v1/media/prepare")){struct command c;if(!parse_command(r->body,&c)||c.has_agent||c.has_ttl||c.has_probe){error(fd,400,"expected empty object");return;}if(media_prepare_root && !r->boot[0]){error(fd,400,"media preparation requires boot binding");return;}record_request("media-prepare\n");media_prepare(fd,r->agent);return;}
    if(!strcmp(r->path,"/v1/session")) {
        struct command c;if(!parse_command(r->body,&c) || !c.has_agent || c.has_probe || (r->agent[0] && strcmp(r->agent,c.agent))) {error(fd,400,"expected agent, optional ttl 1..120 and optional label");return;}
        if(owner[0] && strcmp(owner,c.agent)) {error(fd,409,"another agent owns the control lease");return;}
        bool fresh=!owner[0];if(fresh && r->lease[0]){error(fd,409,"expired control lease cannot be renewed");return;}if(!fresh && !authorized_owner(fd,r))return;
        if(fresh){struct sockaddr_in peer;socklen_t peer_size=sizeof(peer);owner_address[0]=0;owner_label[0]=0;owner_since=monotime();
            if(!getpeername(fd,(struct sockaddr*)&peer,&peer_size))(void)inet_ntop(AF_INET,&peer.sin_addr,owner_address,sizeof(owner_address));}
        if(c.has_label)strcpy(owner_label,c.label);
        if(fresh){if(new_lease_id()){error(fd,503,"lease identity unavailable");return;}face_active=false;face_grant=0;face_grant_request=0;face_generation=0;input_client_sequence=face_client_sequence=speaker_client_sequence=0;}strcpy(owner,c.agent);if(fresh)record_request("lease\n");lease_end=monotime()+c.ttl;emit(fresh?"session.acquired":"session.renewed");snprintf(out,sizeof(out),"{\"owner\":\"%s\",\"ttl\":%d,\"lease_id\":\"%s\"}\n",owner,c.ttl,lease_id);respond(fd,200,out);return;
    }
    if(!strcmp(r->path,"/v1/audio/stream")) {
        if(!authorized_owner(fd,r))return;
        struct command c;if(!parse_command(r->body,&c) || c.has_agent || c.has_ttl || c.has_probe){error(fd,400,"expected empty object");return;}
        if(!audio_enabled){error(fd,501,"shared audio stream not enabled");return;}
        capture_switch(true);if(capture_sequence&1u){error(fd,409,"physical capture mute is on");return;}
        if(!preview_connected || !preview_commands){error(fd,409,"connected preview event session required");return;}
        int status=audio_start(fd);if(status)error(fd,status,"audio capture busy or unavailable");return;
    }
    if(!strcmp(r->path,"/v1/session/release")) {if(!authorized_owner(fd,r))return;speaker_cancel();input_cancel();face_cancel();audio_cancel();preview_stop();owner[0]=0;record_request("lease\n");emit("session.released");respond(fd,200,"{\"released\":true}\n");return;}
    if(!strcmp(r->path,"/v1/stop")) {media_cancel();speaker_cancel();input_cancel();face_cancel();probe_cancel_all();audio_cancel();preview_stop();owner[0]=0;emit("stop.requested");respond(fd,200,"{\"stopped\":true}\n");return;}
    if(!strcmp(r->path,"/v1/preview/start")) {
        if(!authorized_owner(fd,r))return;
        /* The preparation job itself (cammy-boot, phase initializing) starts the preview to
         * check the stream; refusing it failed every managed preparation (0.7.5). */
        if(media_prepare_root && strcmp(media_phase(),"ready") && !(!strcmp(media_phase(),"initializing") && !strcmp(owner,"cammy-boot"))){error(fd,409,"explicit media preparation required");return;}
        if(!vendor_backend){error(fd,501,"stock preview backend not enabled");return;}if(preview_start(address)){error(fd,503,"stock camera event service unavailable");return;}snprintf(out,sizeof(out),"{\"preview_active\":true,\"rtsp_url\":\"rtsp://%s:8554/ch00\"}\n",address);respond(fd,202,out);return;
    }
    if(!strcmp(r->path,"/v1/preview/check")){if(!authorized_owner(fd,r))return;struct command c;if(!parse_command(r->body,&c) || c.has_agent || c.has_ttl || c.has_probe){error(fd,400,"expected empty object");return;}if(!preview_connected || !preview_commands){error(fd,409,"connected preview event session required");return;}preview_begin_check();preview_status(fd);return;}
    if(!strcmp(r->path,"/v1/preview/stop")) {if(!authorized_owner(fd,r))return;audio_cancel();preview_stop();respond(fd,200,"{\"preview_active\":false}\n");return;}
    if(!strcmp(r->path,"/v1/input")) {
        if(!authorized_owner(fd,r))return;
        if(!input_enabled){error(fd,501,"native input not enabled");return;}
        if(!r->boot[0]){error(fd,400,"native input requires boot binding");return;}
        if(!r->lease[0]){error(fd,409,"native input requires lease binding");return;}
        uint64_t seq;unsigned button,duration;if(!parse_input(r->body,&button,&duration,&seq)){error(fd,400,"expected observed button and tap/hold/release action");return;}
        if(seq<=input_client_sequence){error(fd,409,"stale input sequence");return;}input_client_sequence=seq;
        if(input_send(button,duration)){error(fd,503,"input guardian unavailable");return;}
        snprintf(out,sizeof(out),"{\"queued\":true,\"sequence\":%" PRIu64 ",\"physical_motion_confirmed\":false}\n",input_sequence);respond(fd,202,out);return;
    }
    /* Temporary face pack override. The camera menu owns the saved choice. */
    if(!strcmp(r->path,"/v1/face/pack")) {
        if(!authorized_owner(fd,r))return;
        if(!face_path){error(fd,501,"hardware adapter not implemented");return;}
        if(!r->boot[0]){error(fd,400,"face pack requires boot binding");return;}
        if(!r->lease[0]){error(fd,409,"face pack requires lease binding");return;}
        uint64_t seq;bool cammy;if(!parse_face_pack(r->body,&cammy,&seq)){error(fd,400,"expected pack cammy or child and sequence");return;}
        if(seq<=face_client_sequence){error(fd,409,"stale face sequence");return;}face_client_sequence=seq;
        if(cammy && !face_active){error(fd,409,"show the face before granting the cammy pack");return;}
        face_grant=cammy?FACE_PACK_CAMMY:FACE_PACK_CHILD;face_grant_request=0;
        if(face_active){ /* now, or on the next renewal once a pending face intent is applied */
            struct cfs_status st;if(face_snapshot(&st)){face_grant=0;error(fd,503,"native GUI control unavailable");return;}
            if(st.request>=face_pending && !st.error)(void)face_send(CFS_RENEW,0,false);
            face_renew_at=monotime();
        }
        snprintf(out,sizeof(out),"{\"queued\":true,\"pack\":\"%s\",\"request_id\":%" PRIu64 ",\"gui_applied\":false,\"authorization\":false}\n",cammy?"cammy":"child",face_request);respond(fd,202,out);return;
    }
    if(!strcmp(r->path,"/v1/face")) {
        if(!authorized_owner(fd,r))return;
        if(!face_path){error(fd,501,"hardware adapter not implemented");return;}
        if(!r->boot[0]){error(fd,400,"native face requires boot binding");return;}
        if(!r->lease[0]){error(fd,409,"native face requires lease binding");return;}
        uint64_t seq;unsigned op,expression;if(!parse_face(r->body,&op,&expression,&seq)){error(fd,400,"expected expression and/or show/hide/replay/idle action");return;}
        if(seq<=face_client_sequence){error(fd,409,"stale face sequence");return;}face_client_sequence=seq;
        if(face_send(op,expression,false)){error(fd,503,"native GUI control unavailable");return;}
        face_active=true;face_renew_at=monotime()+0.25;
        snprintf(out,sizeof(out),"{\"queued\":true,\"request_id\":%" PRIu64 ",\"gui_applied\":false,\"physical_display_confirmed\":false}\n",face_request);respond(fd,202,out);return;
    }
    if(!strcmp(r->path,"/v1/look") || !strcmp(r->path,"/v1/speak") || !strcmp(r->path,"/v1/audio/capture")) {if(!authorized_owner(fd,r))return;error(fd,501,"hardware adapter not implemented");return;}
    error(fd,404,"unknown endpoint");
}
/* 0.9: waits are data- and deadline-driven. Sockets and pipes wake poll, child exits
 * wake it through the SIGCHLD self-pipe, and the timeout is the nearest real deadline
 * (at most 1 s). Without the self-pipe, active children keep the old 25 ms tick. */
#define PORTAL_WAKE(t) do{if((t)<until)until=(t);}while(0)
static int portal_wait_ms(void) {
    double now=monotime(), until=now+1.0;
    if(child_wake[0]<0 && (speaker_pid>0 || probe_pid>0 || audio_pid>0))PORTAL_WAKE(now+.025);
    if(owner[0])PORTAL_WAKE(lease_end);
    if(face_active && owner[0])PORTAL_WAKE(face_renew_at);
    for(int i=0;i<CLIENT_COUNT;i++)
        if(clients[i].fd>=0 && !clients[i].streaming)PORTAL_WAKE(clients[i].deadline);
    if(preview_fd>=0){
        if(!preview_connected || preview_tx<sizeof(preview_command)-1)PORTAL_WAKE(preview_event_deadline);
        else PORTAL_WAKE(preview_next_command);
        if(preview_checking){PORTAL_WAKE(preview_check_deadline);if(preview_rtsp_fd<0)PORTAL_WAKE(preview_next_attempt);}
    }
    if(probe_pid && probe_active && !strcmp(probe_active->status,"running"))PORTAL_WAKE(probe_active->started+1.0);
    if(audio_stopping && audio_pid>0)PORTAL_WAKE(audio_kill_at);
    if(audio_fd>=0){
        PORTAL_WAKE(audio_end);PORTAL_WAKE(audio_profile_checked+AUDIO_PROFILE_RECHECK);
        /* The face page's capture switch has no fd: bound a mute's latency while the mic is open. */
        PORTAL_WAKE(now+AUDIO_CAPTURE_SWITCH_POLL);
        if(!audio_started)PORTAL_WAKE(audio_open_end);
        if(audio_pos<audio_used)PORTAL_WAKE(audio_progress+1.0);
    }
    return until<=now?0:(int)((until-now)*1000.0)+1;
}
#undef PORTAL_WAKE
static void usage(const char *name) {fprintf(stderr,"Usage: %s --token-file FILE [--bind IPv4] [--port 8787] [--root DIR] [--stock-preview] [--shared-audio] [--native-face] [--native-input] [--strict-control] [--managed-media] [--speaker-root DIR] [--daemonize] [--pid-file FILE]\n",name);}
int main(int argc,char **argv) {
    if(getenv("CAMMY_VOLUME_FILE"))speaker_volume_file=getenv("CAMMY_VOLUME_FILE"); /* tests */
    if(getenv("CAMMY_FACE_PACK_FILE"))face_pack_file=getenv("CAMMY_FACE_PACK_FILE"); /* tests */
    const char *token_path=NULL,*bind_ip="127.0.0.1",*root=NULL,*pid_path=NULL;int port=8787;bool background=false;
    for(int i=1;i<argc;i++) {if(!strcmp(argv[i],"--stock-preview"))vendor_backend=true;else if(!strcmp(argv[i],"--shared-audio"))audio_enabled=true;else if(!strcmp(argv[i],"--daemonize"))background=true;else if(!strcmp(argv[i],"--version")){puts("cammy-portal " VERSION);return 0;}else if(!strcmp(argv[i],"--strict-control"))strict_control=true;else if(!strcmp(argv[i],"--managed-media"))media_prepare_root="/tmp/cammy-runtime/media-prepare";else if(i+1<argc && !strcmp(argv[i],"--media-prepare-root"))media_prepare_root=argv[++i];else if(i+1<argc && !strcmp(argv[i],"--speaker-root"))speaker_root=argv[++i];else if(!strcmp(argv[i],"--native-input"))input_enabled=true;else if(!strcmp(argv[i],"--native-face"))face_path=CFS_FILE;else if(i+1<argc && !strcmp(argv[i],"--face-control-file"))face_path=argv[++i];else if(i+1<argc && !strcmp(argv[i],"--token-file"))token_path=argv[++i];else if(i+1<argc && !strcmp(argv[i],"--bind"))bind_ip=argv[++i];else if(i+1<argc && !strcmp(argv[i],"--root"))root=argv[++i];else if(i+1<argc && !strcmp(argv[i],"--pid-file"))pid_path=argv[++i];else if(i+1<argc && !strcmp(argv[i],"--port")){char *e;long n=strtol(argv[++i],&e,10);if(*e || n<1 || n>65535){usage(argv[0]);return 2;}port=(int)n;}else{usage(argv[0]);return 2;}}
    if(!token_path){usage(argv[0]);return 2;}FILE *f=fopen(token_path,"r");if(!f){perror("token file");return 2;}char raw[132];size_t n=fread(raw,1,sizeof(raw),f);bool too_long=n==sizeof(raw) || ferror(f);fclose(f);while(n && (raw[n-1]=='\r'||raw[n-1]=='\n'))n--;
    if(too_long || n<32 || n>128){fprintf(stderr,"token must contain 32..128 characters\n");return 2;}memcpy(token,raw,n);token[n]=0;for(size_t i=0;i<n;i++)if(!isalnum((unsigned char)token[i]) && token[i]!='-' && token[i]!='_'){fprintf(stderr,"token must be alphanumeric, '-' or '_'\n");return 2;}
    int listener=socket(AF_INET,SOCK_STREAM,0);if(listener<0){perror("socket");return 1;}int one=1;setsockopt(listener,SOL_SOCKET,SO_REUSEADDR,&one,sizeof(one));struct sockaddr_in a={.sin_family=AF_INET,.sin_port=htons((uint16_t)port)};
    if(inet_pton(AF_INET,bind_ip,&a.sin_addr)!=1 || bind(listener,(struct sockaddr*)&a,sizeof(a)) || listen(listener,8)){perror("bind/listen");close(listener);return 1;}
    if(background){
        pid_t child=fork();if(child<0){perror("fork");return 1;}if(child>0)return 0;
        if(setsid()<0){perror("setsid");return 1;}
        int nullfd=open("/dev/null",O_RDWR);if(nullfd<0){perror("/dev/null");return 1;}
        for(int fd=0;fd<3;fd++)if(dup2(nullfd,fd)<0)return 1;
        if(nullfd>2)close(nullfd);
    }
    if(pid_path){FILE *pid=fopen(pid_path,"w");if(!pid){perror("pid file");return 1;}fprintf(pid,"%ld\n",(long)getpid());if(fclose(pid)){perror("pid close");return 1;}}
    if(root && (chroot(root) || chdir("/"))){perror("chroot");return 1;}
    signal(SIGTERM,stop_signal);signal(SIGINT,stop_signal);signal(SIGPIPE,SIG_IGN);emit("portal.started");
    listener_fd=listener;
    for(int i=0;i<CLIENT_COUNT;i++)clients[i].fd=-1;
    if(fcntl(listener,F_SETFL,fcntl(listener,F_GETFL)|O_NONBLOCK)<0){perror("listener nonblocking");return 1;}
    if(media_prepare_root && strcmp(media_phase(),"unprepared") && strcmp(media_phase(),"ready"))media_cancel();
    if(input_enabled && input_start()){fprintf(stderr,"input guardian failed\n");return 1;}
    if(!child_wake_start())emit("portal.child-wake-unavailable");
    while(running) {
        expire();if(child_reap_due())speaker_tick();probe_tick();preview_tick();capture_switch(false);audio_tick();face_tick();input_tick();
        child_event=false;
        struct pollfd p[CLIENT_COUNT+PORTAL_FIXED_FDS];p[0]=(struct pollfd){listener,POLLIN,0};p[1]=(struct pollfd){preview_fd,preview_poll_events(),0};
        p[2]=(struct pollfd){input_fd,POLLIN,0}; /* guardian replies and exit wake the loop */
        p[3]=(struct pollfd){audio_pos<audio_used?-1:audio_pipe,POLLIN,0};
        p[4]=(struct pollfd){probe_eof?-1:probe_pipe,POLLIN,0};
        p[5]=(struct pollfd){child_wake[0],POLLIN,0};
        p[6]=(struct pollfd){preview_rtsp_fd,preview_rtsp_poll_events(),0};
        for(int i=0;i<CLIENT_COUNT;i++){
            int fd=clients[i].deferred?-1:clients[i].fd;
            short interests=POLLIN;
            if(fd>=0 && fd==audio_fd && audio_pos<audio_used)interests|=POLLOUT;
            p[i+PORTAL_FIXED_FDS]=(struct pollfd){fd,interests,0};
        }
        int rc=poll(p,CLIENT_COUNT+PORTAL_FIXED_FDS,portal_wait_ms());if(rc<0){if(errno==EINTR){child_event=true;continue;}break;}
        if(p[5].revents){child_wake_drain();child_event=true;}
        if(p[1].revents&(POLLIN|POLLHUP|POLLERR|POLLNVAL))preview_event_ready=true;
        for(int i=0;i<CLIENT_COUNT;i++) {
            struct client *c=&clients[i];if(c->fd<0)continue;
            if(c->streaming){
                if(p[i+PORTAL_FIXED_FDS].revents&(POLLIN|POLLHUP|POLLERR|POLLNVAL)){char byte;ssize_t peeked=recv(c->fd,&byte,1,MSG_PEEK|MSG_DONTWAIT);if(peeked>=0 || (errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR))audio_cancel();}
                continue;
            }
            if(c->deferred) {if(monotime()>=c->deadline)probe_cancel_all();continue;}
            int code=1;
            if(monotime()>=c->deadline)code=400;
            else if(p[i+PORTAL_FIXED_FDS].revents) {
                ssize_t received=recv(c->fd,c->buf+c->used,c->capacity-c->used,0);
                if(received>0){size_t old=c->used;c->used+=(size_t)received;c->buf[c->used]=0;code=client_parse(c,old);if(!code){route(c->fd,&c->request);if(c->deferred || c->streaming)code=1;}}
                else if(received==0 || (errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR))code=400;
            }
            if(code!=1){if(code)error(c->fd,code,"malformed or oversized HTTP request");close_client(c->fd);}
        }
        if(p[0].revents&POLLIN) {
            int fd=accept(listener,NULL,NULL);
            if(fd>=0){int slot=-1;for(int i=0;i<CLIENT_COUNT;i++)if(clients[i].fd<0){slot=i;break;}
                if(slot<0 || fcntl(fd,F_SETFL,fcntl(fd,F_GETFL)|O_NONBLOCK)<0)close(fd);
                else if(!(clients[slot].buf=malloc(INITIAL_REQUEST_CAPACITY+1u))) {error(fd,503,"request buffer unavailable");close(fd);}
                else {clients[slot].capacity=INITIAL_REQUEST_CAPACITY;clients[slot].fd=fd;clients[slot].deferred=false;clients[slot].streaming=false;clients[slot].used=0;clients[slot].header_len=0;clients[slot].need=0;clients[slot].buf[0]=0;clients[slot].deadline=monotime()+3;}
            }
        }
    }
    child_event=true;speaker_cancel();
    double speaker_cleanup_until=monotime()+4.0;while(speaker_pid>0&&monotime()<speaker_cleanup_until){speaker_tick();struct timespec delay={0,10000000};nanosleep(&delay,NULL);}
    media_cancel();input_shutdown();face_cancel();probe_cancel_all();probe_tick();audio_shutdown();
    for(int i=0;i<CLIENT_COUNT;i++)if(clients[i].fd>=0){close(clients[i].fd);free(clients[i].buf);}
    preview_stop();close(listener);return 0;
}
