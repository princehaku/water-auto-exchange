import {createAquarium} from './aquarium-scene.js';

const $ = id => document.getElementById(id);
const commandNames={START:'完整换水',FILL:'开启补水',DRAIN:'开启冲水',FILL_OFF:'关闭补水',DRAIN_OFF:'关闭冲水',STOP:'停止全部输出',RESET:'故障复位'};
const resultNames={queued:'等待设备领取',delivered:'等待设备回执',succeeded:'设备已确认',rejected:'设备已拒绝',expired:'命令已过期',uncertain:'结果待核实',cancelled:'命令已取消'};
const stateNames={UNCONFIGURED:'等待配置',IDLE:'待机',DRAINING:'正在排水',SETTLING:'切换间隔',FILLING:'正在补水',EXCHANGING:'补水与排水同时进行',DONE:'本轮已完成',FAULT:'故障锁定'};
const reasonNames={ready:'已就绪，可以操作',manual_filling:'补水正在运行',manual_draining:'冲水正在运行',manual_exchanging:'两路正在同时运行',stopped:'输出已停止',fill_timeout:'补水超时',drain_timeout:'排水超时',overflow:'超高水位触发',reset:'故障已复位',mapping_not_confirmed:'输出映射尚未确认',wiring_not_confirmed:'接线尚未确认'};
const connectionReasons={connected:'设备已连接',connection_closed:'连接已关闭',peer_disconnected:'设备已断开',peer_closed:'设备主动断开',heartbeat_timeout:'设备通信超时',server_restarted:'服务已重启',protocol_or_internal_error:'连接异常',never_connected:'等待首次连接',auth_timeout:'认证超时',stale_session:'旧会话已结束',session_replaced:'新会话已接管'};
const errors={login_required:'请先登录。',invalid_key:'管理密钥不正确。',try_later:'尝试过于频繁，请稍后再试。',device_offline:'设备已离线，命令未提交。',device_not_ready:'设备尚未就绪。',command_pending:'上一条命令尚在等待回执。',firmware_upgrade_required:'需要 0.8.0 手动模式才能独立关闭输出。',simulation_requires_idle:'校准时设备须在线，且补水、排水均已关闭。',invalid_simulation:'当前水位须为 0–100%，补排水用时为 1–86400 秒；容量可不填，填写时须为 0.1–100000 L。'};
const supportedManual=['0.7.0','0.7.1','0.7.2','0.7.3','0.7.4','0.7.5','0.7.6','0.7.7','0.8.0'];
const refreshInterval=2000;
let scene=null,snapshot=null,lastSnapshot=null,signedIn=false,lastSuccess=0,refreshRequest=null,authEpoch=0,busy=false,submittedId=null,formLoaded=false,refreshEnabled=true;

try{scene=createAquarium($('tank-canvas'));}catch(error){$('scene-fallback').hidden=false;$('tank-canvas').hidden=true;}

function timeLabel(seconds){return Number.isFinite(seconds)?new Date(seconds*1000).toLocaleString('zh-CN',{hour12:false}):'尚无记录';}
function durationLabel(seconds){if(!Number.isFinite(seconds))return '—';const s=Math.max(0,Math.floor(seconds));if(s<60)return s+' 秒';if(s<3600)return Math.floor(s/60)+' 分 '+s%60+' 秒';if(s<86400)return Math.floor(s/3600)+' 时 '+Math.floor(s%3600/60)+' 分';return Math.floor(s/86400)+' 天 '+Math.floor(s%86400/3600)+' 时';}
function waterLabel(value){return Number.isFinite(value)?value.toLocaleString('zh-CN',{minimumFractionDigits:1,maximumFractionDigits:1})+' L':'— L';}
function rateLabel(value,signed=false){if(!Number.isFinite(value))return '— L/min';const number=value!==0&&Math.abs(value)<.01?value.toPrecision(2):value.toLocaleString('zh-CN',{minimumFractionDigits:signed?1:0,maximumFractionDigits:2});return (signed&&value>0?'+':'')+number+' L/min';}
function etaLabel(seconds){if(!Number.isFinite(seconds))return '—';const s=Math.max(0,Math.ceil(seconds)),parts=[Math.floor(s/60)%60,s%60];if(s>=3600)parts.unshift(Math.floor(s/3600));return parts.map(part=>String(part).padStart(2,'0')).join(':');}
function bytesLabel(value){if(!Number.isFinite(value))return '尚未上报';const units=['B','KiB','MiB','GiB'];let unit=0;while(value>=1024&&unit<units.length-1){value/=1024;unit++;}return value.toFixed(unit?1:0)+' '+units[unit];}
function serverNow(data){return data?.server_time+(Date.now()-lastSuccess)/1000;}
const retryNotice='状态同步中断，正在自动重试。';
function setMessage(value){$('message').textContent=value;const panel=document.querySelector('.menu-dialog[open] .panel-message');if(panel)panel.textContent=value;}
function setFeedback(value){$('command-feedback').textContent=value;$('device-feedback').textContent=value;}
function clearRetryNotice(){for(const node of [$('message'),...document.querySelectorAll('.panel-message')])if(node.textContent===retryNotice)node.textContent='';}
function closePanels(){document.querySelectorAll('.menu-dialog[open]').forEach(dialog=>dialog.close());if($('confirm').open)$('confirm').close();}
function updateMenus(){document.querySelectorAll('[data-panel]').forEach(button=>{button.disabled=!signedIn;});}
function openPanel(id){if(!signedIn)return;const dialog=$(id);if(dialog.open)return;closePanels();document.querySelectorAll('[data-panel]').forEach(button=>button.setAttribute('aria-expanded',String(button.dataset.panel===id)));dialog.showModal();}
async function api(path,body,signal=AbortSignal.timeout(6000)){
  const response=await fetch('./api/'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal});
  let data;try{data=await response.json();}catch{throw new Error('服务暂时不可用，请稍后重试。');}
  if(!response.ok){const error=new Error(errors[data.error]||'操作失败：'+(data.error||response.status));error.status=response.status;throw error;}
  return data;
}
function cancelRefresh(){authEpoch++;refreshRequest?.abort();refreshRequest=null;}
function showLogin(){cancelRefresh();refreshEnabled=false;signedIn=false;snapshot=null;lastSnapshot=null;submittedId=null;lastSuccess=0;formLoaded=false;closePanels();updateMenus();$('console').hidden=true;$('login-panel').hidden=false;$('logout').hidden=true;$('header-connection').textContent='未登录';$('header-connection').className='connection-pill';$('header-connection').removeAttribute('title');$('header-connection').setAttribute('aria-label','未登录');$('hero-status').textContent='登录后查看设备';$('hero-seen').textContent='';}
function setConnection(data,serviceOk=true){
  const connection=$('header-connection');
  const online=serviceOk&&data?.online===true,device=data?.device;
  const healthy=online&&device&&device.ready==='1'&&device.outputs_known==='1'&&device.overflow==='0'&&!['FAULT','UNCONFIGURED'].includes(device.state);
  const detail=!serviceOk?'服务连接中断，设备状态未知':!online?'设备离线':!device?'等待设备状态':healthy?'设备在线，运行正常':device.state==='FAULT'?'故障锁定 · '+(reasonNames[device.reason]||device.reason):device.overflow==='1'?'超高水位触发':device.outputs_known!=='1'?'输出状态未知':device.ready!=='1'?'设备尚未就绪':'设备状态异常';
  connection.textContent=!serviceOk?'状态未知':healthy?'设备正常':'设备异常';
  connection.className='connection-pill '+(!serviceOk?'unknown':healthy?'online':'offline');
  connection.title=detail;connection.setAttribute('aria-label',connection.textContent+'，'+detail+'，查看设备状态');
  $('hero-status').textContent=detail;
  $('hero-seen').textContent='最近通信 · '+timeLabel(data?.last_seen);
  $('last-seen').textContent=data?.last_seen?timeLabel(data.last_seen):'—';
  $('online-duration').textContent=serviceOk&&data?.online&&data.connection?.since?durationLabel(serverNow(data)-data.connection.since):'—';
}
function concurrent(device){return device?.version==='0.8.0'&&device.control_mode==='manual';}
function pendingFor(output){return snapshot?.commands?.find(item=>item.command===(output==='fill'?'FILL':'DRAIN')&&['queued','delivered'].includes(item.status));}
function switchCommand(button){
  const output=button.dataset.output,device=snapshot?.device;
  if(!output)return button.dataset.command;
  if(device?.outputs_known==='1'&&device[output]==='1'||pendingFor(output))return concurrent(device)?button.dataset.command+'_OFF':'STOP';
  return button.dataset.command;
}
function can(command){
  const data=snapshot,device=data?.device;
  if(!data?.online||!device||Date.now()-lastSuccess>10000)return false;
  if(command==='STOP')return true;
  if(busy)return false;
  if(command==='FILL_OFF'||command==='DRAIN_OFF')return concurrent(device);
  if(data.commands.some(item=>['queued','delivered'].includes(item.status)))return false;
  if(command==='RESET')return device.state==='FAULT';
  return ['FILL','DRAIN'].includes(command)&&supportedManual.includes(device.version)&&device.control_mode==='manual'&&device.ready==='1'&&device.outputs_known==='1'&&device.overflow==='0'&&(concurrent(device)?['IDLE','DONE','FILLING','DRAINING','EXCHANGING']:['IDLE','DONE']).includes(device.state);
}
function renderControls(){
  const device=lastSnapshot?.device;
  for(const output of ['fill','drain']){
    const button=$(output+'-button'),on=device?.outputs_known==='1'&&device[output]==='1',known=device?.outputs_known==='1';
    button.disabled=!can(switchCommand(button));
    button.classList.toggle('is-on',on);
    button.setAttribute('aria-checked',on?'true':'false');
    $(output+'-detail').textContent=!known?'状态未知':pendingFor(output)?'命令待设备确认':on?'已开启 · 点击独立关闭':'已关闭 · 点击开启';
    $(output+'-state').textContent=!known?'未知':snapshot?.online?(on?'开启':'关闭'):(on?'上次上报：开启':'上次上报：关闭');
  }
  $('stop').disabled=!can('STOP');$('reset').disabled=!can('RESET');
  const deviceOnline=snapshot?.online===true;
  $('control-hint').textContent=!snapshot?'浏览器与服务连接中断，控制暂不可用。':!deviceOnline?'设备离线，等待重新连接。':!device?'等待设备状态。':!supportedManual.includes(device.version)?'当前固件不支持此手动控制页面。':device.control_mode!=='manual'?'设备处于自动模式，此处仅显示状态。':device.state==='FAULT'?'故障锁定，请检查现场后复位。':device.state==='UNCONFIGURED'?'输出尚未配置。':concurrent(device)?'两路可同时开启，并可分别关闭。':['FILLING','DRAINING'].includes(device.state)?'当前固件两路互锁；先关闭当前输出。':'当前固件两路互锁；升级 0.8.0 可同时开启。';
}
function renderProgress(data){
  const now=serverNow(data),sim=data.simulation||{},device=data.device;
  for(const output of ['fill','drain']){
    const on=data.online&&device?.outputs_known==='1'&&device[output]==='1';
    const since=sim[output+'_on_since'];
    const seconds=on&&Number.isFinite(since)?Math.max(0,now-since):null;
    $(output+'-progress-text').textContent=seconds===null?(on?'开启时刻未知':'— / 120 秒'):Math.floor(seconds)+' / 120 秒';
    $(output+'-progress').style.width=seconds===null?'0%':Math.min(100,seconds/120*100)+'%';
  }
}
function renderWaterEstimate(data,serviceOk=true){
  const sim=data?.simulation||{},device=data?.device;
  const calibrated=sim.calibrated===true,hasCapacity=Number.isFinite(sim.capacity_liters);
  const synced=serviceOk&&Date.now()-lastSuccess<=10000;
  const trusted=synced&&data?.online&&device?.outputs_known==='1'&&calibrated&&!sim.uncertain;
  $('volume-value').textContent=hasCapacity?waterLabel(sim.volume_liters)+' / '+sim.capacity_liters.toLocaleString('zh-CN')+' L':'— L / 容量未设置';
  for(const output of ['fill','drain']){
    const rate=sim[output+'_rate_lpm'];
    $(output+'-rate').textContent=rateLabel(rate);
    $(output+'-volume').textContent=waterLabel(sim[output+'_run_liters']);
    $(output+'-total').textContent=waterLabel(sim[output+'_total_liters']);
  }
  const net=trusted&&Number.isFinite(sim.net_lpm)?sim.net_lpm:null;
  $('net-flow').textContent=rateLabel(net,true);
  $('net-flow').dataset.direction=net===null||net===0?'steady':net>0?'fill':'drain';
  const full=trusted&&Number.isFinite(sim.eta_full_seconds),empty=trusted&&Number.isFinite(sim.eta_empty_seconds);
  $('eta-label').textContent=full?'预计满水':empty?'预计排空':'预计满 / 空';
  $('eta-value').textContent=etaLabel(full?sim.eta_full_seconds:empty?sim.eta_empty_seconds:null);
  $('estimate-status').textContent=!synced?'同步中断 · 预估暂停':!calibrated?'按现场水位校准后开始预估':sim.uncertain?'估算不确定 · 请重新校准':!data.online?'设备离线 · 预估暂停':device?.outputs_known!=='1'?'输出未知 · 预估暂停':!hasCapacity?'填写容量后可显示升数':device.fill==='0'&&device.drain==='0'?'待机 · 水量估算已保留':full||empty?'按当前开关估算 · 不会自动停泵':'进出平衡 · 水位预计保持';
  $('calibration-updated').textContent=!calibrated?'尚未校准':Number.isFinite(sim.calibrated_at)?'上次校准 · '+timeLabel(sim.calibrated_at):'校准时间未记录';
}
function renderLevel(data){
  const sim=data.simulation||{},device=data.device,calibrated=sim.calibrated===true;
  const level=calibrated?Math.max(0,Math.min(100,Number(sim.level)||0)):65;
  scene?.setLevel(level);
  const known=data.online&&device?.outputs_known==='1';
  scene?.setFlow?.({fill:known&&device.fill==='1',drain:known&&device.drain==='1',circulation:true});
  $('level-fill').style.width=level+'%';
  $('level-value').textContent=!calibrated?'未校准':Math.round(level)+'%';
  $('level-detail').textContent=!calibrated?'示意水面 · 请先校准':sim.uncertain?'估算不确定 · 需校准':data.online?'按输出状态推算':'设备离线 · 模拟暂停';
  $('scene-status').textContent=!calibrated?'场景示意':sim.uncertain?'估算不确定':data.online?'模拟同步中':'模拟已暂停';
  $('model-note').textContent=!calibrated?'没有水位传感器。填写历史满缸与空缸用时，并按现场已知水位校准，才能开始估算。':sim.uncertain?'工作期间通信中断或服务重启，实际停止时刻无法确认。请到现场核实并重新校准；旧估算不会继续推进。':'水位由补水、排水开启时长及校准速率推算，双路同时开启时按净变化计算。这里不是实测水位，也不决定设备的关断。';
  $('save-calibration').disabled=!(data.online&&device?.outputs_known==='1'&&device.fill==='0'&&device.drain==='0');
  if(!formLoaded){if(calibrated){$('fill-seconds').value=String(sim.fill_seconds);$('drain-seconds').value=String(sim.drain_seconds);$('anchor-level').value=String(Math.round(sim.level));$('capacity-liters').value=Number.isFinite(sim.capacity_liters)?String(sim.capacity_liters):'';}formLoaded=true;}
  renderWaterEstimate(data);
}
function addCells(parent,values){const row=document.createElement('tr');for(const value of values){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}parent.append(row);}
function renderHistory(data){
  const commands=data.commands||[];
  $('commands-list').replaceChildren();
  for(const item of commands)addCells($('commands-list'),[timeLabel(item.created),commandNames[item.command]||item.command,resultNames[item.status]||item.status,item.result||'—']);
  $('commands-empty').hidden=commands.length>0;
  const events=data.connection?.events||[];
  $('connection-list').replaceChildren();
  for(const item of events)addCells($('connection-list'),[timeLabel(item.at),item.state==='online'?'在线':'离线',connectionReasons[item.reason]||'连接状态变化']);
  $('connection-empty').hidden=events.length>0;
}
function render(data){
  snapshot=lastSnapshot=data;
  setConnection(data);
  const device=data.device;
  $('device-state').textContent=device?(stateNames[device.state]||device.state):'等待设备连接';
  $('device-mode').textContent=device?.control_mode==='manual'?'手动控制':device?.control_mode==='automatic'?'自动模式':'模式未知';
  $('version').textContent=device?'v'+device.version:'—';
  const traffic=data.traffic||{};
  $('traffic-total').textContent=bytesLabel(traffic.total_bytes);$('traffic-boot').textContent=bytesLabel(traffic.boot_bytes);
  $('traffic-interval').textContent=traffic.available?bytesLabel(traffic.interval_bytes)+' / '+durationLabel(traffic.interval_seconds):'尚未上报';
  $('traffic-reported').textContent=timeLabel(traffic.last_report_at);
  $('device-reason').textContent=device?(data.online?'':'上次上报 · ')+(reasonNames[device.reason]||device.reason):'设备通过 4G 接入后，这里会自动更新。';
  const completed=data.commands.find(item=>item.id===submittedId);
  if(completed&&!['queued','delivered'].includes(completed.status)){
    setFeedback((commandNames[completed.command]||completed.command)+'：'+(resultNames[completed.status]||completed.status)+(completed.result?' · '+completed.result:''));
    submittedId=null;
  }
  renderControls();renderProgress(data);renderLevel(data);renderHistory(data);
}
async function refresh(){
  if(refreshRequest||document.hidden||!refreshEnabled)return;
  const request=new AbortController(),epoch=authEpoch;
  refreshRequest=request;
  const timeout=setTimeout(()=>request.abort(),6000);
  try{
    const data=await api('status',undefined,request.signal);
    if(epoch!==authEpoch)return;
    signedIn=true;lastSuccess=Date.now();updateMenus();$('login-panel').hidden=true;$('console').hidden=false;$('logout').hidden=false;
    render(data);$('sync-status').textContent='已同步 '+new Date(lastSuccess).toLocaleTimeString('zh-CN',{hour12:false});
    clearRetryNotice();
  }catch(error){
    if(epoch!==authEpoch)return;
    if(error.status===401){showLogin();return;}
    if(signedIn){snapshot=null;setConnection(lastSnapshot,false);renderControls();renderWaterEstimate(lastSnapshot,false);scene?.setFlow?.({fill:false,drain:false,circulation:true});$('save-calibration').disabled=true;$('sync-status').textContent='同步中断 · 自动重试';$('scene-status').textContent='服务连接中断';$('level-detail').textContent='同步中断 · 保留上次估算';}
    setMessage(retryNotice);
  }finally{clearTimeout(timeout);if(refreshRequest===request)refreshRequest=null;}
}
function updateClock(){if(!signedIn||!lastSnapshot)return;setConnection(lastSnapshot,snapshot!==null&&Date.now()-lastSuccess<=10000);renderWaterEstimate(lastSnapshot,snapshot!==null);if(snapshot){renderProgress(snapshot);renderControls();}}

$('login-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;try{await api('login',{key:$('key').value});cancelRefresh();refreshEnabled=true;$('key').value='';setMessage('');await refresh();}catch(error){setMessage(error.message);}finally{button.disabled=false;}});
$('logout').addEventListener('click',async()=>{try{await api('logout',{});showLogin();setMessage('已退出。');}catch(error){setMessage(error.message);}});
async function confirmReset(){const dialog=$('confirm');dialog.returnValue='cancel';dialog.showModal();return new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue==='ok'),{once:true}));}
for(const button of document.querySelectorAll('[data-command]'))button.addEventListener('click',async()=>{
  const command=switchCommand(button);
  if(!can(command)||command==='RESET'&&!await confirmReset()||!can(command)||switchCommand(button)!==command)return;
  busy=true;renderControls();
  try{
    const id=Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join('');
    submittedId=id;await api('commands',{command,id});setFeedback('命令已提交，等待设备回执。');await refresh();
  }catch(error){setFeedback(error.message+' 如果提交时连接中断，请在操作记录中核实结果。');}
  finally{busy=false;renderControls();}
});
for(const button of document.querySelectorAll('[data-anchor]'))button.addEventListener('click',()=>{$('anchor-level').value=button.dataset.anchor;});
$('calibration-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=$('save-calibration');button.disabled=true;
  try{await api('simulation',{level:Number($('anchor-level').value),fill_seconds:Number($('fill-seconds').value),drain_seconds:Number($('drain-seconds').value),capacity_liters:$('capacity-liters').value.trim()===''?null:Number($('capacity-liters').value)});$('message').textContent='';$('calibration-message').textContent='模拟水位已校准，本次与累计水量已清零。请以现场实际水位为准。';await refresh();}
  catch(error){setMessage(error.message);}finally{const device=snapshot?.device;button.disabled=!(snapshot?.online&&Date.now()-lastSuccess<=10000&&device?.outputs_known==='1'&&device.fill==='0'&&device.drain==='0');}
});
for(const button of document.querySelectorAll('[data-panel]'))button.addEventListener('click',()=>openPanel(button.dataset.panel));
for(const button of document.querySelectorAll('[data-close]'))button.addEventListener('click',()=>$(button.dataset.close).close());
for(const dialog of document.querySelectorAll('.menu-dialog'))dialog.addEventListener('close',()=>{document.querySelectorAll('[data-panel]').forEach(button=>{if(button.dataset.panel===dialog.id)button.setAttribute('aria-expanded','false');});});
for(const [button,panel,otherButton,otherPanel] of [['tab-commands','command-panel','tab-connection','connection-panel'],['tab-connection','connection-panel','tab-commands','command-panel']])$(button).addEventListener('click',()=>{$(button).setAttribute('aria-selected','true');$(otherButton).setAttribute('aria-selected','false');$(panel).hidden=false;$(otherPanel).hidden=true;});
setInterval(()=>{updateClock();refresh();},refreshInterval);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
window.addEventListener('focus',refresh);
window.addEventListener('online',refresh);
refresh();
