/* SPDX-License-Identifier: MIT. Opt-in finite PCM jobs; no shell or network TTS. */
#include <sys/prctl.h>
#include <sys/wait.h>
#include "../native/speaker_probe/speaker_stream.h"
static const char *speaker_root;
static pid_t speaker_pid=-1;
static uint64_t speaker_client_sequence,speaker_job_sequence;
static bool speaker_cancelled,speaker_fault;
#ifndef CAMMY_SPEAKER_PEAK
/* Hard sample cap. Adam 2026-11-16 at LINEOUT 27: peak 6000/9000 clean (normal/loud), 13000 clipped badly. */
#define CAMMY_SPEAKER_PEAK 10000
#endif
static const char *speaker_phase="idle";
static const char *speaker_reason="none";
static char speaker_pcm[512];
static int speaker_stream_chunks;
static long speaker_stream_bytes;  /* first chunk plus appends; same total as the guardian */
#define SPEAKER_STREAM_QUEUE 2
static int speaker_chunk_path(char *out,size_t cap,int n,const char *suffix){
    int k=snprintf(out,cap,"%s.%d%s",speaker_pcm,n,suffix);
    return k>0&&(size_t)k<cap?0:-1;
}

static int speaker_path(char *out,size_t cap,const char *name){
    int n=snprintf(out,cap,"%s/%s",speaker_root?speaker_root:"",name);
    return n>0&&(size_t)n<cap?0:-1;
}
static bool speaker_available(void){
    if(!speaker_root||speaker_root[0]!='/')return false;
    struct stat st;char path[512];
    if(lstat(speaker_root,&st)||!S_ISDIR(st.st_mode)||st.st_uid!=geteuid()||(st.st_mode&0777)!=0700)return false;
    const char *names[]={"speaker-guardian","speaker.conf"};
    for(unsigned i=0;i<2;i++){
        if(speaker_path(path,sizeof(path),names[i])||lstat(path,&st)||!S_ISREG(st.st_mode)||st.st_nlink!=1||st.st_uid!=geteuid()||(st.st_mode&0777)!=(i?0600:0700))return false;
    }
    return true;
}
static void speaker_mark_fault(void){
    speaker_fault=true;speaker_phase="cleanup-uncertain";speaker_reason="cleanup-uncertain";
    char path[512];if(speaker_path(path,sizeof(path),"speaker.fault"))return;
    int f=open(path,O_CREAT|O_EXCL|O_WRONLY|O_CLOEXEC|O_NOFOLLOW,0600);
    if(f>=0){ssize_t n=write(f,"cleanup-uncertain\n",18);(void)n;close(f);}
}
static void speaker_tick(void){
    if(speaker_pid<=0)return;
    int status;pid_t result=waitpid(speaker_pid,&status,WNOHANG);
    if(!result || (result<0&&errno==EINTR))return;
    speaker_pid=-1;
    int code=result>0&&WIFEXITED(status)?WEXITSTATUS(status):-1;
    switch(code){
        case CAMMY_SPEAKER_REFUSED: speaker_reason="preflight-refused";break;
        case CAMMY_SPEAKER_PCM_CLOSED: speaker_reason="stock-pcm-closed";break;
        case CAMMY_SPEAKER_PCM_FORMAT: speaker_reason="stock-pcm-format";break;
        case CAMMY_SPEAKER_MIXER_ROUTE: speaker_reason="stock-mixer-route";break;
        case CAMMY_SPEAKER_HUB_DISABLED: speaker_reason="stock-hub-disabled";break;
        case 0: speaker_reason="none";break;
        default: speaker_mark_fault();break;
    }
    if(!speaker_fault)speaker_phase=speaker_cancelled?"cancelled":(code==0?"finished":"preflight-refused");
    if(speaker_pcm[0]){
        char chunk[560];
        /* Slot chunks+1 may hold the feeder's close tombstone; it stays until this reap. */
        for(int n=1;n<=speaker_stream_chunks+1&&n<=CAMMY_SPEAKER_STREAM_MAX_CHUNKS;n++){
            if(!speaker_chunk_path(chunk,sizeof(chunk),n,""))unlink(chunk);
            if(!speaker_chunk_path(chunk,sizeof(chunk),n,".tmp"))unlink(chunk);
        }
        unlink(speaker_pcm);speaker_pcm[0]=0;
    }
    speaker_stream_chunks=0;speaker_stream_bytes=0;
}
static void speaker_cancel(void){
    speaker_tick();if(speaker_pid<=0)return;
    /* Sole unreaped child: PID cannot be reused. Guardian owns bounded cleanup. */
    speaker_cancelled=true;speaker_phase="cancelling";(void)kill(speaker_pid,SIGTERM);
}
static void speaker_status(int fd){
    speaker_tick();char out[512];
    char fault[512];if(speaker_root&&!speaker_path(fault,sizeof(fault),"speaker.fault")&&access(fault,F_OK)==0){speaker_fault=true;speaker_phase="cleanup-uncertain";speaker_reason="cleanup-uncertain";}
    snprintf(out,sizeof(out),"{\"configured\":%s,\"phase\":\"%s\",\"reason\":\"%s\",\"sequence\":%" PRIu64 ",\"active\":%s,\"hardware_audible\":false,\"hardware_cleanup_confirmed\":false,\"format\":\"S16_LE\",\"sample_rate\":16000,\"channels\":1,\"max_seconds\":5,\"stream_max_seconds\":%d,\"peak_limit\":%d}\n",speaker_available()?"true":"false",speaker_phase,speaker_reason,speaker_job_sequence,speaker_pid>0?"true":"false",CAMMY_SPEAKER_STREAM_MAX_SECONDS,CAMMY_SPEAKER_PEAK);respond(fd,200,out);
}
static int speaker_b64_value(unsigned char c){
    if(c>='A'&&c<='Z')return c-'A';
    if(c>='a'&&c<='z')return c-'a'+26;
    if(c>='0'&&c<='9')return c-'0'+52;
    if(c=='+')return 62;
    if(c=='/')return 63;
    return -1;
}
static int speaker_decode(const char *text,size_t n,unsigned char *pcm,size_t *length){
    size_t used=0;if(!n||n%4||n>213336)return -1;
    for(size_t i=0;i<n;i+=4){
        int a=speaker_b64_value((unsigned char)text[i]),b=speaker_b64_value((unsigned char)text[i+1]);
        int c=text[i+2]=='='?0:speaker_b64_value((unsigned char)text[i+2]);
        int d=text[i+3]=='='?0:speaker_b64_value((unsigned char)text[i+3]);
        bool pad2=text[i+2]=='=',pad1=text[i+3]=='=';
        if(a<0||b<0||c<0||d<0||(pad2&&!pad1)||((pad1||pad2)&&i+4!=n)||(pad2&&(b&15))||(pad1&&!pad2&&(c&3)))return -1;
        unsigned value=((unsigned)a<<18)|((unsigned)b<<12)|((unsigned)c<<6)|(unsigned)d;
        unsigned count=pad2?1u:(pad1?2u:3u);if(used+count>160000)return -1;
        pcm[used++]=(unsigned char)(value>>16);if(count>1)pcm[used++]=(unsigned char)(value>>8);if(count>2)pcm[used++]=(unsigned char)value;
    }
    if(used<1600||used%2)return -1;
    for(size_t i=0;i<used;i+=2){unsigned v=(unsigned)pcm[i]|((unsigned)pcm[i+1]<<8);int sample=v>=32768?(int)v-65536:(int)v;if(sample>CAMMY_SPEAKER_PEAK||sample< -CAMMY_SPEAKER_PEAK)return -1;}
    *length=used;return 0;
}
static int speaker_parse(const char *p,unsigned char *pcm,size_t *length,uint64_t *sequence,bool *append){
    const char *encoded=NULL;size_t encoded_len=0;bool have_append=false;*sequence=0;*append=false;ws(&p);if(*p++!='{')return -1;ws(&p);
    for(;;){char key[32];if(!string_value(&p,key,sizeof(key)))return -1;ws(&p);if(*p++!=':')return -1;ws(&p);
        if(!strcmp(key,"sequence")){if(*sequence||!control_sequence(&p,sequence))return -1;}
        else if(!strcmp(key,"pcm_s16le_mono16k")){
            if(encoded || *p++!='"')return -1;
            encoded=p;p=strchr(p,'"');if(!p)return -1;
            encoded_len=(size_t)(p-encoded);p++;
        }
        else if(!strcmp(key,"append")){if(have_append)return -1;if(!strncmp(p,"true",4)){*append=true;p+=4;}else if(!strncmp(p,"false",5))p+=5;else return -1;have_append=true;}
        else return -1;
        ws(&p);if(*p=='}'){p++;ws(&p);break;}if(*p++!=',')return -1;ws(&p);
    }
    return *p||!*sequence||!encoded?-1:speaker_decode(encoded,encoded_len,pcm,length);
}
/* Menu volume (cammy-ui writes 0..100 to /etc/cammy/volume): scales speaker PCM down,
 * never up, with a squared curve so the steps sound even. Absent file: unchanged. */
static const char *speaker_volume_file="/etc/cammy/volume";
static int speaker_volume(void){char t[8]={0};int f=open(speaker_volume_file,O_RDONLY|O_CLOEXEC);if(f<0)return 100;
    ssize_t n=read(f,t,sizeof(t)-1);close(f);if(n<=0)return 100;long v=strtol(t,NULL,10);return v<0?0:v>100?100:(int)v;}
static void speaker_scale(unsigned char *pcm,size_t length){int v=speaker_volume();if(v>=100)return;int32_t gain=v*v;
    for(size_t i=0;i+1<length;i+=2){int32_t s=(int16_t)(uint16_t)(pcm[i]|pcm[i+1]<<8);int32_t o=s*gain/10000;pcm[i]=(unsigned char)(o&255);pcm[i+1]=(unsigned char)((uint32_t)o>>8&255);}}
static void speaker_play(int fd,const char *body){
    speaker_tick();if(!speaker_available()){error(fd,501,"speaker runtime not configured");return;}
    char fault[512];if(speaker_path(fault,sizeof(fault),"speaker.fault")){error(fd,503,"speaker path unavailable");return;}
    if(speaker_fault||access(fault,F_OK)==0){speaker_phase="cleanup-uncertain";error(fd,503,"speaker cleanup requires recovery");return;}
    static unsigned char pcm[160000];size_t length;uint64_t sequence;bool append;
    if(speaker_parse(body,pcm,&length,&sequence,&append)){error(fd,400,"expected sequence and bounded base64 mono16k S16 PCM within peak_limit");return;}
    speaker_scale(pcm,length);
    if(sequence<=speaker_client_sequence){error(fd,409,"stale speaker sequence");return;}
    if(lease_end-monotime()<(double)length/32000.0+12.0){error(fd,409,"renew lease before speaker playback");return;}
    if(speaker_pid>0){
        /* Stream append (opt-in "append":true, newer sequence, same lease): chunk N, which the
         * guardian's feeder plays gaplessly after the earlier chunks. 202 only after link()
         * wins slot N against the feeder's close tombstone (speaker_stream.h); EEXIST is a
         * clean 409 "speaker stream closed" with nothing queued. */
        if(!append||speaker_cancelled||!speaker_pcm[0]){error(fd,409,"speaker job already active");return;}
        if(speaker_stream_chunks>=CAMMY_SPEAKER_STREAM_MAX_CHUNKS||speaker_stream_bytes+(long)length>CAMMY_SPEAKER_STREAM_MAX_BYTES){error(fd,409,"speaker stream limit");return;}
        char older[560],tmp[560],final[560];int n=speaker_stream_chunks+1;
        if(n>SPEAKER_STREAM_QUEUE&&!speaker_chunk_path(older,sizeof(older),n-SPEAKER_STREAM_QUEUE,"")&&access(older,F_OK)==0){error(fd,409,"speaker queue full");return;}
        if(speaker_chunk_path(tmp,sizeof(tmp),n,".tmp")||speaker_chunk_path(final,sizeof(final),n,"")){error(fd,503,"speaker path unavailable");return;}
        int f=open(tmp,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
        if(f<0){error(fd,503,"speaker job residue requires recovery");return;}
        bool ok=true;size_t sent=0;while(sent<length){ssize_t w=write(f,pcm+sent,length-sent);if(w<0&&errno==EINTR)continue;if(w<=0){ok=false;break;}sent+=(size_t)w;}
        if(close(f))ok=false;
        if(!ok){unlink(tmp);error(fd,503,"speaker payload write failed");return;}
        if(link(tmp,final)){int e=errno;unlink(tmp);
            if(e==EEXIST)error(fd,409,"speaker stream closed");else error(fd,503,"speaker payload write failed");
            return;}
        unlink(tmp);  /* the feeder waits for nlink 1 before reading */
        speaker_stream_chunks=n;speaker_stream_bytes+=(long)length;speaker_client_sequence=speaker_job_sequence=sequence;emit("speaker.appended");
        respond(fd,202,"{\"queued\":true,\"appended\":true,\"hardware_audible\":false}\n");
        return;
    }
    char player[512],config[512];
    if(speaker_path(player,sizeof(player),"speaker-guardian")||speaker_path(config,sizeof(config),"speaker.conf")||speaker_path(speaker_pcm,sizeof(speaker_pcm),"speaker.pcm")){error(fd,503,"speaker path unavailable");return;}
    /* A leftover chunk or close tombstone would join (or close) the new job. */
    char residue[560];struct stat rs;
    if(speaker_chunk_path(residue,sizeof(residue),1,"")||lstat(residue,&rs)==0||errno!=ENOENT){speaker_pcm[0]=0;error(fd,503,"speaker job residue requires recovery");return;}
    int f=open(speaker_pcm,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
    if(f<0){speaker_pcm[0]=0;error(fd,503,"speaker job residue requires recovery");return;}
    bool ok=true;size_t sent=0;while(sent<length){ssize_t n=write(f,pcm+sent,length-sent);if(n<0&&errno==EINTR)continue;if(n<=0){ok=false;break;}sent+=(size_t)n;}
    if(close(f))ok=false;
    if(!ok){unlink(speaker_pcm);speaker_pcm[0]=0;error(fd,503,"speaker payload write failed");return;}
    pid_t parent=getpid(),child=fork();
    if(child<0){unlink(speaker_pcm);speaker_pcm[0]=0;error(fd,503,"speaker guardian unavailable");return;}
    if(!child){
        if(prctl(PR_SET_PDEATHSIG,SIGTERM)||getppid()!=parent)_exit(125);
        probe_child_close_fds();
        int nullfd=open("/dev/null",O_RDWR);if(nullfd<0)_exit(125);
        for(int i=0;i<3;i++)if(dup2(nullfd,i)<0)_exit(125);
        if(nullfd>2)close(nullfd);
        char *args[]={player,speaker_pcm,config,"auto",NULL};execv(player,args);_exit(127);
    }
    speaker_pid=child;speaker_stream_chunks=0;speaker_stream_bytes=(long)length;speaker_client_sequence=speaker_job_sequence=sequence;speaker_phase="running";speaker_reason="none";speaker_cancelled=false;emit("speaker.started");
    respond(fd,202,"{\"queued\":true,\"hardware_audible\":false}\n");
}
