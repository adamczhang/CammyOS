/* SPDX-License-Identifier: MIT. Explicit, one-per-boot preparation job. */
static const char *media_prepare_root;
static bool media_prepare_requested;
static char media_initiator[49];
/* A clean refusal (nothing changed on the camera) may be retried a few times per boot. */
static int media_refusals;
#define MEDIA_REFUSALS_MAX 5
static int media_private_dir(void){struct stat st;if(!media_prepare_root)return -1;
    if(mkdir(media_prepare_root,0700)&&errno!=EEXIST)return -1;
    return lstat(media_prepare_root,&st)||!S_ISDIR(st.st_mode)||(st.st_mode&0777)!=0700||st.st_uid!=geteuid()?-1:0;}
static void media_path(char path[512],const char *name){(void)snprintf(path,512,"%s/%s",media_prepare_root,name);}
static const char *media_phase(void){
    if(!media_prepare_root)return "legacy";
    static char value[64];char path[512];media_path(path,"cancel");if(access(path,F_OK)==0)return "cancelled";
    media_path(path,"state");
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);if(fd<0){media_path(path,"request");return media_prepare_requested||access(path,F_OK)==0?"queued":"unprepared";}
    ssize_t n=read(fd,value,sizeof(value)-1);close(fd);if(n<=0)return "invalid-state";value[n]=0;
    while(n>0&&(value[n-1]=='\n'||value[n-1]=='\r'))value[--n]=0;
    const char *allowed[]={"queued","initializing","ready","failed","cancelled","requires-recovery","refused"};
    for(unsigned i=0;i<sizeof(allowed)/sizeof(*allowed);i++)if(!strcmp(value,allowed[i]))return value;
    return "invalid-state";
}
static void media_status(int fd){char out[512];const char *phase=media_phase();
    snprintf(out,sizeof(out),"{\"managed\":%s,\"phase\":\"%s\",\"ready\":%s,\"automatic_boot_initialization\":false,\"requires_recovery\":%s}\n",media_prepare_root?"true":"false",phase,!strcmp(phase,"ready")?"true":"false",(!strcmp(phase,"failed")||!strcmp(phase,"cancelled")||!strcmp(phase,"requires-recovery")||!strcmp(phase,"invalid-state"))?"true":"false");respond(fd,200,out);}
static void media_cancel(void){
    if(!media_prepare_root)return;
    char request_path[512];media_path(request_path,"request");
    if(!media_prepare_requested && access(request_path,F_OK)!=0)return;
    const char *phase=media_phase();if(!strcmp(phase,"ready")||!strcmp(phase,"refused"))return;
    char path[512];media_path(path,"cancel");int fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
    if(fd>=0){ssize_t ignored=write(fd,"cancel\n",7);(void)ignored;close(fd);}
}
static void media_prepare(int fd,const char *agent){
    if(!media_prepare_root){error(fd,501,"managed media not enabled");return;}
    if(!agent[0]){error(fd,400,"X-Agent-ID required for preparation");return;}
    const char *phase=media_phase();
    if(!strcmp(phase,"ready")){respond(fd,200,"{\"queued\":false,\"ready\":true}\n");return;}
    if(!strcmp(phase,"refused")){
        if(media_refusals>=MEDIA_REFUSALS_MAX){error(fd,503,"media preparation refused too often this boot");return;}
        char state[512];media_path(state,"state");char request[512];media_path(request,"request");
        if(access(request,F_OK)==0||unlink(state)){error(fd,503,"refused media job cleanup uncertain");return;}
        media_refusals++;media_prepare_requested=false;media_initiator[0]=0;
        if(!strcmp(owner,"cammy-boot"))owner[0]=0;
        phase=media_phase();
    }
    if(strcmp(phase,"unprepared") && strcmp(phase,"queued") && strcmp(phase,"initializing")){error(fd,503,"media preparation requires recovery; no automatic retry");return;}
    if(owner[0] && strcmp(owner,"cammy-boot")){error(fd,409,"release current control lease before preparing media");return;}
    if(media_private_dir()){error(fd,503,"private media job directory unavailable");return;}
    char path[512];media_path(path,"request");
    if(media_prepare_requested || access(path,F_OK)==0){if(strcmp(media_initiator,agent)){error(fd,409,"another agent owns preparation");return;}respond(fd,202,"{\"queued\":true,\"created\":false,\"ready\":false}\n");return;}
    if(new_lease_id()){error(fd,503,"preparation lease identity unavailable");return;}
    uint32_t boot[4];if(cfs_current_boot(boot)){error(fd,503,"boot identity unavailable");return;}
    char record[256];unsigned char bytes[16];memcpy(bytes,boot,sizeof(bytes));char hex[33];
    for(unsigned i=0;i<16;i++)(void)snprintf(hex+i*2,3,"%02x",bytes[i]);
    uint64_t deadline=(uint64_t)(monotime()*1000.0)+90000u;
    int n=snprintf(record,sizeof(record),"format=cammy-media-prepare/1\nboot=%s\ndeadline_ms=%" PRIu64 "\n",hex,deadline);
    int job=open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
    if(job<0 || n<=0 || n>=(int)sizeof(record)){if(job>=0)close(job);error(fd,503,"media job already exists or unavailable");return;}
    bool ok=write(job,record,(size_t)n)==n;if(close(job))ok=false;
    if(!ok){error(fd,503,"media job write uncertain; recovery required");return;}
    strcpy(owner,"cammy-boot");strcpy(media_initiator,agent);lease_end=monotime()+120;media_prepare_requested=true;emit("media.preparation.queued");
    respond(fd,202,"{\"queued\":true,\"created\":true,\"ready\":false,\"sensing_session_started\":false}\n");
}
static void media_cancel_owned(int fd,const char *agent){
    if(!media_prepare_root){error(fd,501,"managed media not enabled");return;}
    if(strcmp(agent,media_initiator)||!media_initiator[0]){error(fd,409,"active preparation initiator required");return;}
    if(!strcmp(media_phase(),"ready")){respond(fd,200,"{\"cancelled\":false,\"already_ready\":true}\n");return;}
    media_cancel();if(!strcmp(owner,"cammy-boot")){audio_cancel();preview_stop();owner[0]=0;}
    respond(fd,200,"{\"cancelled\":true,\"hardware_cleanup_confirmed\":false}\n");
}
