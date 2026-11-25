/* SPDX-License-Identifier: MIT
 * Included by the single-threaded portal. No shell or caller-supplied paths.
 * The prefix exists solely for compiled fixture harnesses; no runtime/API flag.
 */
#include <sys/wait.h>
#include <sys/prctl.h>
#define PROBE_RESULT_CAP 16000
#define PROBE_HISTORY 2
static const char *probe_prefix = "";
static const char *probe_names[] = {"runtime", "network-bindings", "display-layout", "audio-state"};
struct probe_job {
    unsigned long id;
    int kind;
    char agent[49];
    const char *status;
    double started, finished;
    size_t used;
    char result[PROBE_RESULT_CAP];
};
static struct probe_job probe_jobs[PROBE_HISTORY], probe_compat;
static int probe_waiter_fd=-1;
static unsigned long probe_sequence;
static struct probe_job *probe_active;
static pid_t probe_pid;
static int probe_pipe = -1;
static bool probe_eof; /* pipe at EOF before the reap: stop polling it (POLLHUP would spin) */
static void probe_child_close_fds(void);

static void probe_quote(FILE *out, const unsigned char *text, size_t len) {
    fputc('"', out);
    for (size_t i=0; i<len; i++) {
        unsigned char c=text[i];
        if (c=='"' || c=='\\') { fputc('\\',out); fputc(c,out); }
        else if(c<32 || c>=127) fprintf(out,"\\u%04x",(unsigned)c);
        else fputc(c,out);
    }
    fputc('"',out);
}
static FILE *probe_open(const char *path) {
    char full[1024];
    int n=snprintf(full,sizeof(full),"%s%s",probe_prefix,path);
    if(n<0 || (size_t)n>=sizeof(full)) {errno=ENAMETOOLONG;return NULL;}
    return fopen(full,"r");
}
static void probe_sample(FILE *out, const char *path) {
    unsigned char raw[257]; size_t n=0; int err=0;
    FILE *in=probe_open(path);
    if(in) {n=fread(raw,1,sizeof(raw),in);if(ferror(in))err=errno?errno:EIO;fclose(in);}
    else err=errno;
    fprintf(out,"{\"source\":\"%s\",\"available\":%s,\"error_errno\":%d,\"truncated\":%s,\"value\":",path,err?"false":"true",err,n>256?"true":"false");
    if(err) fputs("null",out);else probe_quote(out,raw,n>256?256:n);
    fputc('}',out);
}
static void probe_network(FILE *out) {
    /* Inventory only selected IPv4 listeners. Never contact the service. */
    char raw[8193];size_t n=0;int err=0;FILE *in=probe_open("/proc/net/tcp");
    if(in){n=fread(raw,1,sizeof(raw)-1,in);if(ferror(in))err=errno?errno:EIO;fclose(in);}else err=errno;
    raw[n]=0;
    fprintf(out,"{\"source\":\"/proc/net/tcp\",\"available\":%s,\"error_errno\":%d,\"truncated\":%s,\"listeners\":[",err?"false":"true",err,n==sizeof(raw)-1?"true":"false");
    char *save=NULL,*line=strtok_r(raw,"\n",&save);unsigned count=0;bool limited=false;
    while(line) {
        /* Exclude an incomplete final row when the bounded source is truncated. */
        bool complete=save && (*save || n<sizeof(raw)-1);
        unsigned address,port,state;
        if(complete && sscanf(line," %*u: %8x:%x %*8x:%*x %x",&address,&port,&state)==3 && state==10 && (port==8082 || port==8554 || port==8787 || port==9999)) {
            if(count<16){struct in_addr ip={.s_addr=address};char text[INET_ADDRSTRLEN];
                if(inet_ntop(AF_INET,&ip,text,sizeof(text)))fprintf(out,"%s{\"address\":\"%s\",\"port\":%u}",count++?",":"",text,port);
            }else limited=true;
        }
        line=strtok_r(NULL,"\n",&save);
    }
    fprintf(out,"],\"row_limit_reached\":%s,\"scope\":\"selected IPv4 listeners only; no SDP or frame check\"}",limited?"true":"false");
}
static int probe_collect(int fd, int kind) {
    FILE *out=fdopen(fd,"w");if(!out)return -1;
    if(kind==4){diagnostic_prefix=probe_prefix;diagnostics_json(out);int bad=ferror(out);if(fclose(out))bad=1;return bad?-1:0;}
    fprintf(out,"{\"observed_monotonic_seconds\":%.6f,\"boot_id\":",monotime());
    probe_sample(out,"/proc/sys/kernel/random/boot_id");
    fputs(",\"samples\":[",out);
    const char *paths[5]={NULL};
    switch(kind) {
    case 0:paths[0]="/tmp/cammy-runtime/RELEASE";paths[1]="/tmp/cammy-runtime/state";paths[2]="/proc/uptime";break;
    case 1:probe_network(out);break;
    case 2:paths[0]="/sys/class/graphics/fb0/virtual_size";paths[1]="/sys/class/graphics/fb0/bits_per_pixel";paths[2]="/sys/class/graphics/fb0/stride";break;
    case 3:paths[0]="/proc/asound/cards";paths[1]="/proc/asound/card0/pcm0p/sub0/hw_params";paths[2]="/proc/asound/card0/pcm0c/sub0/hw_params";paths[3]="/proc/asound/card0/pcm0c/sub0/status";break;
    default:fclose(out);return -1;
    }
    for(int i=0;i<5 && paths[i];i++){if(i)fputc(',',out);probe_sample(out,paths[i]);}
    fputs("]}",out);int failed=ferror(out);if(fclose(out))failed=1;return failed?-1:0;
}
static void probe_finish(const char *status) {
    if(!probe_active || strcmp(probe_active->status,"running"))return;
    probe_active->status=status;probe_active->finished=monotime();
    if(strcmp(status,"completed")){probe_active->used=0;probe_active->result[0]=0;if(probe_pid>0)kill(probe_pid,SIGKILL);}
    if(probe_pipe>=0){close(probe_pipe);probe_pipe=-1;}
    if(probe_active==&probe_compat && probe_waiter_fd>=0) {
        if(!strcmp(status,"completed"))respond(probe_waiter_fd,200,probe_active->result);
        else error(probe_waiter_fd,503,"diagnostics cancelled, failed or timed out");
        close_client(probe_waiter_fd);probe_waiter_fd=-1;
    }
    if(probe_active!=&probe_compat)emit(!strcmp(status,"completed")?"probe.completed":!strcmp(status,"timed_out")?"probe.timed_out":!strcmp(status,"cancelled")?"probe.cancelled":"probe.failed");
}
static void probe_tick(void) {
    if(!probe_pid)return;
    int status=0;pid_t done=child_reap_due()?waitpid(probe_pid,&status,WNOHANG):0;
    bool reaped=done==probe_pid || (done<0 && errno==ECHILD);
    /* Never signal a PID after waitpid has released it for reuse. */
    if(reaped)probe_pid=0;
    if(!strcmp(probe_active->status,"running")) {
        for(;;) {
            char bytes[1024];ssize_t n=read(probe_pipe,bytes,sizeof(bytes));
            if(n>0){if(probe_active->used+(size_t)n>=PROBE_RESULT_CAP){probe_finish("failed");break;}memcpy(probe_active->result+probe_active->used,bytes,(size_t)n);probe_active->used+=(size_t)n;probe_active->result[probe_active->used]=0;}
            else if(n<0 && errno==EINTR)continue;
            else if(n<0 && errno!=EAGAIN && errno!=EWOULDBLOCK){probe_finish("failed");break;}
            else{if(!n)probe_eof=true;break;}
        }
    }
    if(reaped) {
        if(!strcmp(probe_active->status,"running"))probe_finish(done>0 && WIFSIGNALED(status) && WTERMSIG(status)==SIGALRM?"timed_out":done>0 && WIFEXITED(status) && WEXITSTATUS(status)==0 && probe_active->used>0?"completed":"failed");
        probe_pid=0;probe_active=NULL;return;
    }
    if(!strcmp(probe_active->status,"running") && monotime()-probe_active->started>=1.0)probe_finish("timed_out");
}
static void probe_cancel_all(void) {if(probe_active)probe_finish("cancelled");}
static void probe_catalogue(int fd) {
    char out[1500];size_t n=(size_t)snprintf(out,sizeof(out),"{\"supervisor\":\"%s\",\"history_limit\":2,\"result_limit_bytes\":%d,\"probes\":[",probe_pid?(strcmp(probe_active->status,"running")?"degraded":"busy"):"ready",PROBE_RESULT_CAP-1);
    for(size_t i=0;i<sizeof(probe_names)/sizeof(probe_names[0]);i++)n+=(size_t)snprintf(out+n,sizeof(out)-n,"%s{\"id\":\"%s\",\"available\":true,\"effect\":\"read-only metadata\",\"deadline_seconds\":1,\"requires_lease\":false}",i?",":"",probe_names[i]);
    snprintf(out+n,sizeof(out)-n,"]}");respond(fd,200,out);
}
static void probe_reply(int fd, int code, const struct probe_job *job) {
    char out[PROBE_RESULT_CAP+600];
    snprintf(out,sizeof(out),"{\"job_id\":\"job-%lu\",\"probe_id\":\"%s\",\"agent\":\"%s\",\"status\":\"%s\",\"read_only\":true,\"started_monotonic_seconds\":%.6f,\"finished_monotonic_seconds\":%.6f,\"result\":%s}",job->id,probe_names[job->kind],job->agent,job->status,job->started,job->finished,job->used && !strcmp(job->status,"completed")?job->result:"null");
    respond(fd,code,out);
}
static void probe_spawn(int fd, int kind, const char *agent) {
    child_event=true;probe_tick(); /* a request: reap now, not on the next SIGCHLD */
    if(probe_pid){error(fd,409,"probe worker busy or awaiting reap; no replacement started");return;}
    if(probe_sequence==ULONG_MAX){error(fd,503,"probe job sequence exhausted");return;}
    int pipefd[2];if(pipe(pipefd)){error(fd,503,"probe pipe unavailable");return;}
    int flags=fcntl(pipefd[0],F_GETFL);if(flags<0 || fcntl(pipefd[0],F_SETFL,flags|O_NONBLOCK)<0){close(pipefd[0]);close(pipefd[1]);error(fd,503,"probe pipe unavailable");return;}
    pid_t parent=getpid();
    pid_t pid=fork();
    if(pid<0){close(pipefd[0]);close(pipefd[1]);error(fd,503,"probe worker unavailable");return;}
    if(!pid){if(prctl(PR_SET_PDEATHSIG,SIGKILL)<0 || getppid()!=parent)_exit(1);signal(SIGTERM,SIG_DFL);signal(SIGINT,SIG_DFL);signal(SIGALRM,SIG_DFL);alarm(1);close(pipefd[0]);probe_child_close_fds();int result=probe_collect(pipefd[1],kind);_exit(result?1:0);}
    close(pipefd[1]);probe_pipe=pipefd[0];probe_pid=pid;probe_eof=false;
    unsigned long id=kind==4?0:++probe_sequence;probe_active=kind==4?&probe_compat:&probe_jobs[(id-1)%PROBE_HISTORY];memset(probe_active,0,sizeof(*probe_active));
    probe_active->id=id;probe_active->kind=kind;strcpy(probe_active->agent,agent);probe_active->status="running";probe_active->started=monotime();if(kind!=4)emit("probe.started");
    if(kind==4){probe_waiter_fd=fd;for(int i=0;i<CLIENT_COUNT;i++)if(clients[i].fd==fd)clients[i].deferred=true;}
    else probe_reply(fd,202,probe_active);
}
static void probe_start_job(int fd,int kind,const char *agent) {probe_spawn(fd,kind,agent);}
static void probe_start_compat(int fd) {probe_spawn(fd,4,"legacy-diagnostics");}
static struct probe_job *probe_find(const char *text, bool *cancel) {
    *cancel=false;if(strncmp(text,"job-",4) || text[4]<'1' || text[4]>'9')return NULL;
    errno=0;char *end;unsigned long id=strtoul(text+4,&end,10);
    if(errno || (*end && strcmp(end,"/cancel")))return NULL;
    *cancel=*end!=0;
    for(int i=0;i<PROBE_HISTORY;i++)if(probe_jobs[i].id==id)return &probe_jobs[i];
    return NULL;
}
