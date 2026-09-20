// The current bridge is a command parser + read-only planner, not an LLM with memory.
// Local history is never silently submitted as context or replayed as commands.
const KEY='gitirl-room-conversations-v1',MAX_CHATS=20,MAX_TURNS=60;
for(const event of ['pageswap','pagereveal'])addEventListener(event,e=>{const t=e.viewTransition;if(t)for(const p of [t.ready,t.finished,t.updateCallbackDone])p.catch(()=>{});});
const $=id=>document.getElementById(id), el=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
const uuid=()=>crypto.randomUUID();
const panels={'room-settings':$('room-settings'),'room-history':$('room-history'),'room-objects':$('room-objects'),'agent-chat':$('agent-chat')};
let active='agent-chat';
function openPanel(name){const previous=active;active=name;document.body.classList.toggle('dock-open',!!name);for(const [key,panel] of Object.entries(panels)){if(panel.tagName==='DETAILS')panel.open=key===name;else panel.hidden=key!==name;}for(const b of document.querySelectorAll('[data-panel]'))b.setAttribute('aria-pressed',String(b.dataset.panel===name));if(name&&name!==previous&&!matchMedia('(prefers-reduced-motion: reduce)').matches){const panel=panels[name];for(const animation of panel.getAnimations())animation.cancel();panel.animate([{opacity:0,transform:'translateY(5px)'},{opacity:1,transform:'translateY(0)'}],{duration:160,easing:'cubic-bezier(.2,.8,.2,1)'});}}
for(const b of document.querySelectorAll('[data-panel]'))b.onclick=()=>openPanel(active===b.dataset.panel?null:b.dataset.panel);
$('close-chat').onclick=()=>openPanel(null);
for(const [name,panel] of Object.entries(panels))if(panel.tagName==='DETAILS')panel.addEventListener('toggle',()=>{if(!panel.open&&active===name)openPanel(null);});
document.addEventListener('keydown',e=>{if(e.key==='Escape')openPanel(null);});
openPanel(active);
let conversations=[],current,busy=false;
try{const saved=JSON.parse(localStorage.getItem(KEY)||'[]');if(Array.isArray(saved))conversations=saved.filter(c=>c&&typeof c.id==='string'&&typeof c.title==='string'&&Array.isArray(c.turns)).slice(0,MAX_CHATS).map(c=>({...c,turns:c.turns.filter(t=>t&&typeof t.text==='string'&&typeof t.time==='string').slice(-MAX_TURNS)}));}catch{}
// One-time cleanup of the test exchange requested during the room UI review.
try {
  const cleanupKey = KEY + ':removed-dw-test';
  if (!localStorage.getItem(cleanupKey)) {
    for (const chat of conversations) {
      chat.turns = chat.turns.filter(turn => turn.text.trim().toLowerCase() !== 'dw');
      if (chat.title.trim().toLowerCase() === 'dw') chat.title = chat.turns[0]?.text.slice(0,45) || 'New conversation';
    }
    localStorage.setItem(KEY, JSON.stringify(conversations));
    localStorage.setItem(cleanupKey, '1');
  }
} catch { /* Storage may be disabled; the chat still works in memory. */ }
function save(){try{localStorage.setItem(KEY,JSON.stringify(conversations));$('chat-storage-state').textContent='Kept in this browser only. Each message stands alone.';}catch{$('chat-storage-state').textContent='Browser storage unavailable. This history may not survive reload.';}}
function create(){const c={id:uuid(),title:'New conversation',created:new Date().toISOString(),turns:[]};conversations.unshift(c);conversations=conversations.slice(0,MAX_CHATS);current=c;save();render();}
function picker(){const select=$('conversation-picker');select.replaceChildren();for(const c of conversations){const o=el('option',`${c.title} · ${new Date(c.created).toLocaleDateString()}`);o.value=c.id;select.append(o);}select.value=current.id;}
function link(text,href){const a=el('a',text);a.href=href;return a;}
function details(title,data){const d=el('details');d.className='tool-detail';d.append(el('summary',title),el('pre',JSON.stringify(data,null,2)));return d;}
function result(body,response){
  if(!response){body.append(el('p','This request was interrupted before its reply was saved. It will not be resent automatically.'));return;}
  const a=response.action,r=a?.result||{};
  const who=response.served_by==='stub'?'a stand-in parser answered (not the real one)':/^andrew:intent/.test(response.served_by||'')?'understood by the language layer':response.served_by?'understood here':'no answer';const provenance=el('p',`${who}${a?.kind==='plan'?' · a plan, nothing moved':a?.kind==='read'?' · read from the room’s history':a?.kind==='proposal'?' · needs a pull request':''}`);provenance.className='reply-kind';
  if(response.ok===false||response.error){body.append(el('p',response.error?.message||'I could not do that.'));if(response.error?.details?.hint)body.append(el('p',response.error.details.hint));}
  else if(a?.kind==='refused')body.append(el('p',r.detail||'This command is not available here.'));
  else if(a?.kind==='confirm')confirm_(body,r);
  else if(a?.kind==='job'||a?.kind==='jobs'||a?.kind==='proposal')caretaker(body,a,r);
  else if(a?.kind==='plan'){
    // "before dinner" -> a commit: say WHICH, and when. `moment` is the bridge's own {when, at, how, source};
    // `resolved` is the same thing from our planner, and `ref_resolved` is the bare commit the words landed on.
    const m=r.moment||(r.resolved?.how==='time'?r.resolved:null), landed=r.ref_resolved&&r.ref_resolved!==a.ref?r.ref_resolved:null;
    const at=m&&(m.when||m.at), sha=(m&&m.sha)||landed;
    if(at)body.append(el('p',`“${a.ref}” is ${new Date(at).toLocaleString()}${sha?` — the room was last committed before then in ${sha.slice(0,7)}`:''}${m.message?` (“${m.message}”)`:''}.`));
    else if(landed)body.append(el('p',`“${a.ref}” is commit ${landed.slice(0,7)} — the room as it stood then.`));
    const n=r.ops?.length||0;
    // 0 ops is the answer "it already looks like that", not a broken demo — the bridge writes the sentence, we print it
    body.append(el('p',n===0&&r.detail?r.detail:`Here is what “${a.as} ${a.ref||''}” would take: ${n} thing${n===1?'':'s'} to move${r.conflicts?.length?`, ${r.conflicts.length} I would leave alone`:''}. Nothing has moved.`));
    const list=el('ul');for(const op of (r.ops||[]).slice(0,100)){const li=el('li');li.append(link(op.class||op.object_id,`/object/${encodeURIComponent(op.object_id)}`),document.createTextNode(` · ${op.kind}${Number.isFinite(op.delta_m)?` · ${(op.delta_m*100).toFixed(1)} cm`:''}`));const b=el('button','Show voxels');b.type='button';b.onclick=()=>{window.dispatchEvent(new CustomEvent('room:select-object',{detail:{objectId:op.object_id,commit:a.base_sha||r.base_sha}}));openPanel(null);};li.append(document.createTextNode(' '),b);list.append(li);}body.append(list);
    if(r.conflicts?.length)body.append(details('Conflicts left untouched',r.conflicts));
    if(r.working_tree_dirty)body.append(el('p','The working tree has uncommitted changes.'));
    const src=(r.moment||r.resolved||{}).source;
    if(src){const p=el('p',`Found that moment in ${/elastic/i.test(src)?'the room\u2019s event history (Elasticsearch)':src}.`);p.className='reply-kind';body.append(p);}
    body.append(link('Review room history ↗','/?info#history'));
  }else if(a?.kind==='read'){
    if(Array.isArray(r.commits)){body.append(el('p',`${r.commits.length} recent room commits.`));const list=el('ul');for(const c of r.commits){const li=el('li',`${c.sha} · ${c.subject}`);list.append(li);}body.append(list,link('Open the history graph ↗','/?info#history'));}
    else if(a.as==='blame'&&r.moved_in){const m=r.moved_in;body.append(el('p',`${r.class||r.object_id} was last ${r.what||'changed'}${Number.isFinite(r.delta_m)?` ${(r.delta_m*100).toFixed(0)} cm`:''} in ${m.sha.slice(0,7)} — “${m.subject}” · ${new Date(m.at).toLocaleString()}.`));if(m.capture_id)body.append(link(`the capture that saw it: ${m.capture_id} ↗`,`/capture/${encodeURIComponent(m.capture_id)}`));if(!r.frame_url&&r.frame_reason)body.append(el('p',`No picture of the moment: ${r.frame_reason}.`));}
    else if(a.as==='why'){why(body,r);}
    else if(a.as==='status'){body.append(el('p',r.clean?'Nothing to commit, working tree clean — the room is at main.':'The room has drifted from main.'));const dl=el('dl');for(const [label,value] of [['Branch',r.branch],['HEAD',r.head],['Changes',r.changes],['Conflicts',r.conflicts]]){const row=el('div');row.append(el('dt',label),el('dd',String(value??'Not recorded')));dl.append(row);}body.append(dl);}
    else body.append(el('p','Recorded differences returned by the room backend.'),details('View differences',r));
  }else body.append(el('p','The bridge returned no readable action. Inspect the response below.'));
  const diagnostics=details('Details',response);diagnostics.insertBefore(provenance,diagnostics.lastChild);
  if(response.trace?.length){const d=el('details');d.className='tool-detail';d.append(el('summary','How I worked it out'));const list=el('ol');for(const hop of response.trace)list.append(el('li',`${hop.node}: ${hop.label||''}${Number.isFinite(hop.ms)?` (${Math.round(hop.ms)} ms)`:''}`));d.append(list);diagnostics.insertBefore(d,diagnostics.lastChild);}
  body.append(diagnostics);
}
// A vector search always returns a nearest neighbour, so "no match" does not exist — only a score. Between the
// refusing floor and the acting floor the bridge ASKS instead of guessing, and nothing has been planned or
// dispatched at this point. Saying yes is a whole second request (result.yes, posted with a fresh id); saying no
// is simply never sending it. So an unanswered question cannot turn into an action, and there is no timer.
function confirm_(body,r){
  body.append(el('p',r.question||'Did you mean this one?'));
  const c=r.candidate||{},u=r.runner_up;
  const say=(o,lead)=>{const p=el('p');p.className='reply-kind';
    p.append(document.createTextNode(`${lead} `),link(o.class||o.object_id,`/object/${encodeURIComponent(o.object_id)}`),
      document.createTextNode(`${o.zone?` on the ${o.zone}`:''}${Number.isFinite(o.score)?` · ${o.score.toFixed(3)}`:''}`));
    return p;};
  if(c.object_id)body.append(say(c,'I mean'));
  if(u&&u.object_id)body.append(say(u,'not'));
  if(r.why){const w=el('p',r.why);w.className='reply-kind';body.append(w);}
  const yes=el('button','Yes, that one'),no=el('button','No');
  yes.type=no.type='button';yes.className='confirm-yes';no.className='confirm-no';
  const row=el('div');row.className='confirm-row';row.append(yes,no);
  const done=word=>{row.replaceChildren(el('em',word));};
  yes.onclick=()=>{const p=r.yes&&r.yes.payload;if(!p||!p.text){done('That answer did not carry a request to send.');return;}
    const {text,...extra}=p;done('Yes — sent.');
    send(text,{extra,display:`Yes — ${c.class||c.object_id||'that one'}`});};   // a REAL second request; nothing was pending
  no.onclick=()=>{done(r.no?`No. ${r.no.replace(/^do not send it;\s*/i,'')}`:'No. Nothing was planned or dispatched.');};
  body.append(row);
}
// "why was this diff wrong": roomctl's join, said plainly. The VERDICT and the numbers are roomctl's (it reads the
// robot's own thresholds), so nothing here re-decides them — a finding is printed as it was written.
function why(body,r){
  const g=r.gate||{},t=r.telemetry||{},cap=r.capture_id;
  body.append(el('p',r.trustworthy
    ?`That picture was trustworthy${cap?` — capture ${cap}`:''}: the quality gate passed and the robot was steady when it looked.`
    :`That picture was not trustworthy${cap?` — capture ${cap}`:''}.${(r.findings||[]).length?'':' The indices do not say why.'}`));
  for(const f of r.findings||[])body.append(el('p',`${f[0].toUpperCase()}${f.slice(1)}.`));
  if(g.skew_ms!=null||g.tilt_rate_max!=null){
    const dl=el('dl');
    const row=(label,value)=>{const d=el('div');d.append(el('dt',label),el('dd',value));dl.append(d);};
    if(g.skew_ms!=null)row('Cameras apart',`${Number(g.skew_ms).toFixed(1)} ms (allowed under ${g.max_skew_ms})`);
    if(g.tilt_rate_max!=null)row('Leaning',`${Number(g.tilt_rate_max).toFixed(3)} rad/s (allowed under ${g.max_tilt_rate})`);
    if(g.coverage_pct!=null)row('Coverage',`${(Number(g.coverage_pct)*100).toFixed(0)}%`);
    const peak=t.tilt_rate?.peak,odo=t.odom_residual?.peak;
    if(peak!=null)row('Peak tilt just before',`${Number(peak).toFixed(3)} rad/s`);
    if(odo!=null)row('Odometry off by',`${(Number(odo)*100).toFixed(1)} cm`);
    body.append(dl);
  }
  if(cap)body.append(link(`the capture itself: ${cap} ↗`,`/capture/${encodeURIComponent(cap)}`),document.createTextNode('  ·  '),link('replay that moment ↗',`/replay/${encodeURIComponent(cap)}`));
  if(r.trace?.url)body.append(link('the trace of that second ↗',r.trace.url));
  else if(r.trace?.id){const p=el('p',`Trace ${r.trace.id.slice(0,12)}\u2026 — the same moment in Sentry.`);p.className='reply-kind';body.append(p);}
}
// The caretaker's answers (docs/31 §3c): it found something and would point at it, it would tidy, or it says a
// change of where a thing BELONGS needs a pull request. It never says more than the job itself does.
const POSE=p=>p?`(${[p.x,p.y,p.z].map(v=>Number(v).toFixed(2)).join(', ')})`:'';
function jobLine(job,dispatch){const sent=dispatch?.dispatched,why=dispatch?.why||'',p=el('p',sent?`${job.job_id} · ${dispatch.state||'sent'} — on my way. I will tell you how it went.`:`This is only the plan: ${/EDGE_URL|no edge/i.test(why)||!why?'no robot is connected right now':why}. Nothing moved.`);if(why)p.title=why;return p;}
function follow(job,where){if(!job?.job_id)return;let tries=0;const t=setInterval(async()=>{if(++tries>60||!where.isConnected)return clearInterval(t);try{const r=await fetch(`/api/jobs/${job.job_id}`);if(r.status===404)return clearInterval(t);const d=await r.json();where.textContent=`${d.job_id||job.job_id} · ${d.state}${d.message?` — ${d.message}`:''}`;if(/^(succeeded|failed|undelivered|unknown|cancelled|rejected|done)/.test(d.state||''))clearInterval(t);}catch{}},2000);}
function caretaker(body,a,r){
  const found=r.resolved;
  if(found)body.append(el('p',`${a.kind==='proposal'?'You mean':'Found it:'} ${found.class||found.object_id}${r.job?.zone?` — on the ${r.job.zone}`:''}.`));
  if(a.kind==='job'&&r.job){const line=jobLine(r.job,r.dispatch);body.append(el('p',`I would go over and point at it ${POSE(r.job.target_pose)}, about ${r.job.estimated_s} s.`),line);if(r.dispatch?.dispatched)follow(r.job,line);
    const row=el('p');const show=el('button','Show it in the room');show.type='button';show.onclick=()=>{window.dispatchEvent(new CustomEvent('room:select-object',{detail:{objectId:r.job.object_id}}));};row.append(show,document.createTextNode(' '),link('its whole life ↗',`/object/${encodeURIComponent(r.job.object_id)}`));body.append(row);}
  else if(a.kind==='jobs'){const jobs=r.jobs||[];
    if(!jobs.length)body.append(el('p',r.dispatch?.why?`${r.dispatch.why[0].toUpperCase()}${r.dispatch.why.slice(1)}.`:'Nothing to tidy.'));
    else{body.append(el('p',`${jobs.length} thing${jobs.length===1?'':'s'} to put back where ${jobs.length===1?'it belongs':'they belong'}:`));const list=el('ul');for(const job of jobs.slice(0,50))list.append(el('li',`${job.object_id} → ${job.zone||''} ${POSE(job.target_pose)}`));body.append(list,jobLine(jobs[0],r.dispatch));}
    if(r.skipped?.length)body.append(details(`${r.skipped.length} I would leave alone, and why`,r.skipped));}
  else if(a.kind==='proposal'){body.append(el('p',`Moving it${r.to_zone?` to the ${r.to_zone}`:''} changes where it BELONGS. That is a decision, not a mess — it goes through a pull request the household approves, and I move nothing until then.`));if(r.detail)body.append(el('p',r.detail));}
}
function render(){picker();document.querySelector('.chat-suggestions').hidden=current.turns.length>0;const messages=$('chat-messages');messages.replaceChildren();if(!current.turns.length){const p=el('div');p.className='chat-empty';p.append(el('strong','What can I help you find?'),el('p','Ask about an object, a change, or a tidy-up.')); messages.append(p);}
  for(const turn of current.turns){for(const role of ['user','assistant']){const item=el('article');item.className='chat-message';item.dataset.role=role;item.setAttribute('aria-label',role==='user'?'Your message':'Agent reply');const head=el('header');if(role==='assistant')head.append(el('span','Agent'));head.append(el('time',new Date(turn.time).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})));const body=el('div');body.className='message-body';if(role==='user')body.textContent=turn.text;else if(turn.pending&&busy)body.append(el('p','looking…'));else result(body,turn.response);item.append(head,body);messages.append(item);}}
  messages.scrollTop=messages.scrollHeight;
}
// `extra` carries the bridge's own confirm payload (result.yes.payload) straight through, and `display` is what
// the bubble shows instead of the sentence being resent. The request_id is always fresh: the bridge is
// idempotent per id, so reusing one would replay the FIRST answer — the question — instead of acting.
async function send(text,{extra=null,display=null}={}){if(busy||!text.trim())return;text=text.trim().slice(0,500);busy=true;const chat=current,turn={id:uuid(),text:display||text,sent:text,time:new Date().toISOString(),pending:true};chat.turns.push(turn);chat.turns=chat.turns.slice(-MAX_TURNS);if(chat.title==='New conversation')chat.title=text.slice(0,45);save();render();$('agent-input').value='';$('send-agent').disabled=true;$('chat-request-state').textContent='';grow();
  try{const response=await fetch('/api/agent/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'user_command',request_id:turn.id,timestamp:turn.time,payload:{...(extra||{}),text}}),signal:AbortSignal.timeout(20000)});const data=await response.json();turn.response=data;if(!response.ok&&!data.error)turn.response={ok:false,error:{message:`Agent service returned HTTP ${response.status}`}};}
  catch(e){turn.response={ok:false,error:{message:e.name==='TimeoutError'?'The agent timed out. No automatic retry was sent.':`Could not reach the agent: ${e.message}`}};}
  finally{turn.pending=false;busy=false;save();$('send-agent').disabled=false;$('chat-request-state').textContent='';render();}
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
