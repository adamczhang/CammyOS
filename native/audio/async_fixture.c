/* SPDX-License-Identifier: MIT -- fake MPI calls, no camera/audio devices. */
#define _POSIX_C_SOURCE 200809L
#include "cammy_audio_async.h"
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static unsigned checks;
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr,"%d: %s\n",__LINE__,#x); exit(1); } } while (0)
struct fake {
    _Atomic unsigned stage, allow_open, allow_send, allow_close;
    _Atomic unsigned opens, sends, closes, data_errors, thread_errors;
    int open_result, send_result, close_result, release_send, eof_send, release_close;
    const uint8_t *pcm; uint32_t bytes;
    ca_driver_callback callback; void *cookie; uint64_t job;
    pthread_t worker;
};
static struct fake f;
static struct ca_async *a; /* intentional process-lifetime root on uncertainty */
static struct ca_core *c;
static struct ca_binding id={.instance=7,.boot={1}};
static struct ca_binding owner={.instance=7,.boot={1},.controller=9,.generation=1};
static uint8_t pcm[640];
static void pause_ms(void) { struct timespec t={0,1000000}; (void)nanosleep(&t,NULL); }
static void gate(_Atomic unsigned *flag) { while (!atomic_load(flag)) pause_ms(); }
static int fake_open(void *ctx,uint64_t job,ca_driver_callback cb,void *cookie)
{
    struct fake *p=ctx; p->worker=pthread_self(); p->callback=cb;p->cookie=cookie;p->job=job;
    atomic_fetch_add(&p->opens,1); atomic_store(&p->stage,1); gate(&p->allow_open);
    return p->open_result;
}
static int fake_send(void *ctx,uint64_t job,uint32_t frame,const uint8_t *data,uint32_t bytes)
{
    struct fake *p=ctx;
    if (!pthread_equal(p->worker,pthread_self()) || frame!=1 || job!=p->job) atomic_fetch_add(&p->thread_errors,1);
    p->pcm=data;p->bytes=bytes;atomic_fetch_add(&p->sends,1);atomic_store(&p->stage,2);
    gate(&p->allow_send);
    if (p->release_send) (void)p->callback(p->cookie,job,CA_FRAME_RETURNED,1);
    if (p->eof_send) (void)p->callback(p->cookie,job,CA_STREAM_EOF,0);
    if (p->send_result==-1) p->pcm=NULL;
    return p->send_result;
}
static int fake_close(void *ctx,uint64_t job)
{
    struct fake *p=ctx;
    if (!pthread_equal(p->worker,pthread_self()) || job!=p->job) atomic_fetch_add(&p->thread_errors,1);
    atomic_fetch_add(&p->closes,1);atomic_store(&p->stage,3);gate(&p->allow_close);
    if (p->pcm && (p->pcm[0]!=0x5a || p->pcm[p->bytes-1]!=0x5a)) atomic_fetch_add(&p->data_errors,1);
    if (p->release_close && p->pcm) (void)p->callback(p->cookie,job,CA_FRAME_RETURNED,1);
    if (!p->close_result) p->pcm=NULL;
    atomic_store(&p->stage,4);return p->close_result;
}
static struct ca_driver driver={CA_DRIVER_ABI,sizeof(struct ca_driver),1,&f,fake_open,fake_send,fake_close};
static void setup(void)
{
    memset(pcm,0x5a,sizeof pcm);
    atomic_init(&f.stage,0);atomic_init(&f.allow_open,1);atomic_init(&f.allow_send,1);atomic_init(&f.allow_close,1);
    atomic_init(&f.opens,0);atomic_init(&f.sends,0);atomic_init(&f.closes,0);
    atomic_init(&f.data_errors,0);atomic_init(&f.thread_errors,0);
    f.release_send=1;f.eof_send=1;
    a=ca_async_create(&id,&driver);CHECK(a!=NULL);c=ca_async_core(a);
    CHECK(ca_bind(c,&owner,100,10000)==CA_OK);
}
static void start(uint64_t request)
{
    CHECK(ca_enqueue(c,&owner,request,100,9000,pcm,sizeof pcm)==CA_OK);
    ca_async_poll(a,100);
    /* A fast synthetic driver can complete within this very poll. */
    CHECK(c->phase!=CA_QUEUED && c->reason!=CA_SUBMIT_REFUSED);
}
static void stage(unsigned wanted)
{
    unsigned n;
    for(n=0;n<2000 && atomic_load(&f.stage)<wanted;++n) pause_ms();
    CHECK(atomic_load(&f.stage)>=wanted);
}
static void idle(void)
{
    unsigned n;
    for(n=0;n<2000 && c->phase!=CA_IDLE;++n){ca_async_poll(a,c->now);pause_ms();}
    CHECK(c->phase==CA_IDLE);CHECK(atomic_load(&f.data_errors)==0);CHECK(atomic_load(&f.thread_errors)==0);
}
static void emit(unsigned event,uint32_t frame) { CHECK(ca_async_callback(a,c->job,event,frame)==0); }
static void dispose(void) { CHECK(ca_async_destroy(a)==1);a=NULL;c=NULL; }
static void *producer(void *unused)
{
    (void)unused;
    (void)ca_async_callback(a,999,CA_FRAME_RETURNED,1);
    (void)ca_async_callback(a,999,CA_STREAM_EOF,0);
    return NULL;
}
int main(int argc,char **argv)
{
    unsigned which,i;struct ca_async_status status;uint64_t old;
    if(argc!=2)return 2;
    which=(unsigned)strtoul(argv[1],NULL,10);setup();
    switch(which){
    case 0: start(1);idle();CHECK(c->reason==CA_COMPLETE);CHECK(atomic_load(&f.closes)==1);dispose();break;
    case 1: /* blocked open: actor cancel and timeout retain object */
        atomic_store(&f.allow_open,0);start(1);stage(1);
        CHECK(ca_cancel(c,&owner,101)==CA_OK);ca_async_poll(a,301);
        CHECK(c->phase==CA_UNCERTAIN && ca_async_destroy(a)==0);CHECK(c->pcm[0]==0x5a);
        atomic_store(&f.allow_open,1);idle();CHECK(atomic_load(&f.sends)==0);dispose();break;
    case 2: /* stop cannot overtake a blocked send on another worker */
        atomic_store(&f.allow_send,0);f.release_send=f.eof_send=0;f.release_close=1;
        start(1);stage(2);CHECK(ca_cancel(c,&owner,101)==CA_OK);ca_async_poll(a,301);
        CHECK(c->phase==CA_UNCERTAIN && atomic_load(&f.closes)==0);CHECK(ca_async_destroy(a)==0);
        atomic_store(&f.allow_send,1);idle();dispose();break;
    case 3: /* returned buffer and EOF still do not prove channel shutdown */
        atomic_store(&f.allow_close,0);start(1);
        for(i=0;i<50 && atomic_load(&f.stage)<3;++i){ca_async_poll(a,100);pause_ms();}
        stage(3);ca_async_poll(a,500);ca_async_poll(a,700);
        CHECK(c->phase==CA_UNCERTAIN && ca_async_destroy(a)==0);CHECK(c->pcm[0]==0x5a);
        atomic_store(&f.allow_close,1);idle();dispose();break;
    case 4: /* close failure: process-lifetime retention, no retry/next job */
        f.close_result=-1;start(1);
        for(i=0;i<100 && atomic_load(&f.stage)<4;++i){ca_async_poll(a,100);pause_ms();}
        stage(4);ca_async_poll(a,100);ca_async_poll(a,301);
        CHECK(c->phase==CA_UNCERTAIN && ca_async_destroy(a)==0);CHECK(atomic_load(&f.closes)==1);
        CHECK(ca_enqueue(c,&owner,2,301,9000,pcm,sizeof pcm)==CA_BUSY);break;
    case 5: /* successful close without accepted frame return stays uncertain */
        f.release_send=0;start(1);
        for(i=0;i<100 && atomic_load(&f.stage)<4;++i){ca_async_poll(a,100);pause_ms();}
        stage(4);ca_async_poll(a,500);ca_async_poll(a,700);
        CHECK(c->phase==CA_UNCERTAIN && ca_async_destroy(a)==0);break;
    case 6: /* wrong job and duplicate frame callbacks cannot finish playback */
        f.eof_send=f.release_send=0;start(1);stage(2);
        CHECK(ca_async_callback(a,999,CA_STREAM_EOF,0)==0);ca_async_poll(a,100);
        CHECK(atomic_load(&f.closes)==0 && c->phase==CA_PLAYING);
        emit(CA_FRAME_RETURNED,1);emit(CA_FRAME_RETURNED,1);emit(CA_STREAM_EOF,0);idle();
        ca_async_status(a,&status);CHECK(status.stale==1 && status.duplicates==1);dispose();break;
    case 7: /* mailbox saturation invalidates owner; never fabricate cleanup */
        atomic_store(&f.allow_open,0);start(1);stage(1);
        for(i=0;i<32;++i)(void)ca_async_callback(a,c->job,CA_FRAME_RETURNED,1);
        ca_async_poll(a,100);CHECK(!c->bound);atomic_store(&f.allow_open,1);stage(4);
        ca_async_poll(a,500);CHECK(c->phase==CA_UNCERTAIN && ca_async_destroy(a)==0);break;
    case 8: /* callback fields are fixed typed events, no pointer passthrough */
        f.eof_send=f.release_send=0;start(1);stage(2);
        CHECK(ca_async_callback(a,c->job,100,0)==-1);CHECK(ca_async_callback(a,c->job,CA_FRAME_RETURNED,7)==-1);
        CHECK(ca_async_callback(a,c->job,CA_STREAM_EOF,1)==-1);
        emit(CA_FRAME_RETURNED,1);emit(CA_STREAM_EOF,0);idle();ca_async_status(a,&status);
        CHECK(status.rejected==1);dispose();break;
    case 9: f.send_result=-1;f.eof_send=f.release_send=0;start(1);idle();CHECK(c->reason==CA_BACKEND_ERROR);dispose();break;
    case 10: f.send_result=-2;f.eof_send=f.release_send=0;f.release_close=1;start(1);idle();CHECK(c->reason==CA_BACKEND_ERROR);dispose();break;
    case 11: f.open_result=-1;start(1);idle();CHECK(atomic_load(&f.sends)==0 && atomic_load(&f.closes)==1);CHECK(c->reason==CA_BACKEND_ERROR);dispose();break;
    case 12: { /* concurrent callbacks: sixteen hundred stale events, no races */
        pthread_t threads[4];unsigned round,j;
        f.eof_send=f.release_send=0;start(1);stage(2);
        for(round=0;round<200;++round){
            for(j=0;j<4;++j)CHECK(pthread_create(&threads[j],NULL,producer,NULL)==0);
            for(j=0;j<4;++j)CHECK(pthread_join(threads[j],NULL)==0);
            ca_async_poll(a,100);
        }
        emit(CA_FRAME_RETURNED,1);emit(CA_STREAM_EOF,0);idle();ca_async_status(a,&status);
        CHECK(status.stale==1600 && status.overflow==0);dispose();break;
    }
    case 13: dispose();driver.synthetic=0;CHECK(ca_async_create(&id,&driver)==NULL);CHECK(ca_native_audio_driver()==NULL);break;
    case 14:
        start(1);old=c->job;idle();atomic_store(&f.stage,0);f.eof_send=f.release_send=0;
        start(2);stage(2);CHECK(c->job!=old);CHECK(ca_async_callback(a,old,CA_STREAM_EOF,0)==0);
        ca_async_poll(a,100);CHECK(c->phase==CA_PLAYING && atomic_load(&f.closes)==1);
        emit(CA_FRAME_RETURNED,1);emit(CA_STREAM_EOF,0);idle();dispose();break;
    case 15:
        CHECK(ca_enqueue(c,&owner,1,100,9000,pcm,sizeof pcm)==CA_OK);
        CHECK(ca_cancel(c,&owner,100)==CA_OK);ca_async_poll(a,100);
        CHECK(atomic_load(&f.opens)==0);dispose();break;
    default:return 2;
    }
    printf("{\"scenario\":%u,\"checks\":%u,\"synthetic\":true,\"physical_available\":false}\n",which,checks);
    return 0;
}
