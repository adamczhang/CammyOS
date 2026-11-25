/* SPDX-License-Identifier: MIT
 * Opt-in shared ALSA PCM stream. Parent owns/reaps the only capture child;
 * all pipe/socket I/O is nonblocking and bounded. No shell or device fallback.
 */
#include <dirent.h>
#ifndef CAMMY_AUDIO_EXEC
#define CAMMY_AUDIO_EXEC "/usr/bin/arecord"
#endif
#ifndef CAMMY_AUDIO_LIMIT
#define CAMMY_AUDIO_LIMIT 600
#endif
/* 0.9.8: Cammy's SDIO Wi-Fi (eswin) queues 0.3-1.7 s under 1.5 Mbit/s of preview video
 * and mic (14 ms with no streams, 2026-11-23). With the default send buffer and a 1 s
 * slow-reader limit the mic was cut every 7-60 s in conversation and the words being
 * spoken were lost. A deep send buffer (about 8 s of PCM) rides out the spikes, the
 * limit allows 5 s without progress, and the socket asks for the WMM voice queue. */
#ifndef CAMMY_AUDIO_SLOW_S
#define CAMMY_AUDIO_SLOW_S 5
#endif
#define CAMMY_AUDIO_SNDBUF (256*1024)
static bool audio_enabled, audio_started, audio_stopping, audio_fault;
static pid_t audio_pid;
static int audio_pipe=-1, audio_fd=-1;
static double audio_end, audio_open_end, audio_progress, audio_kill_at;
static size_t audio_used, audio_pos;
static unsigned long long audio_bytes;
static char audio_buffer[8192];
static const char *audio_reason="idle";
/* 0.9: the streaming tick re-reads the profile marker at most once a second (it was
 * open/fstat/read/close every 25 ms); a start or status request reads it fresh. */
#ifndef AUDIO_PROFILE_RECHECK
#define AUDIO_PROFILE_RECHECK 1.0
#endif
#ifndef AUDIO_CAPTURE_SWITCH_POLL
#define AUDIO_CAPTURE_SWITCH_POLL .25
#endif
static double audio_profile_checked=-1e9;
static bool audio_profile_cached;
static bool audio_profile_read(void) {
#ifdef CAMMY_AUDIO_TEST
    return true;
#else
    const char expected[]="m105-shared-v1\n";char value[sizeof(expected)];struct stat st;
    int fd=open("/tmp/cammy-runtime/shared-capture.active",O_RDONLY|O_NONBLOCK|O_CLOEXEC|O_NOFOLLOW);
    if(fd<0)return false;
    bool okay=fstat(fd,&st)==0 && S_ISREG(st.st_mode) && st.st_uid==0 &&
        (st.st_mode&0777)==0600 && st.st_nlink==1 && st.st_size==(off_t)(sizeof(expected)-1);
    ssize_t n=okay?read(fd,value,sizeof(value)):-1;close(fd);
    return n==(ssize_t)(sizeof(expected)-1) && !memcmp(value,expected,sizeof(expected)-1);
#endif
}
static bool audio_profile_ready(bool fresh) {
    double now=monotime();
    if(fresh || now<audio_profile_checked || now-audio_profile_checked>=AUDIO_PROFILE_RECHECK){
        audio_profile_cached=audio_profile_read();audio_profile_checked=now;
    }
    return audio_profile_cached;
}

static void audio_detach(void) {
    int fd=audio_fd;audio_fd=-1;
    for(int i=0;i<CLIENT_COUNT;i++)if(clients[i].fd==fd && fd>=0){
        close(fd);clients[i].fd=-1;clients[i].streaming=false;clients[i].deferred=false;
    }
}
static void audio_cancel(void) {
    if(audio_fd<0 && audio_pipe<0 && audio_pid<=0)return;
    audio_detach();
    if(audio_pipe>=0){close(audio_pipe);audio_pipe=-1;}
    audio_used=audio_pos=0;
    if(audio_pid>0 && !audio_stopping){
        (void)kill(audio_pid,SIGTERM);audio_kill_at=monotime()+.25;audio_stopping=true;
    }
    emit("audio.stop-requested");
}
static void audio_client_closed(int fd) {if(fd==audio_fd)audio_cancel();}
static void audio_failed(const char *why) {
    audio_reason=why;audio_fault=true;
    if(audio_fd>=0 && !audio_started)error(audio_fd,503,"shared capture could not start");
    audio_cancel();
}
static void audio_child_fds(void) {
    DIR *directory=opendir("/proc/self/fd");
    if(!directory)_exit(126);
    int own=dirfd(directory);struct dirent *entry;
    while((entry=readdir(directory))){
        char *end;long fd=strtol(entry->d_name,&end,10);
        if(!*end && fd>=3 && fd<=INT_MAX && fd!=own)close((int)fd);
    }
    closedir(directory);
}
static int audio_start(int fd) {
    if(!audio_profile_ready(true))return 503;
    if(!strcmp(audio_reason,"reap-uncertain"))return 503;
    if(audio_pid>0 || audio_pipe>=0 || audio_fd>=0)return 409;
    int p[2];if(pipe(p))return 503;
    if(fcntl(p[0],F_SETFL,O_NONBLOCK)<0){close(p[0]);close(p[1]);return 503;}
    pid_t parent=getpid(), child=fork();
    if(child<0){close(p[0]);close(p[1]);return 503;}
    if(!child){
        if(prctl(PR_SET_PDEATHSIG,SIGKILL) || getppid()!=parent)_exit(126);
        if(dup2(p[1],STDOUT_FILENO)<0)_exit(126);
        int nullfd=open("/dev/null",O_RDWR);
        if(nullfd<0 || dup2(nullfd,STDIN_FILENO)<0 || dup2(nullfd,STDERR_FILENO)<0)_exit(126);
        audio_child_fds();
        char duration[24];snprintf(duration,sizeof(duration),"%d",CAMMY_AUDIO_LIMIT);
        execl(CAMMY_AUDIO_EXEC,"arecord","-q","-D","cammy_shared","-t","raw",
              "-f","S16_LE","-r","16000","-c","1","-d",duration,(char*)NULL);
        _exit(127);
    }
    close(p[1]);audio_pid=child;audio_pipe=p[0];audio_fd=fd;
    {int v=CAMMY_AUDIO_SNDBUF;(void)setsockopt(fd,SOL_SOCKET,SO_SNDBUF,&v,sizeof(v));
     v=0xb8;(void)setsockopt(fd,IPPROTO_IP,IP_TOS,&v,sizeof(v));     /* DSCP EF */
     v=6;(void)setsockopt(fd,SOL_SOCKET,SO_PRIORITY,&v,sizeof(v));}  /* WMM voice */
    audio_started=audio_stopping=audio_fault=false;audio_used=audio_pos=0;audio_bytes=0;
    audio_reason="starting";audio_progress=monotime();audio_open_end=audio_progress+2;
    audio_end=audio_progress+(double)CAMMY_AUDIO_LIMIT;
    for(int i=0;i<CLIENT_COUNT;i++)if(clients[i].fd==fd)clients[i].streaming=true;
    emit("audio.start-requested");return 0;
}
/* Send buffered bytes: 0 drained, 1 still pending, -1 stream ended. */
static int audio_flush(double now) {
    if(audio_used<=audio_pos)return 0;
    ssize_t n=send(audio_fd,audio_buffer+audio_pos,audio_used-audio_pos,MSG_NOSIGNAL|MSG_DONTWAIT);
    if(n>0){audio_pos+=(size_t)n;audio_progress=now;}
    else if(n<0 && errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR){audio_reason="client-lost";audio_cancel();return -1;}
    if(now-audio_progress>CAMMY_AUDIO_SLOW_S){audio_failed("slow-reader");return -1;}
    if(audio_pos<audio_used)return 1;
    audio_pos=audio_used=0;return 0;
}
static void audio_tick(void) {
    double now=monotime();
    if(audio_pid>0 && child_reap_due()){
        int status=0;pid_t done=waitpid(audio_pid,&status,WNOHANG);
        if(done==audio_pid){
            audio_pid=0;
            if(!audio_stopping && (!WIFEXITED(status) || WEXITSTATUS(status)!=0)){
                audio_failed("capture-exited");return;
            }
        }else if(done<0 && errno!=EINTR){audio_pid=0;audio_failed("reap-uncertain");return;}
    }
    if(audio_stopping){
        if(audio_pid>0 && now>=audio_kill_at){(void)kill(audio_pid,SIGKILL);audio_kill_at=now+.25;}
        if(!audio_pid){audio_stopping=false;audio_reason=audio_fault?audio_reason:"stopped";}
        return;
    }
    if(audio_fd<0)return;
    if(!audio_profile_ready(false)){audio_reason="profile-lost";audio_cancel();return;}
    if(!preview_connected || preview_fd<0){audio_reason="preview-lost";audio_cancel();return;}
    if(!owner[0] || now>=audio_end){audio_reason="session-ended";audio_cancel();return;}
    if(audio_flush(now))return;
    if(audio_pipe>=0){
        char data[4096];ssize_t n=read(audio_pipe,data,sizeof(data));
        if(n>0){
            if(!audio_started){
                const char *headers="HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nX-Cammy-Audio-Format: S16_LE\r\nX-Cammy-Audio-Rate: 16000\r\nX-Cammy-Audio-Channels: 1\r\n\r\n";
                audio_used=strlen(headers);memcpy(audio_buffer,headers,audio_used);
                audio_started=true;audio_reason="streaming";emit("audio.streaming");
            }
            memcpy(audio_buffer+audio_used,data,(size_t)n);audio_used+=(size_t)n;
            audio_bytes+=(unsigned long long)n;audio_progress=now;
            if(audio_flush(now)<0)return; /* 0.9: same wake as the read, not the next tick */
        }else if(n==0){
            close(audio_pipe);audio_pipe=-1;
            if(!audio_started){audio_failed("no-pcm");return;}
            audio_detach();audio_reason="source-ended";
        }else if(errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR){audio_failed("pipe-error");return;}
    }
    if(!audio_started && now>=audio_open_end)audio_failed("open-timeout");
}
static void audio_status(int fd) {
    char b[640];snprintf(b,sizeof(b),"{\"configured\":%s,\"profile_ready\":%s,\"phase\":\"%s\",\"capture_child_alive\":%s,\"cleanup_pending\":%s,\"pcm_bytes\":%llu,\"hardware_cleanup_confirmed\":false,\"native_preview_may_acquire_microphone\":true}\n",
        audio_enabled?"true":"false",audio_profile_ready(true)?"true":"false",audio_reason,audio_pid>0?"true":"false",audio_stopping?"true":"false",audio_bytes);
    respond(fd,200,b);
}
static void audio_shutdown(void) {
    audio_cancel();double until=monotime()+.8;
    while(audio_pid>0 && monotime()<until){audio_tick();struct timespec delay={0,10000000};nanosleep(&delay,NULL);}
    if(audio_pid>0)(void)kill(audio_pid,SIGKILL);
}
