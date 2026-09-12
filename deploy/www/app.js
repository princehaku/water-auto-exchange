'use strict';
const $ = id => document.getElementById(id);
const names = {START:'完整换水',FILL:'补水',DRAIN:'冲水',STOP:'停止输出',RESET:'故障复位'};
const states = {UNCONFIGURED:'等待配置',IDLE:'待机',DRAINING:'正在排水',SETTLING:'切换间隔',FILLING:'正在补水',DONE:'本轮已完成',FAULT:'故障锁定'};
const results = {queued:'等待设备领取',delivered:'等待回执',succeeded:'设备已确认',rejected:'设备已拒绝',expired:'已过期',uncertain:'结果待核实',cancelled:'已取消'};
const reasons = {mapping_not_confirmed:'输出映射尚未确认',wiring_not_confirmed:'接线尚未确认',ready:'输入已稳定，可以启动',waiting_for_inputs:'等待液位输入稳定',stopped:'输出已停止',completed:'本轮换水完成',overflow:'超高水位触发',drain_timeout:'排水超时',fill_timeout:'补水超时',sensor_sequence:'液位反馈顺序异常',sensor_unstable_timeout:'液位输入持续不稳定',disabled:'控制配置未启用',draining:'等待低位补水请求',settling:'输出关闭，等待切换',filling:'等待补水请求解除',reset:'故障已复位'};
const errors = {login_required:'请先登录。',invalid_key:'管理密钥不正确。',try_later:'尝试过于频繁，请稍后再试。',device_offline:'设备已离线，命令未提交。',device_not_ready:'设备尚未就绪。',level_not_ready:'当前液位反馈不满足启动条件。',command_pending:'上一条命令尚在等待回执。',not_faulted:'设备当前无待复位故障。'};
reasons.flushing='正在单独排水，到低位后停止';
reasons.drain_completed='冲水完成，排水已停止';
errors.firmware_upgrade_required='请先更新设备固件，再使用单独冲水。';
reasons.manual_filling='补水已打开，可点击开关关闭';
reasons.manual_draining='冲水已打开，可点击开关关闭';
reasons.ready='已就绪，可以操作';
const refreshInterval=2000;
let snapshot=null, lastSnapshot=null, signedIn=false, busy=false, lastSuccess=0, submittedId=null;
let refreshEnabled=true, refreshRequest=null, authEpoch=0, initialSyncError='';
function message(text){$('message').textContent=text;}
async function api(path, body, signal=AbortSignal.timeout(6000)){
  const response=await fetch('./api/'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal});
  let data; try{data=await response.json();}catch{throw new Error('服务暂时不可用，请稍后重试。');}
  if(!response.ok){if(response.status===401 && !['login','status'].includes(path))showLogin();const error=new Error(errors[data.error]||'操作失败：'+(data.error||response.status));error.status=response.status;throw error;}
  return data;
}
function cancelRefresh(){authEpoch++;refreshRequest?.abort();refreshRequest=null;}
function showLogin(){cancelRefresh();refreshEnabled=false;signedIn=false;snapshot=null;lastSnapshot=null;submittedId=null;lastSuccess=0;if($('message').textContent===initialSyncError)message('');initialSyncError='';$('console').hidden=true;$('login-panel').hidden=false;}
function timeLabel(t){return t?new Date(t*1000).toLocaleString('zh-CN',{hour12:false}):'尚未收到数据';}
function durationLabel(seconds){if(!Number.isFinite(seconds))return '—';seconds=Math.max(0,Math.floor(seconds));if(seconds<60)return seconds+' 秒';if(seconds<3600)return Math.floor(seconds/60)+' 分 '+seconds%60+' 秒';if(seconds<86400)return Math.floor(seconds/3600)+' 小时 '+Math.floor(seconds%3600/60)+' 分';return Math.floor(seconds/86400)+' 天 '+Math.floor(seconds%86400/3600)+' 小时';}
function bytesLabel(bytes){if(!Number.isFinite(bytes)||bytes<0)return '—';const units=['B','KB','MB','GB','TB'];let i=0;while(bytes>=1024&&i<units.length-1){bytes/=1024;i++;}return (i?bytes.toFixed(2):Math.floor(bytes))+' '+units[i];}
const connectionReasons={connected:'设备已认证，长连接已建立',connection_closed:'连接已关闭',peer_disconnected:'设备连接已断开',peer_closed:'设备主动结束连接',heartbeat_timeout:'通信超时，等待设备重新连接',server_restarted:'服务已重启，等待设备重新连接',protocol_or_internal_error:'连接异常，等待设备重新连接',never_connected:'等待设备首次连接',auth_timeout:'认证等待超时',stale_session:'旧会话已结束',session_replaced:'设备重连，已替换上一条连接'};
function renderConnection(data, serviceOk=true){
  const online=data?.online===true, c=data?.connection;
  $('connection').textContent=serviceOk?(online?'● 设备在线':'○ 设备离线'):'○ 服务连接中断';
  $('connection').classList.toggle('online',serviceOk&&online);
  $('connection').classList.toggle('offline',serviceOk&&!online);
  $('connection').classList.toggle('unknown',!serviceOk);
  $('connection-detail').textContent=serviceOk?(connectionReasons[c?.reason]||(online?'设备已连接':'设备离线，等待重新连接')):'浏览器暂时无法连接服务，设备状态未知。下方保留上次查询结果，正在自动重试。';
  const now=(data?.server_time||Date.now()/1000)+Math.max(0,(Date.now()-lastSuccess)/1000);
  $('connection-since').textContent=c?.since?timeLabel(c.since):'尚未建立连接';
  $('connection-duration').textContent=serviceOk&&c?.since?durationLabel(now-c.since):'—';
  $('connection-age').textContent=data?.last_seen?durationLabel(now-data.last_seen):'尚未收到数据';
  $('last-seen').textContent='最近通信：'+timeLabel(data?.last_seen);
}
function renderNetworkHistory(data){
  const events=data.connection?.events||[];$('connection-events').replaceChildren();
  for(const item of events){const row=document.createElement('tr');for(const value of [timeLabel(item.at),item.state==='online'?'在线':'离线',connectionReasons[item.reason]||'连接状态变化']){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}$('connection-events').append(row);}
  $('connection-empty').hidden=events.length>0;$('connection-events-table').hidden=!events.length;
  const t=data.traffic, available=t?.available===true;
  $('traffic-total').textContent=available?bytesLabel(t.total_bytes):'—';
  $('traffic-boot').textContent=available?bytesLabel(t.boot_bytes):'—';
  $('traffic-interval').textContent=available?bytesLabel(t.interval_bytes)+' / '+durationLabel(t.interval_seconds):'—';
  $('traffic-updated').textContent=available?'最近统计：'+timeLabel(t.last_report_at):'尚未收到流量统计，设备需运行支持统计的固件。';
}
function can(command){
  const d=snapshot?.device;if(!snapshot?.online||!d||Date.now()-lastSuccess>10000)return false;
  if(command==='STOP')return true;
  if(busy||snapshot.commands.some(c=>['queued','delivered'].includes(c.status)))return false;
  if(command==='RESET')return d.state==='FAULT';
  return ['FILL','DRAIN'].includes(command)&&['0.7.0','0.7.1','0.7.2','0.7.3','0.7.4','0.7.5','0.7.6','0.7.7'].includes(d.version)&&d.control_mode==='manual'&&d.ready==='1'&&d.outputs_known==='1'&&d.overflow==='0'&&['IDLE','DONE'].includes(d.state);
}
function switchCommand(button){const d=snapshot?.device;return button.dataset.output&&d?.outputs_known==='1'&&d[button.dataset.output]==='1'?'STOP':button.dataset.command;}
function controls(){document.querySelectorAll('[data-command]').forEach(b=>{
  b.disabled=!can(switchCommand(b));
  if(b.dataset.output){const d=snapshot?.device,known=d?.outputs_known==='1',on=known&&d[b.dataset.output]==='1';b.setAttribute('aria-checked',on?'true':'false');b.classList.toggle('is-on',on);$(b.dataset.output+'-hint').textContent=known?(on?'已打开 · 点击关闭':'已关闭 · 点击打开'):'状态未知';}
});}
function render(data){
  snapshot=lastSnapshot=data;const d=data.device, online=data.online;
  const submitted=data.commands.find(c=>c.id===submittedId);
  if(submitted&&!['queued','delivered'].includes(submitted.status)){message(names[submitted.command]+'：'+(results[submitted.status]||submitted.status)+(submitted.result?' · '+submitted.result:''));submittedId=null;}
  renderConnection(data);renderNetworkHistory(data);
  $('state').textContent=d?(states[d.state]||d.state):'等待设备连接';
  $('reason').textContent=d?((online?'':'上次上报 · ')+(reasons[d.reason]||d.reason)):'设备通过 4G 接入后，状态会自动更新。';
  $('version').textContent=d?'Air724UG · v'+d.version:'尚无设备数据';
  $('need-fill').textContent=d?.control_mode==='manual'?'手动开关':d?'自动液位':'未知';
  for(const key of ['fill','drain'])$(key).textContent=d&&d.outputs_known==='1'?(d[key]==='1'?'开启':'关闭'):'未知';
  $('cycle').textContent=d?d.cycle:'—';
  $('protection').textContent=d?(d.overflow==='1'?'超高水位已触发，请检查现场。':d.overflow_protection==='1'?'已配置额外超高输入；软件保护生效。':'额外超高输入未启用。'):'等待保护状态';
  $('control-hint').textContent=!online?'设备离线，连接恢复后可操作。':!['0.7.0','0.7.1','0.7.2','0.7.3','0.7.4','0.7.5','0.7.6','0.7.7'].includes(d.version)?'更新设备后可使用手动开关。':d.control_mode!=='manual'?'设备需配置为手动开关模式。':d.state==='UNCONFIGURED'?'输出引脚尚未配置。手动开关不需要水位传感器。':d.state==='FAULT'?'排除故障后复位；停止输出仍可使用。':['FILLING','DRAINING'].includes(d.state)?'请先关闭当前输出，再打开另一路。':'点击开关打开或关闭，不等待水位信号。';
  document.querySelectorAll('[data-state]').forEach(e=>e.classList.toggle('current',online&&e.dataset.state===d?.state));
  $('history').replaceChildren();
  for(const item of data.commands){const row=document.createElement('tr');for(const value of [timeLabel(item.created),names[item.command]||item.command,results[item.status]||item.status,item.result||'—']){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}$('history').append(row);}
  $('empty').hidden=data.commands.length>0;$('history-table').hidden=!data.commands.length;controls();
}
async function refresh(){
  if(refreshRequest||!refreshEnabled)return;
  const request=new AbortController(), epoch=authEpoch;
  refreshRequest=request;
  const timeout=setTimeout(()=>request.abort(),6000);
  try{
    const data=await api('status',undefined,request.signal);
    if(epoch!==authEpoch)return;
    signedIn=true;lastSuccess=Date.now();
    if($('message').textContent===initialSyncError)message('');initialSyncError='';
    $('login-panel').hidden=true;$('console').hidden=false;render(data);
    $('sync-status').textContent='每 2 秒自动更新 · 已同步 '+new Date(lastSuccess).toLocaleTimeString('zh-CN',{hour12:false});
  }catch(e){
    if(epoch!==authEpoch)return;
    if(e.status===401){showLogin();return;}
    $('sync-status').textContent='同步中断 · 正在自动重试';
    if(signedIn){snapshot=null;controls();renderConnection(lastSnapshot,false);$('control-hint').textContent='服务连接中断，暂时无法操作。';}
    else{initialSyncError='暂时无法获取状态，正在自动重试。';message(initialSyncError);}
  }finally{clearTimeout(timeout);if(refreshRequest===request)refreshRequest=null;}
}
$('login-form').addEventListener('submit',async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;try{await api('login',{key:$('key').value});cancelRefresh();refreshEnabled=true;$('key').value='';message('');await refresh();}catch(e){message(e.message);}finally{button.disabled=false;}});
$('logout').addEventListener('click',async()=>{try{await api('logout',{});showLogin();message('已退出。');}catch(e){message(e.message);}});
$('refresh').addEventListener('click',()=>refresh());
async function confirmCommand(command){if(command!=='RESET')return true;const dialog=$('confirm');$('confirm-title').textContent='确认故障复位？';$('confirm-body').textContent='确认故障原因已经排除。复位后保持关闭。';dialog.returnValue='cancel';dialog.showModal();return new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue==='ok'),{once:true}));}
for(const button of document.querySelectorAll('[data-command]'))button.addEventListener('click',async()=>{
  const command=switchCommand(button);if(!can(command)||!await confirmCommand(command)||!can(command)||switchCommand(button)!==command)return;
  busy=true;controls();
  try{const id=Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');submittedId=id;await api('commands',{command,id});message('命令已提交，正在等待设备回执。');await refresh();}
  catch(e){message(e.message+' 若提交时连接中断，请等待操作记录自动更新后核实结果。');}
  finally{busy=false;controls();}
});
function resumeRefresh(){if(!document.hidden)refresh();}
setInterval(()=>{controls();if(signedIn)renderConnection(lastSnapshot,snapshot!==null&&Date.now()-lastSuccess<=10000);resumeRefresh();},refreshInterval);
document.addEventListener('visibilitychange',resumeRefresh);
window.addEventListener('focus',resumeRefresh);
window.addEventListener('online',resumeRefresh);
refresh();
