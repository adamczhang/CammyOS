#!/usr/bin/env python3
"""Generate the agent-neutral OpenAPI description from this version's contract."""
import json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
paths={}
for path,description in {
'/health':'Unauthenticated service liveness; no device observations.',
'/v1/diagnostics':'Compatibility metadata via the supervised worker;200 retains v0.2 shape,409 busy,503 cancelled/failed/timeout.',
'/v1/audio/stream/status':'Opt-in stream state, child lifecycle and explicit physical-cleanup limitation; does not acquire media.',
'/v1/preview/status':'Event connection, bounded RTSP/SDP observation and age; frame freshness is explicitly unverified.',
'/v1/identity':'Image marker and portal identity; does not claim a new kernel boot.',
'/v1/capabilities':'Actual implemented adapters; clients must inspect availability.',
'/v1/state':'Control owner, remaining lease, preview state and event sequence.',
'/v1/events':'Bounded recent event ring; clients detect gaps using sequence numbers.'}.items():
 op={'summary':description,'operationId':path.strip('/').replace('/','_'),'responses':{'200':{'description':'JSON response'}}}
 if path=='/health':op['security']=[]
 paths[path]={'get':op}
for path,description in {
'/v1/audio/stream':'Explicit owned-session raw PCM stream; requires opt-in shared profile and connected preview. Ends on close, Stop, lease expiry or 600s.',
'/v1/session':'Acquire or renew an agent control lease. Returns409 if another agent owns it.',
'/v1/session/release':'Release this agent\'s lease and stop preview.',
'/v1/preview/start':'Accept nonblocking event startup and bounded SDP check;202 is not readiness. Requires stock backend flag.',
'/v1/preview/check':'Restart a bounded fixed-endpoint SDP check on the owned connected event session; does not renew lease.',
'/v1/preview/stop':'Close the stock event connection.',
'/v1/stop':'Any authenticated agent may stop preview, cancel an active metadata probe and release control.',
'/v1/face':'Reserved face adapter; currently501.',
'/v1/look':'Reserved gimbal adapter; currently501.',
'/v1/speak':'Reserved speaker adapter; currently501.',
'/v1/audio/capture':'Reserved microphone adapter; currently501.'}.items():
 schema={'type':'object','additionalProperties':False}
 if path=='/v1/session':schema.update(required=['agent'],properties={'agent':{'type':'string','pattern':'^[A-Za-z0-9_.-]{1,48}$'},'ttl':{'type':'integer','minimum':1,'maximum':120,'default':30}})
 if path in ('/v1/face','/v1/look','/v1/speak','/v1/audio/capture'):schema={'type':'object','description':'Adapter not implemented; no actuation occurs.'}
 paths[path]={'post':{'summary':description,'operationId':path.strip('/').replace('/','_'),'parameters':[{'name':'X-Agent-ID','in':'header','schema':{'type':'string'},'required':path not in ('/v1/session','/v1/stop')}],'requestBody':{'required':True,'content':{'application/json':{'schema':schema}}},'responses':{str(code):{'description':message} for code,message in [(200,'Success'),(400,'Invalid request'),(401,'Invalid bearer credential'),(409,'Control lease conflict'),(501,'Adapter not implemented/enabled'),(503,'Backend unavailable')]}}}
paths['/v1/audio/stream']['post']['responses']['200']={'description':'Live mono16kHz S16_LE PCM, close-delimited; no TF recording and no automatic lease renewal.', 'headers':{key:{'schema':{'type':'string','enum':[value]}} for key,value in {'X-Cammy-Audio-Format':'S16_LE','X-Cammy-Audio-Rate':'16000','X-Cammy-Audio-Channels':'1'}.items()},'content':{'application/octet-stream':{'schema':{'type':'string','format':'binary'}}}}
# The source contract is v0.3; an installed v0.2 body returns404 for these routes.
paths['/v1/probes']={
 'get':{'summary':'Catalogue of fixed read-only metadata collectors and supervisor availability.',
        'operationId':'v1_probes_catalogue','responses':{'200':{'description':'Catalogue; no collection starts'}}},
 'post':{'summary':'Start one serialized metadata job; no control lease or sensing.',
         'operationId':'v1_probes_start',
         'parameters':[{'name':'X-Agent-ID','in':'header','required':True,'schema':{'type':'string','pattern':'^[A-Za-z0-9_.-]{1,48}$'}}],
         'requestBody':{'required':True,'content':{'application/json':{'schema':{'type':'object','additionalProperties':False,'required':['probe'],'properties':{'probe':{'type':'string','enum':['runtime','network-bindings','display-layout','audio-state']}}}}}},
         'responses':{'202':{'description':'Job accepted; poll job_id'},'400':{'description':'Unknown probe or invalid arguments'},'409':{'description':'Worker busy or awaiting reap'},'503':{'description':'Worker unavailable'}}}}
job_parameter={'name':'job_id','in':'path','required':True,'schema':{'type':'string','pattern':'^job-[1-9][0-9]{0,19}$'}}
paths['/v1/probes/{job_id}']={'get':{'summary':'Read one of the two retained jobs; result may contain missing or truncated sources.',
 'operationId':'v1_probes_status','parameters':[job_parameter],
 'responses':{'200':{'description':'running/completed/failed/timed_out/cancelled with monotonic timing'},'404':{'description':'Unknown or evicted job'}}}}
paths['/v1/probes/{job_id}/cancel']={'post':{"summary":"Cancel the matching job owner's work. Labels are cooperative, not separate credentials.",
 'operationId':'v1_probes_cancel','parameters':[job_parameter,{'name':'X-Agent-ID','in':'header','required':True,'schema':{'type':'string'}}],
 'requestBody':{'required':True,'content':{'application/json':{'schema':{'type':'object','additionalProperties':False}}}},
 'responses':{'200':{'description':'Terminal job status; already finished jobs unchanged'},'409':{'description':'Job owner required'},'404':{'description':'Unknown or evicted job'}}}}
paths['/v1/preview/start']['post']['responses']['202']={'description':'Startup accepted; poll /v1/preview/status'}
paths['/v1/diagnostics']['get']['responses'].update({'409':{'description':'Serialized metadata worker busy'},'503':{'description':'Cancelled, failed or timed out'}})
spec={'openapi':'3.0.3','info':{'title':'CammyOS Agent Portal','version':(root/'VERSION').read_text().strip(),'description':'Agent-neutral robot access. Open protocol; authenticated operations, no arbitrary shell API.'},'servers':[{'url':'http://127.0.0.1:8787'}],'security':[{'bearerAuth':[]}],'components':{'securitySchemes':{'bearerAuth':{'type':'http','scheme':'bearer'}}},'paths':paths}
for p in (root/'docs/openapi.json',root/'overlay/usr/share/cammy/openapi.json'):p.write_text(json.dumps(spec,indent=2)+'\n')
