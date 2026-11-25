/* SPDX-License-Identifier: MIT. CPK1 decoder fixture: a bank against its reference frames
 * (tools/cammy_pack.py decode), the slot/key reuse order the face library uses, and
 * mutated banks that must be rejected or decode without reading or writing out of bounds.
 * Usage: pack-fixture BANK REFERENCE | pack-fixture --reject BANK */
#define _POSIX_C_SOURCE 200809L
#include "face_pack_codec.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
static unsigned char *slurp(const char *p,size_t *n){
    FILE *f=fopen(p,"rb");if(!f)exit(2);
    if(fseek(f,0,SEEK_END))exit(2);
    long size=ftell(f);
    if(size<0)exit(2);
    rewind(f);*n=(size_t)size;
    unsigned char *b=malloc(*n?*n:1);if(!b||fread(b,1,*n,f)!=*n)exit(2);fclose(f);return b;}
static double ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (double)t.tv_sec*1e3+(double)t.tv_nsec/1e6;}
static struct cpk_work w;static unsigned char out[CPK_BGRA_BYTES];
int main(int argc,char **argv){
    size_t n,m;
    if(argc==3&&!strcmp(argv[1],"--reject")){unsigned char *b=slurp(argv[2],&n);int r=cpk_check(b,n);free(b);printf("{\"rejected\":%s}\n",r?"true":"false");return 0;}
    if(argc==4&&!strcmp(argv[1],"--children")){
        unsigned char *b=slurp(argv[2],&n),*ref=slurp(argv[3],&m);
        unsigned count=(unsigned)(m/CPK_BGRA_BYTES);
        if(!count || count>64u || cpk_check_count(b,n,count)||m!=(size_t)count*CPK_BGRA_BYTES)return 1;
        for(unsigned i=0;i<count;i++){
            if(cpk_decode(b,i,&w,out)||memcmp(out,ref+(size_t)i*CPK_BGRA_BYTES,CPK_BGRA_BYTES))return 1;
        }
        free(b);free(ref);
        return 0;
    }
    if(argc!=3)return 2;
    unsigned char *b=slurp(argv[1],&n),*ref=slurp(argv[2],&m);
    if(cpk_check(b,n)||m!=(size_t)CPK_FRAMES*CPK_BGRA_BYTES){puts("{\"error\":\"check\"}");return 1;}
    w.key_index=CPK_NONE;
    static const unsigned order[]={0,5,0,1,5,1,6,7,6,7,8,9,10,11,12,13,14,15,8,9,10,11,12,13,14,15,2,3,4,5,5,15,0};
    unsigned count=(unsigned)(sizeof order/sizeof *order);double worst=0,total=0;
    for(unsigned k=0;k<count;k++){unsigned i=order[k];double t=ms();
        if(cpk_decode(b,i,&w,out)){printf("{\"error\":\"decode %u\"}\n",i);return 1;}
        double d=ms()-t;total+=d;if(d>worst)worst=d;
        if(memcmp(out,ref+(size_t)i*CPK_BGRA_BYTES,CPK_BGRA_BYTES)){printf("{\"error\":\"frame %u\"}\n",i);return 1;}}
    unsigned char *c=malloc(n);unsigned rejected=0,decoded=0,mutations=3000;if(!c)return 2;srand(7);
    for(unsigned t=0;t<mutations;t++){memcpy(c,b,n);size_t len=n;
        if(t%3==0)len=(size_t)rand()%n;
        else for(unsigned k=0;k<1+t%5;k++){size_t at=t%2?(size_t)rand()%(CPK_HEAD_BYTES+CPK_FRAMES*CPK_ENTRY_BYTES):(size_t)rand()%n;c[at]^=(unsigned char)(1+rand()%255);}
        if(cpk_check(c,len)){rejected++;continue;}
        w.key_index=CPK_NONE;for(unsigned i=0;i<CPK_FRAMES;i++)if(!cpk_decode(c,i,&w,out))decoded++;}
    printf("{\"frames_checked\":%u,\"mean_ms\":%.2f,\"worst_ms\":%.2f,\"mutations\":%u,\"rejected\":%u,\"mutated_frames_decoded\":%u}\n",count,total/count,worst,mutations,rejected,decoded);
    free(c);free(b);free(ref);return 0;}
