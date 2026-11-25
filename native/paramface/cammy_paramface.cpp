/* SPDX-License-Identifier: MIT. Adapter only; upstream core remains MIT. */
#include "cammy_paramface.h"
#include <ParamFace.h>
#include <cmath>
#include <cstring>
#include <new>
#include <string>
struct Context {paramface::ParamFace face;M5Canvas canvas;bool loaded=false;};
extern "C" {
void *cm_pf_create(int w,int h){
 if(w<16||h<16||w>1024||h>1024)return nullptr;
 try{Context *c=new Context();if(!c->canvas.createSprite(w,h)){delete c;return nullptr;}return c;}catch(...){return nullptr;}
}
void cm_pf_destroy(void *p){delete static_cast<Context *>(p);}
int cm_pf_load(void *p,const char *json,size_t length){
 if(!p||!json||length<2||length>65536||std::memchr(json,0,length))return -1;
 try{Context *c=static_cast<Context *>(p);std::string owned(json,length);if(!c->face.load(owned.c_str()))return -1;c->loaded=true;return 0;}catch(...){return -1;}
}
int cm_pf_expression(void *p,int e){if(!p||e<0||e>=paramface::kExpressionCount)return -1;static_cast<Context *>(p)->face.setExpression(static_cast<paramface::Expression>(e));return 0;}
int cm_pf_drive(void *p,int me,float m,int ge,float h,float v){
 if(!p||!std::isfinite(m)||!std::isfinite(h)||!std::isfinite(v)||m<0||m>1||h< -1||h>1||v< -1||v>1)return -1;
 auto &f=static_cast<Context *>(p)->face;f.setMouthOpenOverride(me!=0,m);f.setGazeOverride(ge!=0,h,v);return 0;
}
int cm_pf_frame(void *p,float dt,uint8_t *rgb,size_t capacity){
 if(!p||!rgb||!std::isfinite(dt)||dt<0||dt>250)return -1;
 Context *c=static_cast<Context *>(p);if(!c->loaded)return -1;
 const size_t n=static_cast<size_t>(c->canvas.w)*static_cast<size_t>(c->canvas.h);
 if(capacity<n*3)return -1;
 try{c->face.tick(dt);c->face.render(&c->canvas);for(size_t i=0;i<n;i++){const auto v=c->canvas.buf[i];rgb[3*i]=static_cast<uint8_t>(((v>>11)&31)*255/31);rgb[3*i+1]=static_cast<uint8_t>(((v>>5)&63)*255/63);rgb[3*i+2]=static_cast<uint8_t>((v&31)*255/31);}return 0;}catch(...){return -1;}
}
const char *cm_pf_error(void *p){return p?static_cast<Context *>(p)->face.lastError():"no renderer";}
}
