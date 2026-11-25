/* SPDX-License-Identifier: MIT. Finite external STOCK ALSA-route trial.
 * No native memory/API calls. Fixed stock aplay, fork/exec (no shell). Mixer
 * controls go through ALSA control ioctls on /dev/snd/controlC0 by numid, each
 * checked against its stock name (0.9: no amixer fork per control; -DMIXER
 * selects the old amixer exec path, kept for host fixtures).
 * Host independently verifies all routing controls. Legacy baseline: SPK off.
 * Fixed gain trials require LINEOUT zero and encode the observed SPK baseline.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <limits.h>
#include <time.h>
#include <unistd.h>
#include "speaker_stream.h"
#ifndef CAMMY_SPEAKER_PEAK
/* Hard sample cap. Adam 2026-11-16 at LINEOUT 27: peak 6000/9000 clean (normal/loud), 13000 clipped badly. */
#define CAMMY_SPEAKER_PEAK 10000
#endif
/* LINEOUT step: 2..31 = -43.5..0 dB in 1.5 dB steps; 20 (-16.5 dB) is the stock playback level, 27 (-6 dB) is Adam's choice for Cammy (2026-11-16; 31 sounded slightly overblown). */
#ifndef CAMMY_SPEAKER_LINEOUT
#define CAMMY_SPEAKER_LINEOUT 27
#endif
#define CAMMY_LINEOUT_STR2(x) #x
#define CAMMY_LINEOUT_STR(x) CAMMY_LINEOUT_STR2(x)
#ifndef PLAY
#define PLAY "/usr/bin/aplay"
#endif
#ifndef CAMMY_CTL_PATH
#define CAMMY_CTL_PATH "/dev/snd/controlC0"
#endif
#ifndef SYNTHETIC
#define SYNTHETIC 0
#endif
static volatile sig_atomic_t stopped;
static int capture=-1;
static void stop_handler(int sig) { (void)sig; stopped=1; }
static int64_t milliseconds(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC,&ts)) return -1;
    return (int64_t)ts.tv_sec*1000+ts.tv_nsec/1000000;
}
static void pause_short(void) {
    const struct timespec delay={0,10000000}; nanosleep(&delay,NULL);
}
static int run(char *const argv[],int source,int config,int budget,int honor_stop) {
    int64_t now=milliseconds();if(now<0)return -1;
    pid_t parent=getpid(),child=fork();
    if (child<0) return -1;
    if (!child) {
        if (prctl(PR_SET_PDEATHSIG,SIGKILL) || getppid()!=parent) _exit(125);
        if(capture>=0 && dup2(capture,STDOUT_FILENO)<0)_exit(125);
        if (source>=0) {
            int copy=fcntl(source,F_DUPFD_CLOEXEC,10);
            int conf=config>=0?fcntl(config,F_DUPFD_CLOEXEC,10):-1;
            if(copy<0 || (config>=0&&conf<0) || dup2(copy,3)<0 || fcntl(3,F_SETFD,0))_exit(125);
            close(copy);
            if(conf>=0){if(dup2(conf,4)<0||fcntl(4,F_SETFD,0)||setenv("ALSA_CONFIG_PATH","/proc/self/fd/4",1))_exit(125);close(conf);}
            else if(unsetenv("ALSA_CONFIG_PATH"))_exit(125);
        }
        execv(argv[0],argv);_exit(127);
    }
    int status=0,killed=0;int64_t deadline=now+budget;
    for (;;) {
        pid_t result=waitpid(child,&status,WNOHANG);
        if (result==child) return !killed && WIFEXITED(status) && WEXITSTATUS(status)==0 ? 0 : -1;
        if (result<0 && errno!=EINTR) return -1;
        now=milliseconds();
        if (!killed && (now<0 || now>=deadline || (honor_stop && stopped))) {
            /* This sole parent has not reaped the child: PID cannot be reused. */
            kill(child,SIGKILL);killed=1;deadline=now+1000;
        } else if (killed && (now<0 || now>=deadline)) return -2;
        pause_short();
    }
}
#ifdef MIXER
/* Host fixtures: the 0.8 amixer exec path, same calls as before. */
static int mixer_get(unsigned numid,const char *name,char *value,size_t size) {
    char id[16],want[64];snprintf(id,sizeof(id),"numid=%u",numid);snprintf(want,sizeof(want),"name='%s'",name);
    int pipefd[2];if(pipe2(pipefd,O_CLOEXEC|O_NONBLOCK))return -1;
    char *args[]={MIXER,"-c","0","cget",id,NULL};
    capture=pipefd[1];int rc=run(args,-1,-1,1000,1);capture=-1;close(pipefd[1]);
    char raw[2048];ssize_t n=read(pipefd[0],raw,sizeof(raw)-1);close(pipefd[0]);
    if(rc||n<=0||n==(ssize_t)sizeof(raw)-1)return -1;
    raw[n]=0;if(!strstr(raw,want))return -1;
    char *v=strstr(raw,": values=");if(!v)return -1;v+=9;
    size_t len=strcspn(v,"\r\n");if(!len||len>=size)return -1;
    memcpy(value,v,len);value[len]=0;return 0;
}
static int mixer_set(unsigned numid,const char *name,const char *value,int honor_stop) {
    (void)name;char id[16];snprintf(id,sizeof(id),"numid=%u",numid);
    char *args[]={MIXER,"-q","-c","0","cset",id,(char *)value,NULL};
    return run(args,-1,-1,1000,honor_stop);
}
#else
#include <sound/asound.h>
#ifdef CAMMY_CTL_SHIM
#include CAMMY_CTL_SHIM /* host fixture: ctl_open/ctl_ioctl over a state file */
#else
#define ctl_open(path) open(path,O_RDWR|O_CLOEXEC)
/* musl declares ioctl(int,int,...), glibc ioctl(int,unsigned long,...); the kernel reads 32 bits. */
static int ctl_ioctl(int fd,unsigned request,void *arg) {
#ifdef __GLIBC__
    return ioctl(fd,(unsigned long)request,arg);
#else
    return ioctl(fd,(int)request,arg);
#endif
}
#endif
static int ctl_fd=-1;
static struct snd_ctl_elem_value ctl_value;
/* Element numid must carry the stock name and the wanted access; one fd per run. */
static int ctl_find(unsigned numid,const char *name,unsigned access,struct snd_ctl_elem_info *info) {
    if(ctl_fd<0 && (ctl_fd=ctl_open(CAMMY_CTL_PATH))<0)return -1;
    memset(info,0,sizeof(*info));info->id.numid=numid;
    if(ctl_ioctl(ctl_fd,SNDRV_CTL_IOCTL_ELEM_INFO,info)<0)return -1;
    if(info->id.numid!=numid || info->id.iface!=SNDRV_CTL_ELEM_IFACE_MIXER || strncmp((const char *)info->id.name,name,sizeof(info->id.name)) ||
       (info->access&access)!=access || info->count<1 || info->count>128)return -1;
    if(info->type!=SNDRV_CTL_ELEM_TYPE_BOOLEAN && info->type!=SNDRV_CTL_ELEM_TYPE_INTEGER && info->type!=SNDRV_CTL_ELEM_TYPE_ENUMERATED)return -1;
    memset(&ctl_value,0,sizeof(ctl_value));ctl_value.id=info->id;
    return ctl_ioctl(ctl_fd,SNDRV_CTL_IOCTL_ELEM_READ,&ctl_value)<0?-1:0;
}
/* Same text amixer cget prints after ": values=": on/off, integers or enum item
 * indexes, comma-joined per channel. A Stop refuses, as amixer's killed child did. */
static int mixer_get(unsigned numid,const char *name,char *value,size_t size) {
    struct snd_ctl_elem_info info;
    if(stopped || ctl_find(numid,name,SNDRV_CTL_ELEM_ACCESS_READ,&info))return -1;
    size_t used=0;
    for(unsigned i=0;i<info.count;i++){
        int n;
        if(info.type==SNDRV_CTL_ELEM_TYPE_BOOLEAN)n=snprintf(value+used,size-used,"%s%s",i?",":"",ctl_value.value.integer.value[i]?"on":"off");
        else if(info.type==SNDRV_CTL_ELEM_TYPE_INTEGER)n=snprintf(value+used,size-used,"%s%ld",i?",":"",ctl_value.value.integer.value[i]);
        else n=snprintf(value+used,size-used,"%s%u",i?",":"",ctl_value.value.enumerated.item[i]);
        if(n<0 || (size_t)n>=size-used)return -1;
        used+=(size_t)n;
    }
    return 0;
}
/* amixer cset semantics for one value: every channel takes it. Integers must be in range. */
static int mixer_set(unsigned numid,const char *name,const char *value,int honor_stop) {
    struct snd_ctl_elem_info info;
    if((honor_stop && stopped) || ctl_find(numid,name,SNDRV_CTL_ELEM_ACCESS_WRITE,&info))return -1;
    long v;
    if(info.type==SNDRV_CTL_ELEM_TYPE_BOOLEAN){if(!strcmp(value,"on"))v=1;else if(!strcmp(value,"off"))v=0;else return -1;}
    else if(info.type==SNDRV_CTL_ELEM_TYPE_INTEGER){
        char *end;errno=0;v=strtol(value,&end,10);
        if(errno || end==value || *end || v<info.value.integer.min || v>info.value.integer.max)return -1;
    }else return -1;
    for(unsigned i=0;i<info.count;i++)ctl_value.value.integer.value[i]=v;
    return ctl_ioctl(ctl_fd,SNDRV_CTL_IOCTL_ELEM_WRITE,&ctl_value)<0?-1:0;
}
#endif
#ifndef HW_PARAMS
#define HW_PARAMS "/proc/asound/card0/pcm0p/sub0/hw_params"
#endif
/* Automatic mode restores the LINEOUT volume it found; a non-zero stock level is not a fault. */
static char baseline_lineout[4]="0";
static int auto_preflight(int *baseline_on) {
    char value[32],raw[1024];
    int fd=open(HW_PARAMS,O_RDONLY|O_NOFOLLOW|O_CLOEXEC);if(fd<0)return CAMMY_SPEAKER_PCM_FORMAT;
    ssize_t n=read(fd,raw,sizeof(raw)-1);close(fd);if(n<=0||n==(ssize_t)sizeof(raw)-1)return CAMMY_SPEAKER_PCM_FORMAT;raw[n]=0;
    if(!strcmp(raw,"closed\n"))return CAMMY_SPEAKER_PCM_CLOSED;
    const char *params[]={"access: MMAP_INTERLEAVED\n","format: S16_LE\n","channels: 1\n","rate: 16000 (16000/1)\n","period_size: 960\n","buffer_size: 7680\n"};
    for(unsigned i=0;i<sizeof(params)/sizeof(*params);i++)if(!strstr(raw,params[i]))return CAMMY_SPEAKER_PCM_FORMAT;
    const unsigned ids[]={20,22,1};
    const char *names[]={"LINEOUT Switch","LINEOUT Output Select","tx hub mode"};
    const char *values[]={"on","1","1"};
    for(unsigned i=0;i<3;i++){
        if(mixer_get(ids[i],names[i],value,sizeof(value)))return CAMMY_SPEAKER_MIXER_ROUTE;
        if(strcmp(value,values[i]))return i==2u?CAMMY_SPEAKER_HUB_DISABLED:CAMMY_SPEAKER_MIXER_ROUTE;
    }
    if(mixer_get(16,"LINEOUT volume",value,sizeof(value)))return CAMMY_SPEAKER_MIXER_ROUTE;
    /* Any valid level 0..31: the stock app leaves different levels after a boot (10 and 27
     * observed 2026-11-21). Adam accepted playing over a stock sound (2026-11-21). */
    size_t len=strlen(value);
    if(len<1||len>2||strspn(value,"0123456789")!=len||atoi(value)>31)return CAMMY_SPEAKER_MIXER_ROUTE;
    memcpy(baseline_lineout,value,len+1);
    if(mixer_get(21,"SPK Switch",value,sizeof(value)))return CAMMY_SPEAKER_MIXER_ROUTE;
    if(strcmp(value,"off")&&strcmp(value,"on"))return CAMMY_SPEAKER_MIXER_ROUTE;
    *baseline_on=!strcmp(value,"on");return stopped?CAMMY_SPEAKER_REFUSED:0;
}
/* Stream mode: aplay reads a small pipe; this feeder copies the first job file and then
 * any appended chunks (<job>.1, <job>.2, ... link()ed into place by the portal) in order.
 * It closes the stream (speaker_stream.h handshake: an O_EXCL tombstone at the first
 * unplayed slot) after STREAM_IDLE_MS with no next chunk, when the shared total leaves no
 * room for another chunk, or on Stop. A chunk admitted before the tombstone is played. */
#ifndef STREAM_IDLE_MS
#define STREAM_IDLE_MS 1000
#endif
#define STREAM_MAX_BYTES CAMMY_SPEAKER_STREAM_MAX_BYTES
#define STREAM_MAX_CHUNKS CAMMY_SPEAKER_STREAM_MAX_CHUNKS
static int16_t chunk_buf[CAMMY_SPEAKER_CHUNK_MAX_BYTES/2];
static int feed_chunk(int in,int out,long *total){
    struct stat st;
    if(fstat(in,&st) || !S_ISREG(st.st_mode) || st.st_nlink!=1 || st.st_size<CAMMY_SPEAKER_CHUNK_MIN_BYTES || st.st_size>CAMMY_SPEAKER_CHUNK_MAX_BYTES || st.st_size%2)return 3;
    size_t want=(size_t)st.st_size,got=0;
    if(want>sizeof(chunk_buf))return 3;
    while(got<want){size_t left=(want-got)&0x3ffffu;ssize_t n=read(in,(char *)chunk_buf+got,left);if(n<0&&errno==EINTR)continue;if(n<=0)return 3;got+=(size_t)n;}
    for(size_t i=0;i<want/2;i++)if(chunk_buf[i]>CAMMY_SPEAKER_PEAK||chunk_buf[i]<-CAMMY_SPEAKER_PEAK)return 3;
    /* Same exact total as the portal: a job of exactly STREAM_MAX_BYTES plays. */
    if(*total+(long)want>STREAM_MAX_BYTES)return 3;
    size_t sent=0;
    while(sent<want){ssize_t n=write(out,(char *)chunk_buf+sent,want-sent);if(n<0&&errno==EINTR)continue;if(n<=0)return 5;sent+=(size_t)n;}
    *total+=(long)want;return 0;
}
#if SYNTHETIC
/* Test-only: hold the feeder just before it claims slot n (closing.<n>, released by go.<n>). */
static void synthetic_close_gate(int n){
    const char *gate=getenv("CAMMY_SYNTH_CLOSE_GATE");char at[600],go[600];
    if(!gate || snprintf(at,sizeof(at),"%s/closing.%d",gate,n)>=(int)sizeof(at) || snprintf(go,sizeof(go),"%s/go.%d",gate,n)>=(int)sizeof(go))return;
    int f=open(at,O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC|O_NOFOLLOW,0600);if(f>=0)close(f);
    for(int i=0;i<500 && access(go,F_OK);i++)pause_short();
}
#else
#define synthetic_close_gate(n) ((void)(n))
#endif
/* Close slot n: 1 = tombstone created (no append can be admitted any more),
 * 0 = the portal admitted chunk n first, -1 = error. */
static int claim_slot(const char *path){
    int c=open(path,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
    if(c>=0){close(c);return 1;}
    return errno==EEXIST?0:-1;
}
/* The portal unlinks its .tmp name right after link(): wait (bounded) for nlink 1. */
static int settle_links(int f){
    struct stat st;
    for(int i=0;i<100;i++){if(fstat(f,&st))return -1;if(st.st_nlink==1)return 0;pause_short();}
    return -1;
}
static int feed(int first,const char *base,int out){
    signal(SIGPIPE,SIG_IGN);
    long total=0;int rc=feed_chunk(first,out,&total);close(first);
    if(rc)return rc;
    char path[600];
    for(int n=1;n<=STREAM_MAX_CHUNKS;n++){
        if(snprintf(path,sizeof(path),"%s.%d",base,n)>=(int)sizeof(path))return 3;
        int f,waited=0;
        for(;;){
            if(stopped)return claim_slot(path)<0?3:0;  /* Stop drops queued audio by design */
            if((f=open(path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC))>=0)break;
            if(errno!=ENOENT)return 3;
            if(waited>=STREAM_IDLE_MS || STREAM_MAX_BYTES-total<CAMMY_SPEAKER_CHUNK_MIN_BYTES){
                synthetic_close_gate(n);
                int claimed=claim_slot(path);
                if(claimed)return claimed<0?3:0;
                continue;  /* admitted just before the close: play it */
            }
            const struct timespec poll={0,20000000};nanosleep(&poll,NULL);waited+=20;
        }
        if(settle_links(f)){close(f);return 3;}
        rc=feed_chunk(f,out,&total);close(f);unlink(path);
        if(rc)return rc;
    }
    return 0;
}
int main(int argc,char **argv) {
    if((argc!=2 && argc!=3 && argc!=4) || (!SYNTHETIC && geteuid()!=0)) return 2;
    int gain_trial=argc==4;
    int automatic=gain_trial && !strcmp(argv[3],"auto");
    int baseline_on=gain_trial && !strcmp(argv[3],"lineout20-from0-spkon");
    if(gain_trial && !automatic && !baseline_on && strcmp(argv[3],"lineout20-from0"))return 2;
    /* One finite50ms..5s mono16k S16_LE job, regular immutable-open input. */
    int fd=open(argv[1],O_RDONLY|O_NOFOLLOW|O_CLOEXEC);struct stat info;
    if(fd<0 || fstat(fd,&info) || !S_ISREG(info.st_mode) || info.st_size<CAMMY_SPEAKER_CHUNK_MIN_BYTES || info.st_size>CAMMY_SPEAKER_CHUNK_MAX_BYTES || info.st_size%2 || info.st_nlink!=1) {
        if(fd>=0)close(fd);
        return 2;
    }
    int config=-1;
    if(argc>=3){
        config=open(argv[2],O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
        struct stat cs;
        if(config<0 || fstat(config,&cs) || !S_ISREG(cs.st_mode) || cs.st_nlink!=1 || cs.st_uid!=geteuid() || (cs.st_mode&0777)!=0600 || cs.st_size<=0 || cs.st_size>8192){if(config>=0)close(config);close(fd);return 2;}
    }
    struct sigaction action;memset(&action,0,sizeof(action));action.sa_handler=stop_handler;
    sigemptyset(&action.sa_mask);
    if(sigaction(SIGTERM,&action,NULL) || sigaction(SIGINT,&action,NULL) || sigaction(SIGHUP,&action,NULL)) { close(fd);return 2; }
    signal(SIGCHLD,SIG_DFL);
    pid_t parent=getppid();
    if(prctl(PR_SET_PDEATHSIG,SIGTERM) || getppid()!=parent) {close(fd);return 2;}
    if(automatic){
        /* No gain/control mutation before the shared format and baseline agree. */
        int refused=auto_preflight(&baseline_on);
        if(refused){close(fd);if(config>=0)close(config);return refused;}
        int16_t samples[1024];ssize_t nr;
        while((nr=read(fd,samples,sizeof(samples)))>0){
            for(ssize_t i=0;i<nr/(ssize_t)sizeof(*samples);i++)if(samples[i]>CAMMY_SPEAKER_PEAK||samples[i]<-CAMMY_SPEAKER_PEAK){close(fd);close(config);return 3;}
        }
        if(nr<0||lseek(fd,0,SEEK_SET)<0){close(fd);close(config);return 3;}
    }
    int prepared=stopped ? -1 : (gain_trial ? mixer_set(16,"LINEOUT volume",CAMMY_LINEOUT_STR(CAMMY_SPEAKER_LINEOUT),1):0);
    char *play[]={PLAY,"-q","-D",argc>=3?"CammyPlayback":"PlaybackRateDmix","-t","raw","-f","S16_LE","-r","16000","-c","1","/proc/self/fd/3",NULL};
    int budget=(int)(info.st_size/32)+1000;
    int playback=-1,fed=0;
    if(stopped || prepared){close(fd);}
    else if(!automatic){playback=run(play,fd,config,budget,1);close(fd);}
    else{
        int p[2];pid_t feeder=-1;
        if(!pipe2(p,O_CLOEXEC)){
            (void)fcntl(p[1],F_SETPIPE_SZ,4096);
            pid_t self=getpid();feeder=fork();
            if(!feeder){if(prctl(PR_SET_PDEATHSIG,SIGTERM)||getppid()!=self)_exit(125);close(p[0]);_exit(feed(fd,argv[1],p[1]));}
            close(p[1]);
            if(feeder>0)playback=run(play,p[0],config,STREAM_MAX_BYTES/32+STREAM_MAX_CHUNKS*STREAM_IDLE_MS+3000,1);
            close(p[0]);
        }
        close(fd);
        if(feeder>0){int st=0;if(stopped||playback)kill(feeder,SIGTERM);while(waitpid(feeder,&st,0)<0&&errno==EINTR){}fed=WIFEXITED(st)?WEXITSTATUS(st):9;}
        else fed=9;
        if(fed && !stopped)playback=-1;
    }
    if(config>=0)close(config);
    /* Native timer's tinymix index20 is numid21 SPK; restore its baseline. */
    int restored=mixer_set(21,"SPK Switch",baseline_on?"on":"off",0);
    /* Independent of player hooks, setup success, cancellation and SPK restore. */
    int gain_restored=gain_trial ? mixer_set(16,"LINEOUT volume",automatic?baseline_lineout:"0",0):0;
    printf("{\"synthetic\":%s,\"gain_trial\":%s,\"prepare_exit_ok\":%s,\"playback_exit_ok\":%s,\"restore_exit_ok\":%s,\"gain_restore_exit_ok\":%s,\"signal_observed\":%s,\"feeder_exit\":%d,\"hardware_audible\":false}\n",
        SYNTHETIC ? "true":"false",gain_trial ? "true":"false",prepared==0 ? "true":"false",playback==0 ? "true":"false",restored==0 ? "true":"false",gain_restored==0 ? "true":"false",stopped ? "true":"false",fed);
    /* A requested Stop that restored the mixer is a clean outcome, not a cleanup fault. */
    if(stopped && restored==0 && gain_restored==0)return 0;
    return playback==0 && restored==0 && gain_restored==0 ? 0 : 4;
}
