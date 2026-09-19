// Read-only integration status. Availability is not proof of a robot connection.
const list=document.querySelector('#connection-list');
const summary=document.querySelector('#connection-summary');
const refresh=document.querySelector('#refresh-connections');
async function read(url){const response=await fetch(url,{signal:AbortSignal.timeout(7000)});if(!response.ok)throw Error(`HTTP ${response.status}`);return response.json();}
function row(label,value){const div=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;div.append(dt,dd);list.append(div);}
async function update(){
  refresh.disabled=true;summary.textContent='checking…';
  const [search,bridge]=await Promise.allSettled([read('/api/health'),read('/api/agent/bridge')]);list.replaceChildren();
  const searchReady=search.status==='fulfilled'&&search.value.ok===true;
  row('Search',searchReady?'Elasticsearch reachable':'Unavailable');
  if(bridge.status==='fulfilled'){
    const b=bridge.value;
    row('Middleware',b.will_serve==='andrew:jsonl'?'Local adapter available':b.will_serve==='andrew:ws'?'WebSocket adapter':b.will_serve==='stub'?'Stub only':'Unavailable');
    row('Agent socket',b.live?.ws?'Connected':'Not connected');
  }else row('Middleware','Status unavailable');
  row('Camera → scene','Not integrated');summary.textContent=searchReady?'search ready':'needs attention';refresh.disabled=false;
}
refresh.onclick=update;update();
