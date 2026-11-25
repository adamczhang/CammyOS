/* SPDX-License-Identifier: MIT. Synthetic backend, no LVGL/device access. */
#include "cammy_face_surface.h"
#include <stdio.h>
#include <string.h>
#define CHECK(x) do { checks++; if (!(x)) { fprintf(stderr,"line %d: %s\n",__LINE__,#x); return 1; } } while(0)
struct fixture { void *screen, *top; int fail_create, destroy_on_pixels; unsigned creates, pixels, removes; struct cmf_surface *s; };
static void *screen(void *p) { return ((struct fixture *)p)->screen; }
static void *top(void *p) { return ((struct fixture *)p)->top; }
static void *create(void *p, void *parent, struct cmf_surface *s) {
    struct fixture *f=p; f->creates++; f->s=s;
    return f->fail_create || parent != f->top ? NULL : (void *)f;
}
static void pixels(void *p, void *obj, const void *buf, unsigned w, unsigned h) {
    struct fixture *f=p;
    if (obj==f && buf && w==2 && h==2) f->pixels++;
    if (f->destroy_on_pixels) cmf_surface_deleted(f->s,obj);
}
static void remove_object(void *p, void *obj) {
    struct fixture *f=p; f->removes++; cmf_surface_deleted(f->s,obj);
}
int main(void) {
    unsigned checks=0;
    const unsigned char pixels4[16]={0};
    struct cmf_surface s;
    struct fixture f={0}; f.screen=&f; f.top=&s;
    const struct cmf_gui_api a={&f,screen,top,create,pixels,remove_object};
    CHECK(cmf_surface_init(&s,&a,pixels4,16,2,2,100,30,100)==0);
    cmf_surface_tick(&s,129); CHECK(f.creates==0);
    cmf_surface_tick(&s,130); CHECK(s.phase==CMF_VISIBLE && f.creates==1 && f.pixels==1);
    cmf_surface_tick(&s,229); CHECK(f.creates==1 && !f.removes);
    cmf_surface_tick(&s,230); CHECK(s.phase==CMF_REMOVED && f.removes==1 && s.deleted==1);
    cmf_surface_tick(&s,231); CHECK(f.removes==1);
    for (unsigned fault=0;fault<8;fault++) {
        memset(&f,0,sizeof(f));f.screen=&f;f.top=&s;
        CHECK(cmf_surface_init(&s,&a,pixels4,16,2,2,100,30,100)==0);
        if (fault==0) cmf_surface_request_stop(&s);
        if (fault==1) f.screen=NULL;
        if (fault==2) f.top=NULL;
        if (fault==3) f.fail_create=1;
        if (fault==4) f.destroy_on_pixels=1;
        cmf_surface_tick(&s,130);
        if (fault==5) f.screen=&s;
        if (fault==6) cmf_surface_deleted(&s,&f);
        if (fault==7) cmf_surface_request_stop(&s);
        cmf_surface_tick(&s,131);
        CHECK(s.phase==CMF_REMOVED || s.phase==CMF_FAILED);
        CHECK(f.creates<=1 && f.removes<=1 && s.deleted<=1);
        cmf_surface_tick(&s,10000); CHECK(f.creates<=1 && f.removes<=1);
    }
    memset(&f,0,sizeof(f));f.screen=&f;f.top=&s;
    CHECK(cmf_surface_init(&s,&a,pixels4,16,2,2,100,30,100)==0);
    cmf_surface_tick(&s,130);cmf_surface_tick(&s,129);
    CHECK(s.phase==CMF_REMOVED && f.removes==1);
    CHECK(cmf_surface_init(&s,&a,pixels4,16,2,2,100,30,100)==0);
    cmf_surface_tick(&s,230); CHECK(s.phase==CMF_REMOVED && s.created==0);
    CHECK(cmf_surface_init(&s,&a,NULL,16,2,2,100,30,100)==-1);
    CHECK(cmf_surface_init(&s,&a,pixels4,15,2,2,100,30,100)==-1);
    CHECK(cmf_surface_init(&s,&a,pixels4,16,357,2,100,30,100)==-1);
    CHECK(cmf_surface_init(&s,&a,pixels4,16,2,2,UINT64_MAX-10,30,100)==-1);
    CHECK(cmf_surface_init(&s,&a,pixels4,16,2,2,0,0,10001)==-1);
    CHECK(cmf_surface_init(&s,&a,pixels4,16,2,2,0,90001,1)==-1);
    CHECK(cmf_surface_init(&s,&a,pixels4,16,2,2,0,0,0)==-1);
    printf("{\"checks\":%u,\"failures\":0,\"backend\":\"synthetic\",\"hardware_displayed\":false}\n",checks);
    return 0;
}
