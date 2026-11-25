/* Offline ALSA config graph inspection only. No PCM/control/device opened. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct _snd_config snd_config_t;
typedef struct _snd_input snd_input_t;
extern int snd_config_top(snd_config_t **);
extern int snd_input_stdio_open(snd_input_t **,const char *,const char *);
extern int snd_config_load(snd_config_t *,snd_input_t *);
extern int snd_input_close(snd_input_t *);
extern int snd_config_search(snd_config_t *,const char *,snd_config_t **);
extern int snd_config_get_string(const snd_config_t *,const char **);
extern int snd_config_get_integer(const snd_config_t *,long *);
extern int snd_config_delete(snd_config_t *);
static int string(snd_config_t *root,const char *path,const char *expected){snd_config_t *c;const char *value;return !snd_config_search(root,path,&c)&&!snd_config_get_string(c,&value)&&!strcmp(value,expected);}
static int integer(snd_config_t *root,const char *path,long expected){snd_config_t *c;long value;return !snd_config_search(root,path,&c)&&!snd_config_get_integer(c,&value)&&value==expected;}
int main(int argc,char **argv){
 if(argc!=2)return 2;
 snd_input_t *input=NULL;snd_config_t *root=NULL;
 if(snd_config_top(&root)<0||snd_input_stdio_open(&input,argv[1],"r")<0)return 2;
 int result=snd_config_load(root,input);snd_input_close(input);if(result<0){snd_config_delete(root);return 2;}
 #define S(p,v) if(!string(root,p,v)){fprintf(stderr,"mismatch %s\n",p);snd_config_delete(root);return 1;}
 #define I(p,v) if(!integer(root,p,v)){fprintf(stderr,"mismatch %s\n",p);snd_config_delete(root);return 1;}
 S("pcm.CammyPlayback.type","plug");S("pcm.CammyPlayback.slave.pcm.type","softvol");
 S("pcm.CammyPlayback.slave.pcm.slave.pcm.type","hooks");
 S("pcm.CammyPlayback.slave.pcm.slave.pcm.slave.pcm.type","dmix");
 S("pcm.CammyPlayback.slave.pcm.slave.pcm.slave.pcm.slave.pcm.type","hw");
 S("timer.hw.type","hw");
 S("pcm.CammyPlayback.slave.pcm.slave.pcm.slave.pcm.slave.format","S16_LE");
 I("pcm.CammyPlayback.slave.pcm.slave.pcm.slave.pcm.ipc_key",1111);
 I("pcm.CammyPlayback.slave.pcm.slave.pcm.slave.pcm.slave.pcm.card",0);
 I("pcm.CammyPlayback.slave.pcm.slave.pcm.slave.pcm.slave.pcm.device",0);
 I("pcm.CammyPlayback.slave.pcm.slave.pcm.slave.pcm.slave.rate",16000);
 I("pcm.CammyPlayback.slave.pcm.slave.pcm.slave.pcm.slave.channels",1);
 I("pcm.CammyPlayback.slave.pcm.slave.pcm.slave.pcm.slave.period_size",960);
 I("pcm.CammyPlayback.slave.pcm.slave.pcm.slave.pcm.slave.periods",8);
 S("pcm.CammyPlayback.slave.pcm.slave.pcm.hooks.0.type","ctl_elems");
 S("pcm.CammyPlayback.slave.pcm.slave.pcm.hooks.0.hook_args.0.name","SPK Switch");
 I("pcm.CammyPlayback.slave.pcm.slave.pcm.hooks.0.hook_args.0.value",1);
 S("pcm.CammyPlayback.slave.pcm.control.name","Soft Volume Master");
 snd_config_t *unexpected;
 if(!snd_config_search(root,"@hooks",&unexpected)||!snd_config_search(root,"pcm.cammy_shared",&unexpected)){snd_config_delete(root);return 1;}
 snd_config_delete(root);puts("{\"config_graph_verified\":true,\"device_opened\":false,\"hardware_audio_verified\":false}");return 0;
}
