// T3 destinations share the PC agent service; no T3/provider credentials in page.
const el=id=>document.getElementById(id);
const auth={'X-Cammy-Lab':document.querySelector('main').dataset.token,'Content-Type':'application/json'};
let selected=null,session=null,generation=0,lastRequest=null,frameURL=null,submitting=false;
const show=value=>{el('agent-status').textContent=typeof value==='string'?value:JSON.stringify(value,null,2)};
function target(){const value=el('agent-thread').value.trim();if(!/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value))throw Error('Enter an existing T3 thread UUID');return value}
async function request(operation,args={},bound=session,thread=selected){
  if(!thread)throw Error('Connect a thread first');
  const r=await fetch('/api/agent/'+operation,{method:'POST',headers:auth,body:JSON.stringify({thread_id:thread,session:bound,arguments:args})});
  if(!r.ok)throw Error((await r.text()).slice(0,250));return r;
}
async function json(operation,args={},bound=session,thread=selected){return (await request(operation,args,bound,thread)).json()}
async function action(fn){try{await fn()}catch(e){show(e.message)}}
function clearFrame(){if(frameURL)URL.revokeObjectURL(frameURL);frameURL=null;el('agent-frame').removeAttribute('src')}
function same(epoch,thread){return epoch===generation&&thread===selected}
async function update(epoch,thread){
  if(!same(epoch,thread)||!session)return;
  try{
    const state=await json('status');if(!same(epoch,thread))return;
    show(state);
    if(state.owned_session!==session||!state.busy){session=null;clearFrame();return}
    if(state.session?.phase==='live'){
      const r=await request('frame');const blob=await r.blob();if(!same(epoch,thread))return;
      const old=frameURL;frameURL=URL.createObjectURL(blob);el('agent-frame').src=frameURL;if(old)URL.revokeObjectURL(old);
    }
  }catch(e){if(same(epoch,thread))show(e.message)}
  if(same(epoch,thread)&&session)setTimeout(()=>update(epoch,thread),500);
}
async function pollReply(id,epoch,thread){
  if(!same(epoch,thread)||lastRequest!==id)return;
  try{
    const value=await json('thread-result',{request_id:id});if(!same(epoch,thread)||lastRequest!==id)return;
    el('agent-reply').textContent=value.reply||JSON.stringify(value,null,2);
    if(value.phase==='completed'&&value.playback_allowed&&el('agent-auto-speak').checked){
      const speech=await json('thread-speak',{request_id:id});if(same(epoch,thread))el('agent-reply').textContent+= '\n'+JSON.stringify(speech,null,2);
    }
    if(['completed','failed','delivery-uncertain','waiting','completed-reply-unavailable'].includes(value.phase))return;
  }catch(e){if(same(epoch,thread))show(e.message);return}
  setTimeout(()=>pollReply(id,epoch,thread),1500);
}
el('agent-select').onclick=()=>action(async()=>{
  if(session)throw Error('Stop the current Cammy session before switching threads');
  const thread=target();const value=await json('thread-select',{},null,thread);
  generation++;selected=thread;lastRequest=null;clearFrame();el('agent-reply').textContent='No thread reply requested.';show(value);
});
el('agent-open').onclick=()=>action(async()=>{
  if(target()!==selected)throw Error('Connect the chosen thread first');
  const value=await json('open',{seconds:60,sensing:true});session=value.owned_session;
  const epoch=++generation;show(value);update(epoch,selected);
});
el('agent-stop').onclick=()=>action(async()=>{
  if(!session)throw Error('No session opened by this panel');
  const oldSession=session,thread=selected;generation++;session=null;lastRequest=null;clearFrame();
  show(await json('stop',{},oldSession,thread));
});
async function submit(voice){
  if(submitting)throw Error('An utterance is being captured or delivered');
  if(!session||target()!==selected)throw Error('Start a session for the connected thread first');
  const id=crypto.randomUUID().replaceAll('-',''),epoch=generation,thread=selected;lastRequest=id;
  const text=el('agent-text').value.trim();if(!voice&&!text)throw Error('Enter a message');
  show(voice?'Listening for three seconds; transcription stays local.':'Sending once to the selected T3 thread.');
  submitting=true;
  try{
    const value=await json(voice?'voice-turn':'thread-send',voice?{seconds:3,request_id:id}:{text,request_id:id});
    if(!same(epoch,thread))return;el('agent-reply').textContent=JSON.stringify(value,null,2);pollReply(id,epoch,thread);
  }finally{submitting=false}
}
el('agent-listen').onclick=()=>action(()=>submit(true));
el('agent-send').onclick=()=>action(()=>submit(false));
window.addEventListener('pagehide',()=>{generation++;clearFrame()});
