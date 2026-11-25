/* SPDX-License-Identifier: MIT. Finite observed evdev controls, no motor bus. */
#include <sys/wait.h>
#include <sys/sysmacros.h>
#define INPUT_BUTTONS 7
struct input_message {uint64_t sequence,deadline;uint32_t button,pressed;};
struct input_reply {uint64_t sequence;int32_t error,button;};
static const unsigned input_device[INPUT_BUTTONS]={0,0,0,0,1,0,1};
static const unsigned input_code[INPUT_BUTTONS]={115,102,5,6,3,7,139};
static const char *const input_names[INPUT_BUTTONS]={"left","right","zoom_in","zoom_out","turn_180","center","playback_cycle"};
static bool input_enabled;
static int input_fd=-1;
static pid_t input_pid=-1;
static uint64_t input_sequence,input_processed;
static int input_error,input_button=-1;
static volatile sig_atomic_t input_child_alive=1,input_child_channel=-1;
static uint64_t input_write_deadline;
/* Shutting the channel down makes the guardian's next wait return at once, so a
 * stop signal that lands just before an indefinite wait is never missed. */
static void input_child_signal(int sig){
    (void)sig;int saved=errno;input_child_alive=0;
    if(input_child_channel>=0)(void)shutdown((int)input_child_channel,SHUT_RDWR);
    errno=saved;
}
/* A writer callback permits a synthetic pipe sink in local fixtures. */
typedef int (*input_sink)(unsigned,bool,void *);
static int input_device_write(unsigned button,bool pressed,void *ctx) {
    (void)ctx;if(button>=INPUT_BUTTONS)return EINVAL;
    char path[128],name[64],expected[32];unsigned device=input_device[button];
    snprintf(path,sizeof(path),"/sys/class/input/event%u/device/name",device);
    int fd=open(path,O_RDONLY|O_CLOEXEC);if(fd<0)return errno;
    ssize_t n=read(fd,name,sizeof(name)-1);close(fd);if(n<=0)return EIO;name[n]=0;
    while(n>0 && (name[n-1]=='\n'||name[n-1]=='\r'))name[--n]=0;
    snprintf(expected,sizeof(expected),"sunxi-gpadc%u",device);if(strcmp(name,expected))return ENODEV;
    snprintf(path,sizeof(path),"/dev/input/event%u",device);struct stat st;
    if(lstat(path,&st)||!S_ISCHR(st.st_mode)||major(st.st_rdev)!=13||minor(st.st_rdev)!=64+device)return ENODEV;
    fd=open(path,O_WRONLY|O_CLOEXEC|O_NONBLOCK|O_NOFOLLOW);if(fd<0)return errno;
    /* Explicit M105 ARM32 input_event, independent of the host build's time ABI. */
    struct event32 {uint32_t sec,usec;uint16_t type,code;int32_t value;};
    _Static_assert(sizeof(struct event32)==16,"observed evdev event layout");
    const struct event32 packet[2]={{0,0,1,(uint16_t)input_code[button],pressed?1:0},{0,0,0,0,0}};
    if(pressed && (uint64_t)(monotime()*1000.0)>=input_write_deadline){close(fd);return ETIMEDOUT;}
    ssize_t result=write(fd,packet,sizeof(packet));int error=result==(ssize_t)sizeof(packet)?0:EIO;close(fd);return error;
}
/* Event-driven: no key held waits indefinitely for a command, EOF or a stop
 * signal; a held key waits only until its release deadline. */
static void input_guard_loop(int channel,input_sink sink,void *ctx) {
    input_child_channel=channel;
    struct sigaction action={0};action.sa_handler=input_child_signal;sigemptyset(&action.sa_mask);
    (void)sigaction(SIGTERM,&action,NULL);(void)sigaction(SIGINT,&action,NULL);(void)sigaction(SIGHUP,&action,NULL);
    int held=-1;uint64_t until=0,last=0;
    while(input_child_alive){
        uint64_t now=(uint64_t)(monotime()*1000.0);
        if(held>=0 && now>=until){struct input_reply expired={last,sink((unsigned)held,false,ctx),-1};
            if(expired.error)expired.button=-2;else held=-1;
            if(send(channel,&expired,sizeof(expired),MSG_DONTWAIT|MSG_NOSIGNAL)!=(ssize_t)sizeof(expired)||expired.error)break;}
        int wait_ms=held>=0?(int)(until-now):-1; /* until-now is 1..350 here */
        struct pollfd p={channel,POLLIN,0};int rc=poll(&p,1,wait_ms);if(rc<0){if(errno==EINTR)continue;break;}
        if(!rc)continue;
        if(!(p.revents&POLLIN) || (p.revents&(POLLERR|POLLNVAL)))break;
        struct input_message m;ssize_t n=recv(channel,&m,sizeof(m),MSG_DONTWAIT|MSG_TRUNC);
        if(n<=0)break;
        if(n!=(ssize_t)sizeof(m) || m.sequence<=last)continue;
        last=m.sequence;now=(uint64_t)(monotime()*1000.0);
        struct input_reply reply={m.sequence,0,-1};
        if(!m.pressed){if(held>=0)reply.error=sink((unsigned)held,false,ctx);if(!reply.error)held=-1;}
        else if(m.pressed!=1 || m.button>=INPUT_BUTTONS || m.deadline<=now || m.deadline-now>350u){reply.error=EINVAL;}
        else {
            if(held>=0 && held!=(int)m.button){reply.error=sink((unsigned)held,false,ctx);if(!reply.error)held=-1;}
            if(!reply.error && held<0){input_write_deadline=m.deadline;reply.error=sink(m.button,true,ctx);held=(int)m.button;}
            if(!reply.error){until=m.deadline;reply.button=held;}
        }
        if(reply.error && reply.error!=EINVAL)reply.button=-2;
        /* A broken pipe or full channel ends the guardian; the held key is released below. */
        if(send(channel,&reply,sizeof(reply),MSG_DONTWAIT|MSG_NOSIGNAL)!=(ssize_t)sizeof(reply))break;
        if(reply.error && reply.error!=EINVAL)break; /* retain held key for final release attempt */
    }
    if(held>=0)(void)sink((unsigned)held,false,ctx);
    close(channel);_exit(0);
}
static int input_start(void) {
    int fds[2];if(socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC|SOCK_NONBLOCK,0,fds))return -1;
    pid_t pid=fork();if(pid<0){close(fds[0]);close(fds[1]);return -1;}
    if(pid==0){close(fds[0]);if(listener_fd>=0)close(listener_fd);if(preview_fd>=0)close(preview_fd);
        for(int i=0;i<CLIENT_COUNT;i++)if(clients[i].fd>=0)close(clients[i].fd);
        input_guard_loop(fds[1],input_device_write,NULL);}
    close(fds[1]);input_fd=fds[0];input_pid=pid;return 0;
}
static void input_tick(void){
    if(input_pid>0 && child_reap_due() && waitpid(input_pid,NULL,WNOHANG)==input_pid)input_pid=-1;
    if(input_fd<0)return;
    for(unsigned i=0;i<4;i++){struct input_reply r;ssize_t n=recv(input_fd,&r,sizeof(r),MSG_DONTWAIT|MSG_TRUNC);
        if(n<0&&(errno==EAGAIN||errno==EWOULDBLOCK))break;
        if(n!=(ssize_t)sizeof(r)){close(input_fd);input_fd=-1;input_error=EPIPE;break;}
        input_processed=r.sequence;input_error=r.error;input_button=r.button;
    }
}
static int input_send(unsigned button,unsigned milliseconds){
    if(input_fd<0 || input_sequence==UINT64_MAX)return -1;
    struct input_message m={++input_sequence,(uint64_t)(monotime()*1000.0)+milliseconds,button,milliseconds?1u:0u};
    return send(input_fd,&m,sizeof(m),MSG_DONTWAIT|MSG_NOSIGNAL)==(ssize_t)sizeof(m)?0:-1;
}
static void input_cancel(void){if(input_fd>=0)(void)input_send(0,0);}
static void input_shutdown(void){input_cancel();if(input_fd>=0){close(input_fd);input_fd=-1;}if(input_pid>0){
    /* The child gets EOF and releases without relying on this wait. Reap only
     * an already exited child; never block portal shutdown or signal devices. */
    (void)waitpid(input_pid,NULL,WNOHANG);input_pid=-1;}}
static void input_status(int fd){char out[512];input_tick();snprintf(out,sizeof(out),"{\"configured\":%s,\"guardian_connected\":%s,\"last_processed_sequence\":%" PRIu64 ",\"last_error\":%d,\"last_acknowledged_button\":%d,\"hold_deadline_ms\":350,\"physical_motion_confirmed\":false,\"calibrated_pose\":false}\n",input_enabled?"true":"false",input_fd>=0?"true":"false",input_processed,input_error,input_button);respond(fd,200,out);}
