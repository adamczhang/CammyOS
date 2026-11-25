const $=id=>document.getElementById(id),token=document.querySelector('main').dataset.token;
const headers={'X-Cammy-Lab':token,'Content-Type':'application/json'};
async function api(path,body){const r=await fetch(path,{headers,method:body===undefined?'GET':'POST',body:body===undefined?undefined:JSON.stringify(body)});if(!r.ok)throw Error((await r.text()).slice(0,150));return r.json()}
async function action(fn){try{await fn()}catch(e){$('status').textContent=e.message}}
const status=await (await fetch('/status')).json();
for(const name of status.presets){const b=document.createElement('button');b.textContent=name;b.onclick=()=>action(()=>api('/api/face/preset/'+name,{}));$('presets').append(b)}
for(const name of status.expressions){const b=document.createElement('button');b.textContent=name;b.onclick=()=>action(()=>fetch('/face?expr='+name+'&hold=1'));$('expressions').append(b)}
$('mouth').oninput=()=>action(()=>api('/api/face/drive',{mouth:Number($('mouth').value),gaze:[Number($('gaze').value),0]}));$('gaze').oninput=$('mouth').oninput;
$('auto-face').onclick=()=>action(()=>api('/api/face/drive',{}));
$('idle-start').onclick=()=>action(()=>api('/api/face/idle',{enabled:true}));
$('idle-stop').onclick=()=>action(()=>api('/api/face/idle',{enabled:false}));
$('start').onclick=()=>action(()=>api('/api/live/start',{seconds:30}));$('stop').onclick=()=>action(()=>api('/api/live/stop',{}));
let frameURL=null,faceURL=null;
async function image(path,id,secured){const r=await fetch(path,secured?{headers}:{});if(!r.ok)return false;const u=URL.createObjectURL(await r.blob());$(id).src=u;const old=id==='face'?faceURL:frameURL;if(id==='face')faceURL=u;else frameURL=u;if(old)URL.revokeObjectURL(old);return true}
async function faceTick(){try{await image('/face.png','face',false)}catch{}setTimeout(faceTick,100)}faceTick();
async function liveTick(){try{const s=await api('/api/live/status');$('evidence').textContent=JSON.stringify(s,null,2);$('status').textContent=s.phase+' · '+s.frames+' frames · '+s.audio_samples+' audio samples · '+s.seconds_left+' seconds left'+(s.error?' · '+s.error:'');$('level').value=s.phase==='live'&&s.audio_age_seconds<2?(s.audio_rms_dbfs??-90):-90;$('start').disabled=['starting','live','stopping'].includes(s.phase);if(s.phase==='live'&&await image('/api/live/frame.jpg','camera',true)){$('empty').hidden=true}else{$('camera').removeAttribute('src');if(frameURL){URL.revokeObjectURL(frameURL);frameURL=null}$('empty').hidden=false;$('empty').textContent=s.phase==='idle'?'Idle · camera and microphone not requested':s.phase}}catch(e){$('status').textContent='App connection lost';$('camera').removeAttribute('src');if(frameURL){URL.revokeObjectURL(frameURL);frameURL=null}$('empty').hidden=false;$('level').value=-90}setTimeout(liveTick,200)}liveTick();
let conversation=null,held=null;
$('conversation-start').onclick=()=>action(async()=>{conversation=await api('/api/conversation/start',{seconds:60,speaker:$('conversation-speaker').checked})});
$('conversation-stop').onclick=()=>action(async()=>{if(conversation?.session)conversation=await api('/api/conversation/stop',{session:conversation.session})});
$('conversation-listen').onpointerdown=e=>{e.preventDefault();if(held||!conversation?.session)return;$('conversation-listen').setPointerCapture(e.pointerId);held=api('/api/conversation/listen',{session:conversation.session,seconds:8});held.catch(e=>$('conversation-status').textContent=e.message)};
async function releaseListen(){if(!held)return;const pending=held;held=null;await action(async()=>{const state=await pending;conversation=await api('/api/conversation/finish',{session:state.session})})}
$('conversation-listen').onpointerup=releaseListen;$('conversation-listen').onpointercancel=releaseListen;window.addEventListener('blur',releaseListen);
$('conversation-send').onclick=()=>action(async()=>{if(!conversation?.inbox)throw Error('No current agent turn');conversation=await api('/api/conversation/reply',{session:conversation.session,turn:conversation.inbox.turn,text:$('conversation-reply').value});$('conversation-reply').value=''});
async function conversationTick(){try{conversation=await api('/api/conversation/status');$('conversation-status').textContent=conversation.phase+' · '+conversation.seconds_left+'s · replies: '+conversation.speaker+(conversation.error?' · '+conversation.error:'');$('conversation-turn').textContent=conversation.inbox?.text??conversation.reply_text??'';$('conversation-start').disabled=conversation.phase!=='idle'||conversation.worker_cleanup_pending||conversation.media_cleanup_pending;$('conversation-listen').disabled=!['ready','awaiting-agent'].includes(conversation.phase)&&conversation.phase!=='listening';$('conversation-send').disabled=conversation.phase!=='awaiting-agent'}catch{$('conversation-status').textContent='App connection lost';$('conversation-turn').textContent='';$('conversation-listen').disabled=true}setTimeout(conversationTick,200)}conversationTick();

// Native input intents capture a generation at pointer-down. A queued renewal
// from before Stop cannot open a new control lease afterward.
let nativeEpoch=status.native_epoch,nativeSequence=Math.max(0,...Object.values(status.native_sequence??{})),panHeld=null,holdBusy=false,holdTimer=null;
for(const mood of status.expressions){const option=document.createElement('option');option.value=mood;option.textContent=mood;$('native-mood').append(option)}
async function nativeAction(path,body,epoch=nativeEpoch){try{const result=await api(path,{...body,epoch,sequence:++nativeSequence});$('native-status').textContent=result.queued?'Queued; check Cammy for the physical result.':'Control accepted';return result}catch(e){$('native-status').textContent=e.message;throw e}}
$('native-mood').value='happy';
$('native-replay').onclick=()=>{nativeAction('/api/native-face',{expression:$('native-mood').value,action:'replay'}).catch(()=>{})};
$('native-idle').onclick=()=>{nativeAction('/api/native-face',{action:'idle'}).catch(()=>{})};
$('native-hide').onclick=()=>{nativeAction('/api/native-face',{action:'hide'}).catch(()=>{})};
async function releasePan(){const previous=panHeld;panHeld=null;clearInterval(holdTimer);holdTimer=null;if(previous)await nativeAction('/api/native-input',{action:'release'},previous.epoch).catch(()=>{})}
function beginPan(button,event){if(panHeld)return;const press={button,epoch:nativeEpoch,started:performance.now()};panHeld=press;event.preventDefault();event.target.setPointerCapture?.(event.pointerId);async function renew(){if(panHeld!==press||holdBusy)return;if(performance.now()-press.started>10000){releasePan();return}holdBusy=true;try{await nativeAction('/api/native-input',{button,action:'hold'},press.epoch)}catch{releasePan()}finally{holdBusy=false}}renew();holdTimer=setInterval(renew,100)}
for(const [id,button] of [['pan-left','left'],['pan-right','right']]){const element=$(id);element.onpointerdown=e=>beginPan(button,e);element.onpointerup=releasePan;element.onpointercancel=releasePan;element.onlostpointercapture=releasePan}
window.addEventListener('blur',releasePan);document.addEventListener('visibilitychange',()=>{if(document.hidden)releasePan()});
$('turn-around').onclick=()=>{nativeAction('/api/native-input',{button:'turn_180',action:'tap'}).catch(()=>{})};
$('native-stop').onclick=async()=>{panHeld=null;clearInterval(holdTimer);holdTimer=null;try{await api('/api/native-stop',{});$('native-status').textContent='Stop requested; physical cleanup is not inferred.'}catch(e){$('native-status').textContent=e.message}};
async function nativeTick(){try{const current=await (await fetch('/status')).json();nativeEpoch=current.native_epoch;nativeSequence=Math.max(nativeSequence,...Object.values(current.native_sequence??{}))}catch{}setTimeout(nativeTick,500)}nativeTick();
