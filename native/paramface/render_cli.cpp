/* SPDX-License-Identifier: MIT. Offline/RAM RGB renderer; no display device I/O. */
#include "cammy_paramface.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
int main(int argc,char **argv){
 if(argc!=6){std::fprintf(stderr,"usage: renderer FACE_JSON EXPRESSION WIDTH HEIGHT TICKS\n");return 2;}
 char *end=nullptr;long values[4];
 for(int i=0;i<4;i++){values[i]=std::strtol(argv[i+2],&end,10);if(!*argv[i+2]||*end)return 2;}
 if(values[0]<0||values[0]>5||values[1]<16||values[1]>1024||values[2]<16||values[2]>1024||values[3]<0||values[3]>1800)return 2;
 FILE *f=std::fopen(argv[1],"rb");if(!f)return 1;
 char json[65538];size_t n=std::fread(json,1,sizeof(json),f);int bad=std::ferror(f);std::fclose(f);
 if(bad||n>65536)return 1;
 void *ctx=cm_pf_create(static_cast<int>(values[1]),static_cast<int>(values[2]));if(!ctx)return 1;
 std::vector<uint8_t> rgb(static_cast<size_t>(values[1]*values[2]*3));
 int rc=cm_pf_load(ctx,json,n);if(!rc)rc=cm_pf_expression(ctx,static_cast<int>(values[0]));
 for(long i=0;i<values[3]&&!rc;i++)rc=cm_pf_frame(ctx,33.333f,rgb.data(),rgb.size());
 if(!rc)rc=cm_pf_frame(ctx,0,rgb.data(),rgb.size());
 if(!rc){std::printf("P6\n%ld %ld\n255\n",values[1],values[2]);if(std::fwrite(rgb.data(),1,rgb.size(),stdout)!=rgb.size())rc=-1;}
 cm_pf_destroy(ctx);return rc?1:0;
}
