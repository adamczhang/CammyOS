"""Install reversible PC-only user service and supported Codex stdio registration.

No camera contact, audio playback, T3 thread creation/message or firmware action.
"""
import argparse,json,os,shutil,sys,subprocess,tomllib,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def install():
    subprocess.run(['findmnt','-n',os.environ.get('CAMMY_WORK_MOUNT', '/')],check=True,stdout=subprocess.DEVNULL)
    out=ROOT/'private/agent-control';out.mkdir(exist_ok=True,mode=0o700)
    python=out/'venv/bin/python';assert python.is_file()
    source=ROOT/'scripts/systemd/cammy-agent.service';target=Path.home()/'.config/systemd/user/cammy-agent.service'
    sys.path.insert(0,str(ROOT/'tools/c3'));from install_units import render
    unit=render(source.read_text(),root=ROOT,mount=os.environ.get('CAMMY_WORK_MOUNT'))
    if target.exists() and target.read_text()!=unit:raise ValueError('Different existing agent unit; inspect before replacing')
    target.parent.mkdir(parents=True,exist_ok=True);target.write_text(unit);target.chmod(0o644)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    subprocess.run(['systemctl','--user','enable','--now','cammy-agent.service'],check=True,capture_output=True)
    config=Path.home()/'.codex/config.toml';before=tomllib.loads(config.read_text());servers=before.get('mcp_servers',{})
    expected={'command':str(python),'args':[str(ROOT/'host/cammy_mcp.py')]}
    if 'cammy' in servers:
        if any(servers['cammy'].get(k)!=v for k,v in expected.items()):raise ValueError('Different existing Cammy MCP registration')
    else:
        backup=out/'codex-config.before-cammy.toml'
        if not backup.exists():shutil.copyfile(config,backup);backup.chmod(0o600)
        subprocess.run(['codex','mcp','add','cammy','--',str(python),str(ROOT/'host/cammy_mcp.py')],check=True,capture_output=True)
    raw=config.read_text();after=tomllib.loads(raw)
    if 'env_vars' not in after['mcp_servers']['cammy']:
        needle='[mcp_servers.cammy]\n'
        if raw.count(needle)!=1:raise ValueError('MCP config section differs')
        raw=raw.replace(needle,needle+'env_vars = ["T3CODE_THREAD_ID"]\n');tomllib.loads(raw)
        fd,name=tempfile.mkstemp(prefix='.cammy-',suffix='.toml',dir=config.parent)
        temporary=Path(name)
        try:
            with os.fdopen(fd,'w') as f:os.fchmod(f.fileno(),0o600);f.write(raw);f.flush();os.fsync(f.fileno())
            os.replace(temporary,config)
        finally:temporary.unlink(missing_ok=True)
    after=tomllib.loads(config.read_text())
    for name,value in servers.items():
        if name!='cammy' and after['mcp_servers'][name]!=value:raise ValueError('Unrelated MCP server configuration changed')
    assert after['mcp_servers']['cammy']['env_vars']==['T3CODE_THREAD_ID']
    return {'user_service':'cammy-agent.service','enabled':True,'mcp_registered':'cammy','audio_policy':'quiet until user resumes','camera_activated':False}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true',required=True);p.parse_args();print(json.dumps(install(),indent=2))
