// The current bridge is a command parser + read-only planner, not an LLM with memory.
// Local history is never silently submitted as context or replayed as commands.
const KEY='gitirl-room-conversations-v1',MAX_CHATS=20,MAX_TURNS=60;
for(const event of ['pageswap','pagereveal'])addEventListener(event,e=>{const t=e.viewTransition;if(t)for(const p of [t.ready,t.finished,t.updateCallbackDone])p.catch(()=>{});});
const $=id=>document.getElementById(id), el=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
const uuid=()=>crypto.randomUUID();
const panels={'room-search':document.querySelector('.room-search'),'room-settings':$('room-settings'),'system-status':document.querySelector('.system-status'),'agent-chat':$('agent-chat')};
let active='agent-chat';
function openPanel(name){const previous=active;active=name;document.body.classList.toggle('dock-open',!!name);for(const [key,panel] of Object.entries(panels)){if(panel.tagName==='DETAILS')panel.open=key===name;else panel.hidden=key!==name;}for(const b of document.querySelectorAll('[data-panel]'))b.setAttribute('aria-pressed',String(b.dataset.panel===name));if(name&&name!==previous&&!matchMedia('(prefers-reduced-motion: reduce)').matches){const panel=panels[name];for(const animation of panel.getAnimations())animation.cancel();panel.animate([{opacity:0,transform:'translateY(5px)'},{opacity:1,transform:'translateY(0)'}],{duration:160,easing:'cubic-bezier(.2,.8,.2,1)'});}}
for(const b of document.querySelectorAll('[data-panel]'))b.onclick=()=>openPanel(active===b.dataset.panel?null:b.dataset.panel);
$('close-chat').onclick=()=>openPanel(null);
for(const [name,panel] of Object.entries(panels))if(panel.tagName==='DETAILS')panel.addEventListener('toggle',()=>{if(!panel.open&&active===name)openPanel(null);});
document.addEventListener('keydown',e=>{if(e.key==='Escape')openPanel(null);});
openPanel(active);
let conversations=[],current,busy=false;
try{const saved=JSON.parse(localStorage.getItem(KEY)||'[]');if(Array.isArray(saved))conversations=saved.filter(c=>c&&typeof c.id==='string'&&typeof c.title==='string'&&Array.isArray(c.turns)).slice(0,MAX_CHATS).map(c=>({...c,turns:c.turns.filter(t=>t&&typeof t.text==='string'&&typeof t.time==='string').slice(-MAX_TURNS)}));}catch{}
function save(){try{localStorage.setItem(KEY,JSON.stringify(conversations));$('chat-storage-state').textContent='Kept in this browser only. Each message stands alone.';}catch{$('chat-storage-state').textContent='Browser storage unavailable. This history may not survive reload.';}}
function create(){const c={id:uuid(),title:'New conversation',created:new Date().toISOString(),turns:[]};conversations.unshift(c);conversations=conversations.slice(0,MAX_CHATS);current=c;save();render();}
function picker(){const select=$('conversation-picker');select.replaceChildren();for(const c of conversations){const o=el('option',`${c.title} · ${new Date(c.created).toLocaleDateString()}`);o.value=c.id;select.append(o);}select.value=current.id;}
function link(text,href){const a=el('a',text);a.href=href;return a;}
function details(title,data){const d=el('details');d.className='tool-detail';d.append(el('summary',title),el('pre',JSON.stringify(data,null,2)));return d;}
function result(body,response){
  if(!response){body.append(el('p','This request was interrupted before its reply was saved. It will not be resent automatically.'));return;}
  const a=response.action,r=a?.result||{};
  const who=response.served_by==='stub'?'a stand-in parser answered (not the real one)':response.served_by?'understood here':'no answer';const provenance=el('p',`${who}${a?.kind==='plan'?' · a plan, nothing moved':a?.kind==='read'?' · read from the room’s history':''}`);provenance.className='reply-kind';body.append(provenance);
  if(response.ok===false||response.error){body.append(el('p',response.error?.message||'I could not do that.'));if(response.error?.details?.hint)body.append(el('p',response.error.details.hint));body.append(el('p','I understand: status, log, diff, and restore <a saved state>. To find a thing, use Search.'));}
  else if(a?.kind==='refused')body.append(el('p',r.detail||'This command is not available here.'));
  else if(a?.kind==='plan'){
    body.append(el('p',`Here is what “${a.as} ${a.ref||''}” would take: ${r.ops?.length||0} thing${(r.ops?.length||0)===1?'':'s'} to move${r.conflicts?.length?`, ${r.conflicts.length} I would leave alone`:''}. Nothing has moved.`));
    const list=el('ul');for(const op of (r.ops||[]).slice(0,100)){const li=el('li');li.append(link(op.class||op.object_id,`/object/${encodeURIComponent(op.object_id)}`),document.createTextNode(` · ${op.kind}${Number.isFinite(op.delta_m)?` · ${(op.delta_m*100).toFixed(1)} cm`:''}`));const b=el('button','Show voxels');b.type='button';b.onclick=()=>{window.dispatchEvent(new CustomEvent('room:select-object',{detail:{objectId:op.object_id,commit:a.base_sha||r.base_sha}}));openPanel(null);};li.append(document.createTextNode(' '),b);list.append(li);}body.append(list);
    if(r.conflicts?.length)body.append(details('Conflicts left untouched',r.conflicts));
    if(r.working_tree_dirty)body.append(el('p','The working tree has uncommitted changes.'));
    body.append(link('Review room history ↗','/?info#history'));
  }else if(a?.kind==='read'){
    if(Array.isArray(r.commits)){body.append(el('p',`${r.commits.length} recent room commits.`));const list=el('ul');for(const c of r.commits){const li=el('li',`${c.sha} · ${c.subject}`);list.append(li);}body.append(list,link('Open the history graph ↗','/?info#history'));}
    else if(a.as==='status'){body.append(el('p',r.clean?'Nothing to commit, working tree clean — the room is at main.':'The room has drifted from main.'));const dl=el('dl');for(const [label,value] of [['Branch',r.branch],['HEAD',r.head],['Changes',r.changes],['Conflicts',r.conflicts]]){const row=el('div');row.append(el('dt',label),el('dd',String(value??'Not recorded')));dl.append(row);}body.append(dl);}
    else body.append(el('p','Recorded differences returned by the room backend.'),details('View differences',r));
  }else body.append(el('p','The bridge returned no readable action. Inspect the response below.'));
  if(response.trace?.length){const d=el('details');d.className='tool-detail';d.append(el('summary','How I worked it out'));const list=el('ol');for(const hop of response.trace)list.append(el('li',`${hop.node}: ${hop.label||''}${Number.isFinite(hop.ms)?` (${Math.round(hop.ms)} ms)`:''}`));d.append(list);body.append(d);}
  body.append(details('Raw response',response));
}
function render(){picker();const messages=$('chat-messages');messages.replaceChildren();if(!current.turns.length){const p=el('div');p.className='chat-empty';p.append(el('strong','I remember where everything belongs.'),el('p','Ask if the room is clean, what changed, or who moved what — or tell me to put it back. I understand a few commands, not small talk, and I always show the plan first.'));messages.append(p);}
  for(const turn of current.turns){for(const role of ['user','assistant']){const item=el('article');item.className='chat-message';item.dataset.role=role;const head=el('header');head.append(el('span',role==='user'?'YOU':'ROOMMATE'),el('time',new Date(turn.time).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})));const body=el('div');body.className='message-body';if(role==='user')body.textContent=turn.text;else if(turn.pending&&busy)body.append(el('p','looking…'));else result(body,turn.response);item.append(head,body);messages.append(item);}}
  messages.scrollTop=messages.scrollHeight;
}
async function send(text){if(busy||!text.trim())return;text=text.trim().slice(0,500);busy=true;const chat=current,turn={id:uuid(),text,time:new Date().toISOString(),pending:true};chat.turns.push(turn);chat.turns=chat.turns.slice(-MAX_TURNS);if(chat.title==='New conversation')chat.title=text.slice(0,45);save();render();$('agent-input').value='';$('send-agent').disabled=true;$('chat-request-state').textContent='';grow();
  try{const response=await fetch('/api/agent/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'user_command',request_id:turn.id,timestamp:turn.time,payload:{text}}),signal:AbortSignal.timeout(20000)});const data=await response.json();turn.response=data;if(!response.ok&&!data.error)turn.response={ok:false,error:{message:`Agent service returned HTTP ${response.status}`}};}
  catch(e){turn.response={ok:false,error:{message:e.name==='TimeoutError'?'The agent timed out. No automatic retry was sent.':`Could not reach the agent: ${e.message}`}};}
  finally{turn.pending=false;busy=false;save();$('send-agent').disabled=false;$('chat-request-state').textContent='I plan first. Nothing moves until you say so.';render();}
}
// one line until it needs more: the box grows with what is typed, up to five lines, and shrinks back after a send
function grow(){const t=$('agent-input');t.style.height='auto';t.style.height=Math.min(t.scrollHeight,116)+'px';}
$('agent-input').addEventListener('input',grow);
$('agent-form').onsubmit=e=>{e.preventDefault();send($('agent-input').value);};
$('agent-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('agent-form').requestSubmit();}};
for(const b of document.querySelectorAll('[data-prompt]'))b.onclick=()=>{send(b.dataset.prompt);};
$('new-conversation').onclick=()=>{create();$('agent-input').focus();};
$('conversation-picker').onchange=e=>{current=conversations.find(c=>c.id===e.target.value)||conversations[0];render();};
$('delete-conversation').onclick=()=>{if(!confirm('Delete this conversation from this browser? This does not change the room.'))return;conversations=conversations.filter(c=>c.id!==current.id);if(!conversations.length)create();else{current=conversations[0];save();render();}};
$('export-conversation').onclick=()=>{const blob=new Blob([JSON.stringify(current,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=link('Export',url);a.download=`gitirl-conversation-${current.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
if(!conversations.length)create();else{current=conversations[0];render();}
(async()=>{const say=t=>{$('agent-capability').textContent=t;};const get=async u=>{const r=await fetch(u,{signal:AbortSignal.timeout(7000)});if(!r.ok)throw Error(String(r.status));return r.json();};
  let home=true,stub=false,edge=false;try{const b=await get('/api/agent/bridge');stub=b.will_serve==='stub';}catch{home=false;}
  try{edge=!!(await get('/api/housebot')).enabled;}catch{/* no dispatcher mounted: plans only */}
  say(!home?'the roommate is out — this server did not answer. Your conversations are still here.':`caretaker · ${stub?'stand-in parser · ':'parsed here · '}${edge?'the robot runs via Housebot Edge':'plans only, robot not connected'}`);})();
