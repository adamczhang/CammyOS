/* SPDX-License-Identifier: MIT. Fake pipe sink; no real input device is opened. */
#define main portal_unused_main
#include "../../src/cammy_portal.c"
#undef main
#define CHECK(x) do{if(!(x)){fprintf(stderr,"fixture line%d\n",__LINE__);exit(1);}}while(0)
struct fake_event {unsigned button,down;};
static int fake_sink(unsigned button,bool pressed,void *ctx){int fd=*(int *)ctx;struct fake_event e={button,pressed?1u:0u};return write(fd,&e,sizeof(e))==(ssize_t)sizeof(e)?0:EIO;}
static int fail_down(unsigned button,bool pressed,void *ctx){
    int result=fake_sink(button,pressed,ctx);return result?result:(pressed?EIO:0);
}
static struct fake_event event(int fd){struct pollfd p={fd,POLLIN,0};CHECK(poll(&p,1,1000)>0);struct fake_event e;CHECK(read(fd,&e,sizeof(e))==(ssize_t)sizeof(e));return e;}
static void send_command(int fd,uint64_t seq,unsigned button,unsigned ttl){struct input_message m={seq,(uint64_t)(monotime()*1000.0)+ttl,button,ttl?1u:0u};CHECK(send(fd,&m,sizeof(m),MSG_NOSIGNAL)==sizeof(m));}
static void fault_case(void){
    int channel[2],sink[2];CHECK(!socketpair(AF_UNIX,SOCK_SEQPACKET,0,channel));CHECK(!pipe(sink));pid_t child=fork();CHECK(child>=0);
    if(!child){close(channel[0]);close(sink[0]);input_guard_loop(channel[1],fail_down,&sink[1]);}
    close(channel[1]);close(sink[1]);send_command(channel[0],1,0,200);
    struct input_reply reply;CHECK(recv(channel[0],&reply,sizeof(reply),0)==sizeof(reply));CHECK(reply.error==EIO&&reply.button==-2);
    struct fake_event e=event(sink[0]);CHECK(e.down==1);e=event(sink[0]);CHECK(!e.down);
    CHECK(waitpid(child,NULL,0)==child);close(channel[0]);close(sink[0]);
}
int main(void){
    int channel[2],sink[2];CHECK(!socketpair(AF_UNIX,SOCK_SEQPACKET,0,channel));CHECK(!pipe(sink));pid_t child=fork();CHECK(child>=0);
    if(!child){close(channel[0]);close(sink[0]);input_guard_loop(channel[1],fake_sink,&sink[1]);}
    close(channel[1]);close(sink[1]);
    send_command(channel[0],1,0,200);struct fake_event e=event(sink[0]);CHECK(e.button==0&&e.down==1);
    send_command(channel[0],2,0,250);struct pollfd p={sink[0],POLLIN,0};CHECK(poll(&p,1,40)==0); /* renewal does not repeat down */
    send_command(channel[0],3,1,250);e=event(sink[0]);CHECK(e.button==0&&!e.down);e=event(sink[0]);CHECK(e.button==1&&e.down);
    e=event(sink[0]);CHECK(e.button==1&&!e.down); /* independent expiry */
    send_command(channel[0],2,0,200);CHECK(poll(&p,1,40)==0); /* stale sequence */
    send_command(channel[0],4,0,200);e=event(sink[0]);CHECK(e.button==0&&e.down);
    close(channel[0]);e=event(sink[0]);CHECK(e.button==0&&!e.down);CHECK(waitpid(child,NULL,0)==child);close(sink[0]);
    fault_case();
    puts("{\"synthetic\":true,\"renewal_no_repeat\":true,\"expiry_release\":true,\"parent_eof_release\":true,\"physical_motion_verified\":false}");return 0;
}
