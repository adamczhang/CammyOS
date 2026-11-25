"""Independent control guardian with pipe sinks, including uncertain down writes."""
import json,subprocess,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FLAGS=['-O2','-std=c11','-Wall','-Wextra','-Werror','-Wshadow','-Wconversion']
# Event-driven wait: idle guardian sleeps without timer wakeups, a held key still
# releases at its deadline, and cancel, stop signals and a broken pipe release it.
WAIT_FIXTURE=r'''
#define main portal_unused_main
#include PORTAL_SOURCE
#undef main
#define CHECK(x) do{if(!(x)){fprintf(stderr,"wait fixture line %d\n",__LINE__);exit(1);}}while(0)
struct ev {unsigned button,down;};
static int sink(unsigned b,bool p,void *ctx){struct ev e={b,p?1u:0u};return write(*(int *)ctx,&e,sizeof(e))==(ssize_t)sizeof(e)?0:EIO;}
static pid_t start(int *ch,int *ev){int c[2],s[2];CHECK(!socketpair(AF_UNIX,SOCK_SEQPACKET,0,c));CHECK(!pipe(s));pid_t pid=fork();CHECK(pid>=0);
    if(!pid){close(c[0]);close(s[0]);input_guard_loop(c[1],sink,&s[1]);}
    close(c[1]);close(s[1]);*ch=c[0];*ev=s[0];return pid;}
static bool next(int fd,int ms,struct ev *e){struct pollfd p={fd,POLLIN,0};return poll(&p,1,ms)>0&&read(fd,e,sizeof(*e))==(ssize_t)sizeof(*e);}
static void cmd(int fd,uint64_t seq,unsigned b,unsigned ttl){struct input_message m={seq,(uint64_t)(monotime()*1000.0)+ttl,b,ttl?1u:0u};CHECK(send(fd,&m,sizeof(m),MSG_NOSIGNAL)==(ssize_t)sizeof(m));}
static void reply(int fd,int error,int button){struct input_reply r;CHECK(recv(fd,&r,sizeof(r),0)==(ssize_t)sizeof(r));CHECK(r.error==error&&r.button==button);}
static long switches(pid_t pid){char path[64],line[256];long v=-1;snprintf(path,sizeof(path),"/proc/%d/status",(int)pid);FILE *f=fopen(path,"r");
    while(f&&fgets(line,sizeof(line),f))if(sscanf(line,"voluntary_ctxt_switches: %ld",&v)==1)break;
    if(f)fclose(f);
    return v;}
static bool exits(pid_t pid,int ms){for(int i=0;i<ms;i++){if(waitpid(pid,NULL,WNOHANG)==pid)return true;struct timespec d={0,1000000};nanosleep(&d,NULL);}return false;}
static void pause_ms(long ms){struct timespec d={ms/1000,(ms%1000)*1000000};nanosleep(&d,NULL);}
int main(void){
    int ch,ev;struct ev e;pid_t g=start(&ch,&ev);
    pause_ms(50);long before=switches(g);pause_ms(500);long idle=switches(g)-before;CHECK(before>=0);
    double t0=monotime();cmd(ch,1,0,200);CHECK(next(ev,1000,&e)&&e.button==0&&e.down);reply(ch,0,0);
    CHECK(next(ev,1000,&e)&&e.button==0&&!e.down);double held=monotime()-t0;CHECK(held>=.19&&held<.6);reply(ch,0,-1);
    cmd(ch,2,1,300);CHECK(next(ev,1000,&e)&&e.button==1&&e.down);reply(ch,0,1);
    cmd(ch,3,0,0);CHECK(next(ev,150,&e)&&e.button==1&&!e.down);reply(ch,0,-1);
    CHECK(!kill(g,SIGTERM));CHECK(exits(g,1000));CHECK(!next(ev,10,&e));close(ch);close(ev);
    g=start(&ch,&ev);cmd(ch,1,4,300);CHECK(next(ev,1000,&e)&&e.button==4&&e.down);
    CHECK(!kill(g,SIGTERM));CHECK(next(ev,150,&e)&&e.button==4&&!e.down);CHECK(exits(g,1000));close(ch);close(ev);
    g=start(&ch,&ev);cmd(ch,1,6,300);close(ch);CHECK(next(ev,1000,&e)&&e.button==6&&e.down);
    CHECK(next(ev,150,&e)&&e.button==6&&!e.down);CHECK(exits(g,1000));close(ev);
    printf("{\"idle_switches_500ms\":%ld,\"hold_ms\":%.0f,\"cancel_release\":true,\"signal_release\":true,\"broken_pipe_release\":true}\n",idle,held*1000);return 0;
}
'''
class GuardianTests(unittest.TestCase):
    def commands(self,out):
        return [(['cc'],[],[str(out/'guardian-host')]),
                ([str(ROOT/'build/toolchain/bin/arm-buildroot-linux-musleabihf-gcc')],['-static','-march=armv7-a','-mfpu=vfpv3-d16','-mfloat-abi=hard',str(ROOT/'build/arm/arm_legacy_stat.o')],[str(ROOT.parent/'Cammy Bot/tools/build-deps/usr/bin/qemu-arm'),str(out/'guardian-arm')])]
    def test_host_and_arm_expiry_eof_direction_order_and_failed_write(self):
        out=ROOT/'build/input';out.mkdir(exist_ok=True)
        for cc,extra,runner in self.commands(out):
            with self.subTest(compiler=cc):
                subprocess.run(cc+extra+FLAGS+[str(ROOT/'native/input/guardian_fixture.c'),'-o',runner[-1]],check=True,capture_output=True)
                result=json.loads(subprocess.check_output(runner,timeout=5))
                self.assertTrue(result['parent_eof_release']);self.assertTrue(result['expiry_release']);self.assertFalse(result['physical_motion_verified'])
    def test_host_and_arm_wait_is_event_driven_and_releases(self):
        out=ROOT/'build/input';out.mkdir(exist_ok=True);source=out/'guardian_wait_fixture.c';source.write_text(WAIT_FIXTURE)
        for cc,extra,runner in self.commands(out):
            binary=runner[-1]+'-wait'
            with self.subTest(compiler=cc):
                subprocess.run(cc+extra+FLAGS+['-DPORTAL_SOURCE="'+str(ROOT/'src/cammy_portal.c')+'"',str(source),'-o',binary],check=True,capture_output=True)
                result=json.loads(subprocess.check_output(runner[:-1]+[binary],timeout=10))
                self.assertLessEqual(result['idle_switches_500ms'],3) # a 20 ms poll made about 25
                self.assertTrue(result['broken_pipe_release'])
if __name__=='__main__':unittest.main()
