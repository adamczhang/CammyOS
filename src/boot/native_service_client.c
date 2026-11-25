/* SPDX-License-Identifier: MIT
 * Fixed local boot operations. No arbitrary URL, shell, recording or media reads.
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <net/if.h>
#include <poll.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <stdint.h>
#include <limits.h>
#ifndef TOKEN_FILE
#define TOKEN_FILE "/etc/cammy/portal.token"
#endif
#ifndef CONFIG_FILE
#define CONFIG_FILE "/tmp/data/net_config.lua"
#endif
#ifndef PORTAL_PORT
#define PORTAL_PORT 8787
#endif
#ifndef NATIVE_PORT
#define NATIVE_PORT 8082
#endif
#ifndef RTSP_PORT
#define RTSP_PORT 8554
#endif
#ifndef TIMEOUT_MS
#define TIMEOUT_MS 8000
#endif
static double deadline;
static double preparation_until;
static char token[129], response[20000];
static double now(void) {struct timespec t;if(clock_gettime(CLOCK_MONOTONIC,&t))exit(2);return (double)t.tv_sec+(double)t.tv_nsec/1e9;}
static int waitfd(int fd,short events) {for(;;){double left=deadline-now();if(left<=0)return -1;struct pollfd p={fd,events,0};int n=poll(&p,1,(int)(left*1000)+1);if(n<0&&errno==EINTR)continue;return n>0 && !(p.revents&POLLNVAL)?0:-1;}}
static int sendall(int fd,const char *p,size_t n) {while(n){if(waitfd(fd,POLLOUT))return -1;ssize_t k=send(fd,p,n,MSG_NOSIGNAL);if(k<0&&(errno==EAGAIN||errno==EINTR))continue;if(k<=0)return -1;p+=k;n-=(size_t)k;}return 0;}
static int local_ip(char ip[INET_ADDRSTRLEN]) {
#ifdef TEST_LOCAL_IP
 snprintf(ip,INET_ADDRSTRLEN,"127.0.0.1");return 0;
#else
 int fd=socket(AF_INET,SOCK_DGRAM|SOCK_CLOEXEC,0);if(fd<0)return -1;
 struct ifreq r={0};snprintf(r.ifr_name,sizeof(r.ifr_name),"wlan0");int ok=ioctl(fd,SIOCGIFADDR,&r);close(fd);
 struct sockaddr_in *a=(struct sockaddr_in *)&r.ifr_addr;
 if(ok || !a->sin_addr.s_addr || a->sin_addr.s_addr==htonl(0xc0a80a01))return -1;
 return inet_ntop(AF_INET,&a->sin_addr,ip,INET_ADDRSTRLEN)?0:-1;
#endif
}
static int read_private(const char *path,char *out,size_t cap) {
 int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW|O_NONBLOCK);if(fd<0)return -1;
 struct stat s;int rc=-1;if(fstat(fd,&s)||!S_ISREG(s.st_mode)||s.st_nlink!=1||s.st_uid!=geteuid()||(s.st_mode&077)||s.st_size<1||(unsigned long long)s.st_size>=cap)goto done;
 ssize_t n=read(fd,out,cap-1);if(n!=s.st_size)goto done;out[n]=0;rc=0;
 done:close(fd);return rc;
}
static int load_token(void) {
 if(read_private(TOKEN_FILE,token,sizeof(token)))return -1;
 size_t n=strlen(token);while(n && (token[n-1]=='\n'||token[n-1]=='\r'))token[--n]=0;
 if(n<16)return -1;
 for(size_t i=0;i<n;i++)if(!isalnum((unsigned char)token[i])&&token[i]!='-'&&token[i]!='_')return -1;
 return 0;
}
/* Bounded flat JSON response parser; rejects duplicate keys and malformed values. */
struct field {char key[64],value[160];bool quoted;};static struct field fields[24];static size_t count;
static void ws(const char **p){while(isspace((unsigned char)**p))++*p;}
static int string(const char **p,char *out,size_t cap){if(*(*p)++!='"')return -1;size_t n=0;while(**p&&**p!='"'){unsigned char c=(unsigned char)*(*p)++;if(c<32||c=='\\'||n+1>=cap)return -1;out[n++]=(char)c;}if(**p!='"')return -1;++*p;out[n]=0;return 0;}
static bool number(const char *p) {
 if(*p=='-')p++;
 if(*p=='0')p++;else {if(*p<'1'||*p>'9')return false;while(isdigit((unsigned char)*p))p++;}
 if(*p=='.'){p++;if(!isdigit((unsigned char)*p))return false;while(isdigit((unsigned char)*p))p++;}
 if(*p=='e'||*p=='E'){p++;if(*p=='+'||*p=='-')p++;if(!isdigit((unsigned char)*p))return false;while(isdigit((unsigned char)*p))p++;}
 return !*p;
}
static int json(const char *p){count=0;ws(&p);if(*p++!='{')return -1;ws(&p);if(*p=='}'){p++;ws(&p);return *p?-1:0;}for(;;){if(count==24)return -1;struct field *f=&fields[count];if(string(&p,f->key,sizeof(f->key)))return -1;for(size_t i=0;i<count;i++)if(!strcmp(f->key,fields[i].key))return -1;ws(&p);if(*p++!=':')return -1;ws(&p);f->quoted=*p=='"';if(f->quoted){if(string(&p,f->value,sizeof(f->value)))return -1;}else{size_t n=0;while(*p&&*p!=','&&*p!='}'&&!isspace((unsigned char)*p)){if(n+1>=sizeof(f->value))return -1;f->value[n++]=*p++;}f->value[n]=0;if(strcmp(f->value,"true")&&strcmp(f->value,"false")&&strcmp(f->value,"null")){if(!number(f->value))return -1;}}count++;ws(&p);if(*p=='}'){p++;ws(&p);return *p?-1:0;}if(*p++!=',')return -1;ws(&p);}}
static bool is(const char *key,const char *value){for(size_t i=0;i<count;i++)if(!strcmp(key,fields[i].key))return !strcmp(value,fields[i].value) && (fields[i].quoted == (strcmp(value,"true") && strcmp(value,"false") && strcmp(value,"null") && !isdigit((unsigned char)*value)));return false;}
static int request(const char *ip,int port,const char *method,const char *path,const char *body,bool auth,bool rtsp) {
 deadline=now()+(rtsp?2.:(double)TIMEOUT_MS/1000.);
 if(preparation_until && preparation_until<deadline)deadline=preparation_until;
 if(deadline<=now())return -1;
 int fd=socket(AF_INET,SOCK_STREAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0);if(fd<0)return -1;int rc=-1;
 struct sockaddr_in a={.sin_family=AF_INET,.sin_port=htons((unsigned short)port)};
 if(inet_pton(AF_INET,ip,&a.sin_addr)!=1)goto done;
 if(connect(fd,(struct sockaddr *)&a,sizeof(a))&&errno!=EINPROGRESS)goto done;
 if(waitfd(fd,POLLOUT))goto done;
 int error=0;socklen_t elen=sizeof(error);if(getsockopt(fd,SOL_SOCKET,SO_ERROR,&error,&elen)||error)goto done;
 char req[1500],authline[240]="";if(auth)snprintf(authline,sizeof(authline),"Authorization: Bearer %s\r\nX-Agent-ID: cammy-boot\r\n",token);
 int n=rtsp?snprintf(req,sizeof(req),"DESCRIBE rtsp://%s:%d/ch00 RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n\r\n",ip,port):snprintf(req,sizeof(req),"%s %s HTTP/1.1\r\nHost: %s:%d\r\n%sContent-Type: application/json\r\nContent-Length: %zu\r\nConnection: close\r\n\r\n%s",method,path,ip,port,authline,strlen(body),body);
 if(n<0||(size_t)n>=sizeof(req)||sendall(fd,req,(size_t)n))goto done;
 size_t used=0,head=0,length=0;bool have_length=false,close_body=false;int status=0;
 for(;;){if(waitfd(fd,POLLIN))goto done;ssize_t k=recv(fd,response+used,sizeof(response)-1-used,0);if(k<0&&(errno==EINTR||errno==EAGAIN))continue;if(k==0 && head && !have_length && close_body && !auth && !rtsp && used>head && used-head<=16000){size_t body_size=used-head;if(memchr(response+head,0,body_size))goto done;memmove(response,response+head,body_size);response[body_size]=0;rc=0;break;}
  if(k<=0)goto done;
  used+=(size_t)k;response[used]=0;
  if(!head){char *end=strstr(response,"\r\n\r\n");if(end){head=(size_t)(end-response)+4;if(head>4096)goto done;
   char headers[4097];memcpy(headers,response,head);headers[head]=0;char *p=strstr(headers,"\r\n");if(!p)goto done;*p=0;
   if(sscanf(headers,rtsp?"RTSP/1.0 %d":"HTTP/1.1 %d",&status)!=1 || status<200||status>202)goto done;
   p+=2;while(*p && strcmp(p,"\r\n")){char *e=strstr(p,"\r\n");if(!e)goto done;*e=0;char *colon=strchr(p,':');if(!colon)goto done;*colon++=0;while(*colon==' '||*colon=='\t')colon++;
    if(!strcasecmp(p,"Transfer-Encoding"))goto done;
    if(!strcasecmp(p,"Connection")&&!strcasecmp(colon,"close"))close_body=true;
    if(!strcasecmp(p,"Content-Length")){if(have_length||!*colon)goto done;for(char *c=colon;*c;c++)if(!isdigit((unsigned char)*c))goto done;unsigned long v=strtoul(colon,NULL,10);if(v>16000)goto done;length=(size_t)v;have_length=true;}p=e+2;
   }if(have_length?!length:(auth||rtsp||!close_body))goto done;
  }else if(used>4096)goto done;}
  if(head && have_length && used>=head+length){if(used!=head+length||memchr(response+head,0,length))goto done;memmove(response,response+head,length);response[length]=0;rc=0;break;}if(used==sizeof(response)-1)goto done;
 }
 done:close(fd);return rc;
}
static int portal(const char *path,const char *body,bool home){char ip[INET_ADDRSTRLEN]="127.0.0.1";if(home&&local_ip(ip))return -1;return request(ip,PORTAL_PORT,body?"POST":"GET",path,body?body:"",true,false)||json(response)?-1:0;}
static int owned(bool idle){if(portal("/v1/state",NULL,false)||!is("owner","cammy-boot")||(idle&&!is("preview_active","false")))return -1;return 0;}
static int native(const char *path,bool post){return request("127.0.0.1",NATIVE_PORT,post?"POST":"GET",path,"",false,false)||json(response)?-1:0;}
static int normal_idle(void){if(owned(true)||native("/api/getdeviceinfo/?custom=1&cmd=2005",false)||!is("RecodStatus","0"))return -1;return 0;}
static int prefix(void){FILE *f=fopen(CONFIG_FILE,"r");if(!f)return -1;struct stat st;if(fstat(fileno(f),&st)||!S_ISREG(st.st_mode)||st.st_size>16384){fclose(f);return -1;}char line[512];int found=0;while(fgets(line,sizeof(line),f)){char compact[512];size_t n=0;bool quoted=false;for(size_t i=0;line[i];i++){if(line[i]=='"')quoted=!quoted;if(quoted||!isspace((unsigned char)line[i]))compact[n++]=line[i];}compact[n]=0;if(!strcmp(compact,"[\"ssid_prefix\"]=\"CammyOS\",")||!strcmp(compact,"[\"ssid_prefix\"]=\"CammyOS\""))found++;}int bad=ferror(f);fclose(f);return found==1&&!bad?0:-1;}
static int preparation_ttl(int *ttl){
 const char *raw=getenv("CAMMY_PREPARE_DEADLINE_MS");*ttl=120;if(!raw)return 0;
 if(!*raw||strlen(raw)>15)return -1;
 for(const char *p=raw;*p;p++)if(*p<'0'||*p>'9')return -1;
 char *end;errno=0;unsigned long long until_ms=strtoull(raw,&end,10);if(errno||*end)return -1;
 struct timespec t;if(clock_gettime(CLOCK_MONOTONIC,&t))return -1;
 uint64_t now=(uint64_t)t.tv_sec*1000u+(uint32_t)t.tv_nsec/1000000u;
 if(until_ms<=now||until_ms-now>90000u)return -1;
 uint64_t seconds=(until_ms-now)/1000u;if(!seconds)return -1;*ttl=(int)seconds;
 preparation_until=(double)until_ms/1000.0;return 0;
}
static void record_capture_mode(void){
 int fd=open("/tmp/cammy-runtime/last-request",O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC|O_NOFOLLOW,0600);
 if(fd>=0){ssize_t written=write(fd,"capture-mode\n",13);(void)written;close(fd);}
}
int main(int argc,char **argv){if(argc!=2)return 2;const char *op=argv[1];int rc=-1;
 if(!strcmp(op,"ip")){char ip[INET_ADDRSTRLEN];if(local_ip(ip))return 1;puts(ip);return 0;}
 int ttl=120;bool mutating=!strcmp(op,"acquire")||!strcmp(op,"init")||!strcmp(op,"photo")||!strcmp(op,"video")||!strcmp(op,"start");
 if(mutating&&preparation_ttl(&ttl))return 1;
 if(load_token())return 1;
 if(!strcmp(op,"photo")||!strcmp(op,"video"))record_capture_mode();
 if(!strcmp(op,"acquire")){char body[96];snprintf(body,sizeof(body),"{\"agent\":\"cammy-boot\",\"ttl\":%d}",ttl);if(!portal("/v1/state",NULL,false)&&(is("owner","null")||is("owner","cammy-boot"))&&is("preview_active","false")&&!portal("/v1/session",body,false)&&is("owner","cammy-boot"))rc=0;}
 else if(!strcmp(op,"release")){if(!portal("/v1/session/release","{}",false)&&is("released","true"))rc=0;}
 else if(!strcmp(op,"idle")){if(!portal("/v1/state",NULL,false)&&is("owner","null")&&is("preview_active","false"))rc=0;}
 else if(!strcmp(op,"preflight")){if(!normal_idle()&&!prefix()&&!native("/api/getdeviceinfo/?custom=1&cmd=2008",false)&&is("current_mode","0"))rc=0;}
 else if(!strcmp(op,"init")){if(!normal_idle()&&!prefix()&&!native("/api/setdeviceinfo/?custom=1&cmd=3003&str=CammyOS",true)&&is("Cmd","3003")&&is("Value","0"))rc=0;}
 else if(!strcmp(op,"photo")||!strcmp(op,"video")){bool photo=!strcmp(op,"photo");if(!normal_idle()&&!native(photo?"/api/setdeviceinfo/?custom=1&cmd=3034&par=4":"/api/setdeviceinfo/?custom=1&cmd=3034&par=0",true)&&is("Value","0"))rc=0;}
 else if(!strcmp(op,"mode-photo")||!strcmp(op,"mode-video")){if(!owned(true)&&!native("/api/getdeviceinfo/?custom=1&cmd=2008",false)&&is("current_mode",!strcmp(op,"mode-photo")?"4":"0"))rc=0;}
 else if(!strcmp(op,"start")){if(!owned(true)&&!portal("/v1/preview/start","{}",true)&&is("preview_active","true"))rc=0;}
 else if(!strcmp(op,"sdp")){char ip[INET_ADDRSTRLEN];if(!owned(false)&&!local_ip(ip)&&!request(ip,RTSP_PORT,"","","",false,true)&&!strncmp(response,"v=0",3)&&(strstr(response,"\nm=video ")))rc=0;}
 else return 2;
 if(rc)fprintf(stderr,"boot operation unavailable: %s\n",op);else puts("ok");return rc?1:0;
}
