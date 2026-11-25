/* SPDX-License-Identifier: MIT. CPK1: a lossless packed face bank (tools/cammy_pack.py).
 * Header-only and free of libc and division, so it links into the in-process face library
 * and its host fixtures alike. Every length and offset is checked before it is used. */
#ifndef CAMMY_FACE_PACK_CODEC_H
#define CAMMY_FACE_PACK_CODEC_H
#include <stdint.h>
#include <stddef.h>
#define CPK_MAGIC 0x314b5043u /* "CPK1" */
#define CPK_VERSION 1u
#define CPK_W 356u
#define CPK_H 400u
#define CPK_BGR_BYTES (CPK_W*CPK_H*3u)
#define CPK_BGRA_BYTES (CPK_W*CPK_H*4u)
#define CPK_HEAD_BYTES 32u
#define CPK_ENTRY_BYTES 16u
#define CPK_NONE 0xffffffffu
enum { CPK_KEY_SUB=1, CPK_KEY_UP=2, CPK_DELTA=3 };
/* Checked adult bank size, including five awake moods, shared blink, speech and sleep. */
#define CPK_FRAMES 16u
/* One decoded key plane, kept between calls so deltas cost one LZ pass. */
struct cpk_work { uint32_t key_index; const unsigned char *bank; unsigned char key[CPK_BGR_BYTES]; };
static inline uint32_t cpk_u32(const unsigned char *p) {
    return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24;
}
static inline uint32_t cpk_field(const unsigned char *b,uint32_t index,unsigned word) {
    return cpk_u32(b+CPK_HEAD_BYTES+index*CPK_ENTRY_BYTES+word*4u);
}
static inline int cpk_is_key(uint32_t filter) { return filter==CPK_KEY_SUB || filter==CPK_KEY_UP; }
/* 0 when the complete bank contains the expected number of valid entries. */
static inline int cpk_check_count(const unsigned char *b,size_t n,uint32_t count) {
    if(!b || n<CPK_HEAD_BYTES || n>0x7fffffffu)return -1;
    if(cpk_u32(b)!=CPK_MAGIC || cpk_u32(b+4)!=CPK_VERSION || cpk_u32(b+8)!=CPK_W || cpk_u32(b+12)!=CPK_H ||
       cpk_u32(b+16)!=count || cpk_u32(b+20)!=(uint32_t)n)return -1;
    uint32_t table=CPK_HEAD_BYTES+count*CPK_ENTRY_BYTES;
    if(n<table)return -1;
    for(uint32_t i=0;i<count;i++) {
        uint32_t off=cpk_field(b,i,0),len=cpk_field(b,i,1),filter=cpk_field(b,i,2),ref=cpk_field(b,i,3);
        if(off<table || !len || off>(uint32_t)n || len>(uint32_t)n-off)return -1;
        if(cpk_is_key(filter)){if(ref!=CPK_NONE)return -1;}
        else if(filter!=CPK_DELTA || ref>=i || !cpk_is_key(cpk_field(b,ref,2)))return -1;
    }
    return 0;
}
/* LZ4-style block into exactly cap bytes of out. -1 on any malformed or short input. */
static inline int cpk_check(const unsigned char *b,size_t n) {
    return cpk_check_count(b,n,CPK_FRAMES);
}
static inline int cpk_lz(const unsigned char *in,uint32_t n,unsigned char *out,uint32_t cap) {
    uint32_t p=0,o=0;
    for(;;) {
        if(p>=n)return -1;
        uint32_t t=in[p++],lit=t>>4;
        if(lit==15u)for(;;){if(p>=n)return -1;uint32_t x=in[p++];lit+=x;if(lit>cap)return -1;if(x!=255u)break;}
        if(lit>n-p || lit>cap-o)return -1;
        for(uint32_t i=0;i<lit;i++)out[o+i]=in[p+i];
        p+=lit;o+=lit;
        if(p==n)return o==cap?0:-1;
        if(n-p<2u)return -1;
        uint32_t off=(uint32_t)in[p]|(uint32_t)in[p+1]<<8;p+=2;
        uint32_t m=(t&15u)+4u;
        if((t&15u)==15u)for(;;){if(p>=n)return -1;uint32_t x=in[p++];m+=x;if(m>cap)return -1;if(x!=255u)break;}
        if(!off || off>o || m>cap-o)return -1;
        for(uint32_t i=0;i<m;i++,o++)out[o]=out[o-off];
    }
}
static inline void cpk_unfilter(unsigned char *r,uint32_t filter) {
    uint32_t step=filter==CPK_KEY_SUB?3u:CPK_W*3u;
    for(uint32_t i=step;i<CPK_BGR_BYTES;i++)r[i]=(unsigned char)(r[i]+r[i-step]);
}
static inline int cpk_plane(const unsigned char *b,uint32_t index,unsigned char *dst) {
    uint32_t off=cpk_field(b,index,0),len=cpk_field(b,index,1),filter=cpk_field(b,index,2);
    if(cpk_lz(b+off,len,dst,CPK_BGR_BYTES))return -1;
    if(cpk_is_key(filter))cpk_unfilter(dst,filter);
    return 0;
}
/* Decode frame index of a checked bank into out (CPK_BGRA_BYTES, BGRA8888, alpha 255,
 * exactly what bgra_preview makes). The packed plane is decoded into the tail of out and
 * widened forward in place: pixel i writes out[4i..4i+3] after reading its three bytes at
 * tail+3i, and 4i+3 < tail+3(i+1) for every i, so nothing unread is overwritten. */
static inline int cpk_decode(const unsigned char *b,uint32_t index,struct cpk_work *w,unsigned char *out) {
    if(index>=cpk_u32(b+16))return -1;
    if(w->bank!=b){w->bank=b;w->key_index=CPK_NONE;}
    uint32_t filter=cpk_field(b,index,2),ref=cpk_field(b,index,3);
    unsigned char *tail=out+(CPK_BGRA_BYTES-CPK_BGR_BYTES);const unsigned char *src=tail,*key=NULL;
    if(filter==CPK_DELTA) {
        if(w->key_index!=ref){w->key_index=CPK_NONE;if(cpk_plane(b,ref,w->key))return -1;w->key_index=ref;}
        if(cpk_lz(b+cpk_field(b,index,0),cpk_field(b,index,1),tail,CPK_BGR_BYTES))return -1;
        key=w->key;
    } else if(w->key_index==index) src=w->key;
    else if(cpk_plane(b,index,tail))return -1;
    for(uint32_t i=0,j=0;i<CPK_BGRA_BYTES;i+=4u,j+=3u) {
        unsigned char x=src[j],y=src[j+1],z=src[j+2];
        if(key){x=(unsigned char)(x+key[j]);y=(unsigned char)(y+key[j+1]);z=(unsigned char)(z+key[j+2]);}
        out[i]=x;out[i+1]=y;out[i+2]=z;out[i+3]=0xffu;
    }
    return 0;
}
#endif
