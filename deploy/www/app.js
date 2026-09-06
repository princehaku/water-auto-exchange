'use strict';
const $ = id => document.getElementById(id);
const names = {START:'开始换水',FILL:'单独补水',STOP:'停止输出',RESET:'故障复位'};
const states = {UNCONFIGURED:'等待配置',IDLE:'待机',DRAINING:'正在排水',SETTLING:'切换间隔',FILLING:'正在补水',DONE:'本轮已完成',FAULT:'故障锁定'};
const results = {queued:'等待设备领取',delivered:'等待回执',succeeded:'设备已确认',rejected:'设备已拒绝',expired:'已过期',uncertain:'结果待核实',cancelled:'已取消'};
const reasons = {mapping_not_confirmed:'输出映射尚未确认',wiring_not_confirmed:'接线尚未确认',ready:'输入已稳定，可以启动',waiting_for_inputs:'等待液位输入稳定',stopped:'输出已停止',completed:'本轮换水完成',overflow:'超高水位触发',drain_timeout:'排水超时',fill_timeout:'补水超时',sensor_sequence:'液位反馈顺序异常',sensor_unstable_timeout:'液位输入持续不稳定',disabled:'控制配置未启用',draining:'等待低位补水请求',settling:'输出关闭，等待切换',filling:'等待补水请求解除',reset:'故障已复位'};
const errors = {login_required:'请先登录。',invalid_key:'管理密钥不正确。',try_later:'尝试过于频繁，请稍后再试。',device_offline:'设备已离线，命令未提交。',device_not_ready:'设备尚未就绪。',level_not_ready:'当前液位反馈不满足启动条件。',command_pending:'上一条命令尚在等待回执。',not_faulted:'设备当前无待复位故障。'};
let snapshot=null, signedIn=false, busy=false, refreshing=false, lastSuccess=0, submittedId=null;
function message(text){$('message').textContent=text;}
async function api(path, body){
  const response=await fetch('./api/'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(6000)});
  let data; try{data=await response.json();}catch{throw new Error('服务暂时不可用，请稍后重试。');}
  if(!response.ok){if(response.status===401 && path!=='login')showLogin();throw new Error(errors[data.error]||'操作失败：'+(data.error||response.status));}
  return data;
}
function showLogin(){signedIn=false;snapshot=null;$('console').hidden=true;$('login-panel').hidden=false;}
function timeLabel(t){return t?new Date(t*1000).toLocaleString('zh-CN',{hour12:false}):'尚未收到数据';}
function can(command){
  const d=snapshot?.device;if(!snapshot?.online||!d||Date.now()-lastSuccess>10000)return false;
  if(command==='STOP')return true;
  if(busy||snapshot.commands.some(c=>['queued','delivered'].includes(c.status)))return false;
  if(command==='RESET')return d.state==='FAULT';
  return d.ready==='1'&&d.outputs_known==='1'&&d.overflow==='0'&&['IDLE','DONE'].includes(d.state)&&d.need_fill===(command==='FILL'?'1':'0');
}
function controls(){document.querySelectorAll('[data-command]').forEach(b=>b.disabled=!can(b.dataset.command));}
function render(data){
  snapshot=data;const d=data.device, online=data.online;
  const submitted=data.commands.find(c=>c.id===submittedId);
  if(submitted&&!['queued','delivered'].includes(submitted.status)){message(names[submitted.command]+'：'+(results[submitted.status]||submitted.status)+(submitted.result?' · '+submitted.result:''));submittedId=null;}
  $('connection').textContent=online?'● 设备在线':'○ 设备离线';$('connection').classList.toggle('online',online);
  $('last-seen').textContent='最近通信：'+timeLabel(data.last_seen);
  $('state').textContent=d?(states[d.state]||d.state):'等待设备连接';
  $('reason').textContent=d?((online?'':'上次上报 · ')+(reasons[d.reason]||d.reason)):'设备通过 4G 接入后，状态会自动更新。';
  $('version').textContent=d?'Air724UG · v'+d.version:'尚无设备数据';
  $('need-fill').textContent=d?({'1':'请求补水','0':'无请求',unknown:'未稳定'}[d.need_fill]||'未知'):'未知';
  for(const key of ['fill','drain'])$(key).textContent=d&&d.outputs_known==='1'?(d[key]==='1'?'开启':'关闭'):'未知';
  $('cycle').textContent=d?d.cycle:'—';
  $('protection').textContent=d?(d.overflow==='1'?'超高水位已触发，请检查现场。':d.overflow_protection==='1'?'已配置额外超高输入；软件保护生效。':'额外超高输入未启用。'):'等待保护状态';
  $('control-hint').textContent=!online?'设备离线，恢复 4G 连接后即可查看实时状态。':d.state==='UNCONFIGURED'?'请先完成泵和液位反馈接线配置。':d.state==='FAULT'?'排除故障并等待输入稳定后复位。':'操作将发送到设备，请查看下方执行回执。';
  document.querySelectorAll('[data-state]').forEach(e=>e.classList.toggle('current',online&&e.dataset.state===d?.state));
  $('history').replaceChildren();
  for(const item of data.commands){const row=document.createElement('tr');for(const value of [timeLabel(item.created),names[item.command]||item.command,results[item.status]||item.status,item.result||'—']){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}$('history').append(row);}
  $('empty').hidden=data.commands.length>0;$('history-table').hidden=!data.commands.length;controls();
}
async function refresh(){
  if(refreshing)return;refreshing=true;
  try{const data=await api('status');signedIn=true;lastSuccess=Date.now();$('login-panel').hidden=true;$('console').hidden=false;render(data);}
  catch(e){if(signedIn){snapshot=null;controls();$('connection').textContent='○ 服务连接中断';$('connection').classList.remove('online');message(e.message);}}
  finally{refreshing=false;}
}
$('login-form').addEventListener('submit',async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{await api('login',{key:$('key').value});$('key').value='';message('');await refresh();}catch(e){message(e.message);}finally{button.disabled=false;}});
$('logout').addEventListener('click',async()=>{try{await api('logout',{});showLogin();message('已退出。');}catch(e){message(e.message);}});
$('refresh').addEventListener('click',()=>refresh());
async function confirmCommand(command){if(command==='STOP')return true;const dialog=$('confirm');$('confirm-title').textContent='确认'+names[command]+'？';$('confirm-body').textContent=command==='START'?'设备将先排水，再按液位请求补水。请确认现场管路已就绪。':command==='FILL'?'设备将开始补水，直到液位板解除补水请求。':'确认故障原因已经排除。设备会检查输入，复位后保持待机。';dialog.returnValue='cancel';dialog.showModal();return new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue==='ok'),{once:true}));}
for(const button of document.querySelectorAll('[data-command]'))button.addEventListener('click',async()=>{
  const command=button.dataset.command;if(!can(command)||!await confirmCommand(command)||!can(command))return;
  busy=true;controls();
  try{const id=Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');submittedId=id;await api('commands',{command,id});message('命令已提交，正在等待设备回执。');await refresh();}
  catch(e){message(e.message+' 若提交时连接中断，请先刷新操作记录核实结果。');}
  finally{busy=false;controls();}
});
setInterval(()=>{controls();if(signedIn&&!document.hidden)refresh();},2000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&signedIn)refresh();});
refresh();
