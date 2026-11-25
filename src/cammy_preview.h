/* SPDX-License-Identifier: MIT
 * Fixed native event command and bounded RTSP metadata check. No frame capture.
 * Test harnesses may replace internal ports/timers; no production CLI/API override.
 */
static int preview_event_port=9999, preview_rtsp_port=8554;
static double preview_keepalive_seconds=30.0;
static int preview_rtsp_fd=-1;
static bool preview_connected, preview_listener_seen, preview_listener_checked, preview_sdp_ready;
static const char *preview_phase="idle", *preview_failure=NULL;
static char preview_address[INET_ADDRSTRLEN]="127.0.0.1";
static char preview_rtsp_request[256], preview_rtsp_reply[8193];
static size_t preview_tx, preview_rtsp_tx, preview_rtsp_used;
static bool preview_rtsp_connected;
static unsigned long preview_commands;
static double preview_event_deadline, preview_next_command, preview_check_deadline, preview_next_attempt, preview_checked;
static bool preview_checking;
static bool preview_event_ready=true; /* poll saw POLLIN/HUP/ERR on the event socket */
static int preview_rtsp_status;
static const char preview_command[]="{\"cmd_id\":\"4016\"}";

static void preview_close_rtsp(void) {
    if(preview_rtsp_fd>=0)close(preview_rtsp_fd);
    preview_rtsp_fd=-1;preview_checking=false;
}
static void preview_stop(void) {
    bool was_active=preview_fd>=0;
    if(was_active)close(preview_fd);
    preview_fd=-1;preview_connected=false;preview_close_rtsp();
    preview_phase="idle";preview_failure=NULL;preview_commands=0;
    preview_listener_seen=false;preview_listener_checked=false;preview_sdp_ready=false;preview_checked=0;preview_rtsp_status=0;
    if(was_active)emit("preview.stopped");
}
static void preview_event_failed(const char *reason) {
    preview_stop();preview_phase="failed";preview_failure=reason;emit("preview.disconnected");
}
static void preview_check_failed(const char *reason) {
    preview_close_rtsp();preview_phase="failed";preview_failure=reason;preview_checked=monotime();emit("preview.readiness_failed");
}
static int preview_socket(const char *ip, int port) {
    int fd=socket(AF_INET,SOCK_STREAM,0);if(fd<0)return -1;
    int flags=fcntl(fd,F_GETFL);
    if(flags<0 || fcntl(fd,F_SETFL,flags|O_NONBLOCK)<0){close(fd);return -1;}
    struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons((uint16_t)port)};
    if(inet_pton(AF_INET,ip,&addr.sin_addr)!=1 || (connect(fd,(struct sockaddr*)&addr,sizeof(addr))<0 && errno!=EINPROGRESS)){close(fd);return -1;}
    return fd;
}
static int preview_connect_status(int fd) {
    struct pollfd p={fd,POLLOUT,0};int n=poll(&p,1,0);
    if(n<0)return errno==EINTR?0:-1;
    if(!n)return 0;
    int err=0;socklen_t len=sizeof(err);
    return getsockopt(fd,SOL_SOCKET,SO_ERROR,&err,&len)<0 || err?-1:1;
}
static int preview_write(int fd, const char *data, size_t len, size_t *sent) {
    if(*sent==len)return 1;
    ssize_t n=send(fd,data+*sent,len-*sent,MSG_NOSIGNAL);
    if(n<0)return errno==EAGAIN || errno==EWOULDBLOCK || errno==EINTR?0:-1;
    if(!n)return -1;
    *sent+=(size_t)n;return *sent==len?1:0;
}
static void preview_begin_check(void) {
    preview_close_rtsp();preview_checking=true;preview_phase="waiting-for-listener";preview_failure=NULL;
    preview_check_deadline=monotime()+5;preview_next_attempt=0;preview_checked=0;
    preview_listener_seen=false;preview_listener_checked=false;preview_sdp_ready=false;preview_rtsp_status=0;
}
static int preview_start(const char *address) {
    if(preview_fd>=0)return 0;
    preview_stop();snprintf(preview_address,sizeof(preview_address),"%s",address);
    preview_fd=preview_socket("127.0.0.1",preview_event_port);
    if(preview_fd<0){preview_phase="failed";preview_failure="event-unavailable";return -1;}
    preview_phase="event-connecting";preview_tx=0;preview_event_deadline=monotime()+2;return 0;
}
/* Returns 1 only for a bounded, matching RTSP response with video SDP. */
static int preview_parse_sdp(void) {
    char *end=strstr(preview_rtsp_reply,"\r\n\r\n");
    if(!end)return preview_rtsp_used>2048?-1:0;
    size_t header_len=(size_t)(end-preview_rtsp_reply)+4;
    if(header_len>2048)return -1;
    char header[2049];memcpy(header,preview_rtsp_reply,header_len);header[header_len]=0;
    char *save=NULL,*line=strtok_r(header,"\r\n",&save);
    if(!line || strlen(line)<12 || strncmp(line,"RTSP/1.0 ",9) || !isdigit((unsigned char)line[9]) || !isdigit((unsigned char)line[10]) || !isdigit((unsigned char)line[11]) || (line[12] && line[12]!=' '))return -1;
    int status=(line[9]-'0')*100+(line[10]-'0')*10+line[11]-'0';
    preview_rtsp_status=status;
    if(status!=200)return -1;
    bool have_length=false,have_cseq=false,have_type=false;size_t length=0;
    while((line=strtok_r(NULL,"\r\n",&save))) {
        char *colon=strchr(line,':');if(!colon)return -1;*colon=0;
        char *value=colon+1;while(*value==' ' || *value=='\t')value++;
        if(!strcasecmp(line,"Content-Length")) {
            char *tail;errno=0;if(have_length || !isdigit((unsigned char)*value))return -1;
            unsigned long n=strtoul(value,&tail,10);if(errno || *tail || !n || n>4096)return -1;
            length=(size_t)n;have_length=true;
        } else if(!strcasecmp(line,"CSeq")) {if(have_cseq || strcmp(value,"1"))return -1;have_cseq=true;}
        else if(!strcasecmp(line,"Content-Type")) {
            if(have_type || strncasecmp(value,"application/sdp",15) || (value[15] && value[15]!=';' && value[15]!=' '))return -1;
            have_type=true;
        } else if(!strcasecmp(line,"Transfer-Encoding"))return -1;
    }
    if(!have_length || !have_cseq || !have_type)return -1;
    if(preview_rtsp_used<header_len+length)return 0;
    if(preview_rtsp_used!=header_len+length)return -1;
    char *body=preview_rtsp_reply+header_len;
    if(strncmp(body,"v=0\r\n",5) && strncmp(body,"v=0\n",4))return -1;
    bool video=false,mapping=false,in_video=false;
    for(line=strtok_r(body,"\r\n",&save);line;line=strtok_r(NULL,"\r\n",&save)) {
        if(!strncmp(line,"m=",2)){in_video=!strncmp(line,"m=video ",8);if(in_video)video=true;}
        if(in_video && !strncmp(line,"a=rtpmap:",9))mapping=true;
    }
    return video && mapping?1:-1;
}
static void preview_tick(void) {
    if(preview_fd<0)return;
    if(!owner[0] || monotime()>=lease_end){preview_stop();return;}
    double now=monotime();
    if(!preview_connected) {
        int result=preview_connect_status(preview_fd);
        if(result<0 || now>=preview_event_deadline){preview_event_failed(result<0?"event-unavailable":"event-timeout");return;}
        if(!result)return;
        preview_connected=true;preview_phase="event-connected";
    }
    if(preview_tx<sizeof(preview_command)-1 || now>=preview_next_command) {
        if(preview_tx==sizeof(preview_command)-1){preview_tx=0;preview_event_deadline=now+2;}
        int result=preview_write(preview_fd,preview_command,sizeof(preview_command)-1,&preview_tx);
        if(result<0 || (!result && now>=preview_event_deadline)){preview_event_failed("event-write-failed");return;}
        if(result){preview_commands++;preview_next_command=now+preview_keepalive_seconds;emit(preview_commands==1?"preview.started":"preview.keepalive");if(preview_commands==1)preview_begin_check();}
    }
    /* 0.9: read the event socket only when poll reported it readable (or hung up). */
    if(preview_event_ready){
        preview_event_ready=false;
        char discard[4096];ssize_t n=recv(preview_fd,discard,sizeof(discard),0);
        if(!n || (n<0 && errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR)){preview_event_failed("event-disconnected");return;}
    }
    ssize_t n;
    if(!preview_checking)return;
    if(now>=preview_check_deadline){preview_check_failed(preview_listener_seen?"rtsp-timeout":"rtsp-unreachable");return;}
    if(preview_rtsp_fd<0) {
        if(now<preview_next_attempt)return;
        preview_next_attempt=now+.2;preview_listener_checked=true;
        preview_rtsp_fd=preview_socket(preview_address,preview_rtsp_port);preview_rtsp_connected=false;
        preview_rtsp_used=0;preview_rtsp_tx=0;preview_rtsp_reply[0]=0;
        if(preview_rtsp_fd<0)return;
    }
    if(!preview_rtsp_connected) {
        int result=preview_connect_status(preview_rtsp_fd);
        if(result<0){close(preview_rtsp_fd);preview_rtsp_fd=-1;return;}
        if(!result)return;
        preview_rtsp_connected=true;preview_listener_seen=true;preview_phase="checking-sdp";
        snprintf(preview_rtsp_request,sizeof(preview_rtsp_request),"DESCRIBE rtsp://%s:%d/ch00 RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\nUser-Agent: CammyOS/0.3\r\n\r\n",preview_address,preview_rtsp_port);
    }
    int written=preview_write(preview_rtsp_fd,preview_rtsp_request,strlen(preview_rtsp_request),&preview_rtsp_tx);
    if(written<0){preview_check_failed("rtsp-write-failed");return;}
    if(!written)return;
    n=recv(preview_rtsp_fd,preview_rtsp_reply+preview_rtsp_used,sizeof(preview_rtsp_reply)-1-preview_rtsp_used,0);
    if(n<0){if(errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR)preview_check_failed("rtsp-read-failed");return;}
    if(!n){preview_check_failed("rtsp-incomplete-response");return;}
    if(memchr(preview_rtsp_reply+preview_rtsp_used,0,(size_t)n)){preview_check_failed("rtsp-protocol-error");return;}
    preview_rtsp_used+=(size_t)n;preview_rtsp_reply[preview_rtsp_used]=0;
    int result=preview_parse_sdp();
    if(result<0){preview_check_failed("rtsp-protocol-error");return;}
    if(result){preview_close_rtsp();preview_sdp_ready=true;preview_phase="sdp-ready";preview_checked=now;emit("preview.sdp_ready");}
    else if(preview_rtsp_used==sizeof(preview_rtsp_reply)-1)preview_check_failed("rtsp-response-too-large");
}
/* RTSP readiness check socket: writable until connected and the DESCRIBE is sent, then replies. */
static short preview_rtsp_poll_events(void) {
    return (short)(!preview_rtsp_connected || preview_rtsp_tx<strlen(preview_rtsp_request)?POLLOUT:POLLIN);
}
static short preview_poll_events(void) {return (short)(POLLIN | (!preview_connected || preview_tx<sizeof(preview_command)-1?POLLOUT:0));}
static void preview_status(int fd) {
    char out[1000];
    snprintf(out,sizeof(out),"{\"phase\":\"%s\",\"preview_active\":%s,\"event_connected\":%s,\"commands_sent\":%lu,\"keepalive_interval_seconds\":%.3f,\"listener_reachable\":%s,\"sdp_ready\":%s,\"rtsp_status\":%d,\"checked_monotonic_seconds\":%.6f,\"sample_age_seconds\":%.3f,\"failure_reason\":%s%s%s,\"frame_fresh\":null,\"frame_evidence\":\"not-collected\"}",preview_phase,preview_fd>=0?"true":"false",preview_connected?"true":"false",preview_commands,preview_keepalive_seconds,preview_listener_checked?(preview_listener_seen?"true":"false"):"null",preview_checked || preview_listener_seen?(preview_sdp_ready?"true":"false"):"null",preview_rtsp_status,preview_checked,preview_checked?monotime()-preview_checked:0,preview_failure?"\"":"",preview_failure?preview_failure:"null",preview_failure?"\"":"");respond(fd,200,out);
}
