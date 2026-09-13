import {createAquarium} from './aquarium-scene.js';

const $ = id => document.getElementById(id);
const commandNames={START:'完整换水',FILL:'开启补水',DRAIN:'开启冲水',FILL_OFF:'关闭补水',DRAIN_OFF:'关闭冲水',STOP:'停止全部输出',RESET:'故障复位',FILL_TIMEOUT:'补水软上限 · 关闭全部',DRAIN_TIMEOUT:'排水软上限 · 关闭全部'};
const resultNames={queued:'等待设备领取',delivered:'等待设备回执',succeeded:'设备已确认',rejected:'设备已拒绝',expired:'命令已过期',uncertain:'结果待核实',cancelled:'命令已取消'};
const stateNames={UNCONFIGURED:'等待配置',IDLE:'待机',DRAINING:'正在排水',SETTLING:'切换间隔',FILLING:'正在补水',EXCHANGING:'补水与排水同时进行',DONE:'本轮已完成',FAULT:'故障锁定'};
const reasonNames={ready:'已就绪，可以操作',manual_filling:'补水正在运行',manual_draining:'冲水正在运行',manual_exchanging:'两路正在同时运行',stopped:'输出已停止',fill_timeout:'补水超时',drain_timeout:'排水超时',communication_timeout:'有效通信中断，输出已保护关断',overflow:'超高水位触发',reset:'故障已复位',mapping_not_confirmed:'输出映射尚未确认',wiring_not_confirmed:'接线尚未确认'};
const connectionReasons={connected:'设备已连接',connection_closed:'连接已关闭',peer_disconnected:'设备已断开',peer_closed:'设备主动断开',heartbeat_timeout:'设备通信超时',server_restarted:'服务已重启',protocol_or_internal_error:'连接异常',never_connected:'等待首次连接',auth_timeout:'认证超时',stale_session:'旧会话已结束',session_replaced:'新会话已接管',control_stop_unconfirmed:'限时关断未确认，连接已终止',control_state_uncertain:'输出状态未知，连接已终止',control_unowned_output:'输出与本次控制记录不符',control_protocol_changed:'设备控制模式变化，需重新连接'};
const errors={login_required:'请先登录。',invalid_key:'管理密钥不正确。',try_later:'尝试过于频繁，请稍后再试。',device_offline:'设备已离线，命令未提交。',device_not_ready:'设备尚未就绪。',command_pending:'上一条命令尚在等待回执。',control_timeout_pending:'已达到软上限，正在确认输出关闭。',firmware_upgrade_required:'需要 0.8.0 / 0.8.1 / 0.8.2 手动模式才能独立关闭输出。',simulation_requires_idle:'校准时设备须在线，且补水、排水均已关闭。',invalid_simulation:'当前水位须为 0–100%，补排水用时为 1–86400 秒；容量可不填，填写时须为 0.1–100000 L。'};
const supportedManual=['0.7.0','0.7.1','0.7.2','0.7.3','0.7.4','0.7.5','0.7.6','0.7.7','0.8.0','0.8.1','0.8.2'];
const refreshInterval=2000;
let scene=null,snapshot=null,lastSnapshot=null,signedIn=false,lastSuccess=0,refreshRequest=null,authEpoch=0,busy=false,jobBusy=false,submittedId=null,observedExchangeId=null,formLoaded=false,refreshEnabled=true;
const outputBusy={fill:false,drain:false};
Object.assign(errors,{invalid_output_run:'作业参数不正确，请刷新后重试。',output_run_active:'已有补排作业正在执行，请先结束对应作业。',output_run_requires_web:'完整用时作业需要 0.8.2 手动模式。',output_run_requires_idle:'本路须关闭并确认就绪后才能开始作业。',output_run_requires_calibration:'请先在校准中保存本路完整用时。',stale_output_run:'本路作业已变化，请等待同步后再操作。',request_id_conflict:'请求标识已使用，请刷新后重试。'});
Object.assign(errors,{invalid_level_target:'目标水位须为 0–100%。',invalid_level_job:'任务参数不正确，请刷新后重试。',level_job_active:'已有水位任务正在执行，请先停止。',level_job_changed:'任务已变化，请等待同步后再操作。',level_job_requires_idle:'启动前设备须在线、就绪，且两路均已关闭。',level_job_requires_calibration:'水位估算尚未校准或已不确定，请按现场水位重新校准。',level_job_requires_web:'当前设备暂不支持目标水位任务。',level_target_reached:'当前估算已经达到目标水位。'});
const jobReasons={done:'已按校准估算完成目标。',target_reached:'已达到预估目标水位。',cancelled_by_user:'已按你的要求停止任务。',manual_override:'手动操作已结束目标任务。',calibration_changed:'重新校准后，原目标任务已结束。',device_disconnected:'设备连接中断，目标任务已结束。',server_restarted:'服务重启，原目标任务未恢复。',output_unknown:'输出状态未知，目标任务已结束。',device_fault:'设备故障，目标任务已停止；请检查现场。',command_rejected:'设备拒绝指令，目标任务已结束。',start_timeout:'开启回执未确认，目标任务已结束。',stop_unconfirmed:'关断尚未确认，请检查设备状态。',estimate_uncertain:'水位估算不确定，请重新校准。'};

try{scene=createAquarium($('tank-canvas'));}catch(error){$('scene-fallback').hidden=false;$('tank-canvas').hidden=true;}

function timeLabel(seconds){return Number.isFinite(seconds)?new Date(seconds*1000).toLocaleString('zh-CN',{hour12:false}):'尚无记录';}
function durationLabel(seconds){if(!Number.isFinite(seconds))return '—';const s=Math.max(0,Math.floor(seconds));if(s<60)return s+' 秒';if(s<3600)return Math.floor(s/60)+' 分 '+s%60+' 秒';if(s<86400)return Math.floor(s/3600)+' 时 '+Math.floor(s%3600/60)+' 分';return Math.floor(s/86400)+' 天 '+Math.floor(s%86400/3600)+' 时';}
function waterLabel(value){return Number.isFinite(value)?value.toLocaleString('zh-CN',{minimumFractionDigits:1,maximumFractionDigits:1})+' L':'— L';}
function percentLabel(value){return Number.isFinite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:1})+'%':'—';}
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
function showLogin(){cancelRefresh();refreshEnabled=false;signedIn=false;snapshot=null;lastSnapshot=null;submittedId=null;observedExchangeId=null;lastSuccess=0;formLoaded=false;jobBusy=false;outputBusy.fill=outputBusy.drain=false;closePanels();updateMenus();$('console').hidden=true;$('login-panel').hidden=false;$('logout').hidden=true;$('header-connection').textContent='未登录';$('header-connection').className='connection-pill';$('header-connection').removeAttribute('title');$('header-connection').setAttribute('aria-label','未登录');$('hero-status').textContent='登录后查看设备';$('hero-seen').textContent='';}
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
function concurrent(device){return ['0.8.0','0.8.1','0.8.2'].includes(device?.version)&&device.control_mode==='manual';}
function webLimits(device){return device?.version==='0.8.2'&&device.control_mode==='manual';}
function outputLimit(device,output){return ['0.8.1','0.8.2'].includes(device?.version)?(output==='fill'?180:300):supportedManual.includes(device?.version)?120:null;}
function timedStopLabel(device){return webLimits(device)&&device.state==='IDLE'&&device.outputs_known==='1'&&device.fill==='0'&&device.drain==='0'&&device.reason==='stopped'?'输出已关闭，可再次开启。':null;}
function pendingFor(output){return snapshot?.commands?.find(item=>item.command===(output==='fill'?'FILL':'DRAIN')&&['queued','delivered'].includes(item.status));}
function runningOutput(output,data=snapshot){const run=data?.output_runs?.[output];return run?.status==='running'?run:null;}
function hasOutputRuns(data=snapshot){return ['fill','drain'].some(output=>runningOutput(output,data));}
function calibratedDuration(output,data=snapshot){const seconds=data?.simulation?.[output+'_seconds'];return Number.isFinite(seconds)&&seconds>=1&&seconds<=86400?seconds:null;}
function displayedOutputRun(output,data){
  const run=data?.output_runs?.[output];
  if(!webLimits(data?.device)||data?.level_job?.status==='running')return null;
  if(!run)return null;
  if(run.status==='running')return run;
  if(data.device?.[output]==='1'||data.simulation?.calibrated_at>run.created_at||data.level_job?.created_at>run.created_at)return null;
  if((data.commands||[]).some(item=>item.created>run.finished_at&&[output.toUpperCase(),output.toUpperCase()+'_OFF','STOP','START'].includes(item.command)&&!['rejected','cancelled','expired'].includes(item.status)))return null;
  return run;
}
function canStartOutput(output){
  const data=snapshot,device=data?.device,control=data?.control_limits;
  if(!signedIn||!data?.online||Date.now()-lastSuccess>10000||!webLimits(device)||outputBusy[output]||jobBusy||busy)return false;
  if(data.level_job?.status==='running'||runningOutput(output)||calibratedDuration(output)===null)return false;
  if(device.ready!=='1'||device.outputs_known!=='1'||device.overflow!=='0'||device[output]!=='0'||!['IDLE','DONE','FILLING','DRAINING','EXCHANGING'].includes(device.state))return false;
  if(control?.source!=='web'||control.uncertain||control.timeout_pending||control[output+'_on_since']!=null||control[output+'_deadline']!=null)return false;
  return !(data.commands||[]).some(item=>['queued','delivered'].includes(item.status)&&[output.toUpperCase(),output.toUpperCase()+'_OFF','STOP','RESET','START'].includes(item.command));
}
function outputUsesRun(button){return !!button.dataset.output&&webLimits(snapshot?.device)&&(!!runningOutput(button.dataset.output)||switchCommand(button)===button.dataset.command);}
function canOutputAction(button){
  const output=button.dataset.output,run=runningOutput(output);
  if(webLimits(snapshot?.device)&&snapshot?.level_job?.status==='running')return false;
  if(!outputUsesRun(button))return can(switchCommand(button));
  return run?!!snapshot&&signedIn&&Date.now()-lastSuccess<=10000&&!outputBusy[output]&&run.reason!=='cancelled_by_user':canStartOutput(output);
}
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
  if(busy||jobBusy)return false;
  if(command==='FILL_OFF'||command==='DRAIN_OFF')return concurrent(device);
  if(data.commands.some(item=>['queued','delivered'].includes(item.status)))return false;
  if(command==='RESET')return device.state==='FAULT';
  if(webLimits(device)&&(data.control_limits?.source!=='web'||data.control_limits?.uncertain||data.control_limits?.timeout_pending))return false;
  return ['FILL','DRAIN'].includes(command)&&supportedManual.includes(device.version)&&device.control_mode==='manual'&&device.ready==='1'&&device.outputs_known==='1'&&device.overflow==='0'&&(concurrent(device)?['IDLE','DONE','FILLING','DRAINING','EXCHANGING']:['IDLE','DONE']).includes(device.state);
}
const calibrationWaitNotice='请先结束补排作业或一键换水，并等待输出关闭确认后校准；单个目标水位任务可在轮间全关时校准。';
function canCalibrate(){
  const data=snapshot,device=data?.device,control=data?.control_limits;
  if(!data?.online||Date.now()-lastSuccess>10000||device?.outputs_known!=='1'||device.fill!=='0'||device.drain!=='0')return false;
  if(hasOutputRuns(data)||outputBusy.fill||outputBusy.drain||jobBusy||data.level_job?.status==='running'&&data.level_job.mode==='exchange')return false;
  if((data.commands||[]).some(item=>['FILL','DRAIN','START'].includes(item.command)&&['queued','delivered'].includes(item.status)))return false;
  if(control?.source==='web'&&(control.uncertain||control.timeout_pending||['fill_on_since','drain_on_since','fill_deadline','drain_deadline'].some(key=>control[key]!=null)))return false;
  return !(data.level_job?.status==='running'&&['starting','active','stopping'].includes(data.level_job.phase));
}
function renderCalibrationStatus(){
  const ready=canCalibrate();$('save-calibration').disabled=!ready;
  const message=$('calibration-message');
  if(!ready&&snapshot?.online)message.textContent=calibrationWaitNotice;
  else if(message.textContent===calibrationWaitNotice)message.textContent='';
}
function renderControls(){
  const device=lastSnapshot?.device;
  for(const output of ['fill','drain']){
    const button=$(output+'-button'),on=device?.outputs_known==='1'&&device[output]==='1',known=device?.outputs_known==='1';
    const run=runningOutput(output,lastSnapshot),previous=displayedOutputRun(output,lastSnapshot),web=webLimits(device),exchange=lastSnapshot?.level_job?.status==='running'&&lastSnapshot.level_job.mode==='exchange';
    button.disabled=!canOutputAction(button);
    button.classList.toggle('is-on',on);
    button.classList.toggle('has-run',!!run);button.dataset.runPhase=run?.phase||'';
    button.setAttribute('aria-checked',on?'true':'false');
    const detail=!known?'状态未知':device.state==='FAULT'?'故障锁定 · 请先复位':exchange?(on?'换水中 · 已开启':'换水中 · 已关闭'):run?(run.reason==='cancelled_by_user'?'正在结束 · 等待关断':run.phase==='waiting'?'等待下一轮 · 点击结束':run.phase==='starting'?'等待开启 · 点击结束':run.phase==='stopping'?'等待关闭 · 点击结束':'运行中 · 点击结束'):outputBusy[output]?'正在提交作业':pendingFor(output)?'命令待设备确认':on?'已开启 · 点击独立关闭':web&&calibratedDuration(output,lastSnapshot)===null?'未校准 · 请先校准用时':previous?({completed:'已完成',cancelled:'已结束',failed:'已中止'}[previous.status]||'已关闭')+' · 点击再运行':web?'已关闭 · 运行完整用时':'已关闭 · 点击开启';
    $(output+'-detail').textContent=detail;
    button.title=exchange?detail+'，可通过“停止换水”结束任务。':run?(on?'阀门已开启。':'阀门已关闭。')+detail:!on&&web?'每次累计运行 '+durationLabel(calibratedDuration(output,lastSnapshot))+'，按完整校准用时分轮；不会按当前水位缩短。':detail;
    $(output+'-state').textContent=!known?'未知':snapshot?.online?(on?'开启':'关闭'):(on?'上次上报：开启':'上次上报：关闭');
  }
  $('reset').disabled=!can('RESET');
  const deviceOnline=snapshot?.online===true;
  $('control-hint').textContent=!snapshot?'浏览器与服务连接中断，控制暂不可用。':!deviceOnline?'设备离线，等待重新连接。':!device?'等待设备状态。':!supportedManual.includes(device.version)?'当前固件不支持此手动控制页面。':device.control_mode!=='manual'?'设备处于自动模式，此处仅显示状态。':device.state==='FAULT'?'故障锁定，请检查现场后复位。':snapshot.control_limits?.timeout_pending?'已达到软上限，服务端正在确认全部关闭。':webLimits(device)&&(snapshot.control_limits?.source!=='web'||snapshot.control_limits?.uncertain)?'服务端计时待确认，暂不能开启。':device.state==='UNCONFIGURED'?'输出尚未配置。':timedStopLabel(device)?timedStopLabel(device):concurrent(device)?'两路可同时开启，并可分别关闭。':['FILLING','DRAINING'].includes(device.state)?'当前固件两路互锁；先关闭当前输出。':'当前固件两路互锁；升级 0.8.0 可同时开启。';
  if(deviceOnline&&webLimits(device)&&device.ready==='1'&&device.outputs_known==='1'&&device.overflow==='0'&&device.state!=='FAULT'&&snapshot.control_limits?.source==='web'&&!snapshot.control_limits.uncertain&&!snapshot.control_limits.timeout_pending)$('control-hint').textContent=calibratedDuration('fill')===null||calibratedDuration('drain')===null?'请先在校准中填写补排用时，再运行完整作业。':'每次按完整校准用时分轮，两路可同时运行、独立结束。';
  renderLevelJob();
  renderCalibrationStatus();
}
function renderProgress(data,serviceOk=true){
  const now=serverNow(data),sim=data?.simulation||{},device=data?.device,web=webLimits(device),control=data?.control_limits||{};
  const timing=web?control:sim;
  const fresh=serviceOk&&Date.now()-lastSuccess<=10000&&data?.online&&device?.outputs_known==='1';
  for(const output of ['fill','drain']){
    const run=displayedOutputRun(output,data);
    if(run){
      const active=run.status==='running',advancing=active&&run.phase==='active'&&fresh&&device[output]==='1'&&!control.uncertain;
      const additional=advancing?Math.min(Math.max(0,(Date.now()-lastSuccess)/1000),run.remaining_seconds||0,run.round_remaining_seconds??Infinity):0;
      const elapsed=Number.isFinite(run.elapsed_seconds)?run.elapsed_seconds+additional:null,remaining=Number.isFinite(run.remaining_seconds)?Math.max(0,run.remaining_seconds-additional):null;
      $(output+'-progress-label').textContent='第 '+run.round+'/'+run.estimated_rounds+' 轮';
      $(output+'-countdown-label').textContent='本次剩余';
      $(output+'-progress-text').textContent=(elapsed===null?'—':Math.floor(elapsed))+' / '+(run.total_seconds??'—')+' 秒';
      $(output+'-progress').style.width=Number.isFinite(run.total_seconds)&&run.total_seconds>0&&elapsed!==null?Math.min(100,elapsed/run.total_seconds*100)+'%':'0%';
      $(output+'-countdown').textContent=run.status==='failed'?'已中止':run.status==='cancelled'?'已结束':active&&!fresh?'—':active&&run.reason==='cancelled_by_user'?'结束中':active&&run.phase==='stopping'&&remaining<=0?'关断中':etaLabel(remaining);
      $(output+'-countdown').classList.toggle('is-ending',active&&remaining!==null&&remaining<=30);
      continue;
    }
    $(output+'-progress-label').textContent=output==='fill'?'补水运行':'排水运行';
    $(output+'-countdown-label').textContent='预计剩余';
    const duration=calibratedDuration(output,data),pending=(data?.commands||[]).some(item=>['queued','delivered'].includes(item.status)&&[output.toUpperCase(),output.toUpperCase()+'_OFF','STOP','START','RESET'].includes(item.command));
    if(web&&fresh&&device[output]==='0'&&device.ready==='1'&&device.overflow==='0'&&device.state!=='FAULT'&&data.level_job?.status!=='running'&&!pending&&control.source==='web'&&!control.uncertain&&!control.timeout_pending&&control[output+'_on_since']==null&&control[output+'_deadline']==null&&duration!==null){
      $(output+'-countdown-label').textContent='完整用时';
      $(output+'-countdown').textContent=etaLabel(duration);
      $(output+'-countdown').classList.remove('is-ending');
      $(output+'-progress-text').textContent='0 / '+duration+' 秒';
      $(output+'-progress').style.width='0%';
      continue;
    }
    const on=fresh&&device[output]==='1',limit=web?control[output+'_seconds']:outputLimit(device,output);
    const since=timing[output+'_on_since'],deadline=control[output+'_deadline'];
    const valid=on&&!timing.uncertain&&Number.isFinite(since)&&Number.isFinite(limit)&&(!web||(control.source==='web'&&Number.isFinite(deadline)));
    const seconds=valid?Math.max(0,now-since):null;
    const remaining=valid?Math.max(0,web?deadline-now:limit-seconds):null;
    $(output+'-progress-text').textContent=seconds===null?(on?'开启时刻待核实':'— / '+(limit??'—')+' 秒'):Math.floor(seconds)+' / '+limit+' 秒';
    $(output+'-progress').style.width=seconds===null?'0%':Math.min(100,seconds/limit*100)+'%';
    $(output+'-countdown').textContent=on&&web&&control.timeout_pending===output?'关断中':etaLabel(remaining);
    $(output+'-countdown').classList.toggle('is-ending',remaining!==null&&remaining<=30);
  }
  const limitsKnown=Number.isFinite(outputLimit(device,'fill'));
  $('control-timeout-note').textContent=!limitsKnown?'限时待设备确认 · 开关以设备回执为准':web?'自动分轮：补水 170 秒 · 排水 290 秒':['0.8.1','0.8.2'].includes(device.version)?'补水限时 3 分钟 · 排水限时 5 分钟':'当前固件两路各限时 120 秒';
  $('device-timeout-note').textContent=!limitsKnown?'等待设备确认当前保护时限。':web?'主开关每次累计运行完整的校准用时：补水按空到满用时，排水按满到空用时，不按当前水位缩短，水位估算到 0% / 100% 也不会提前停。服务端按补水每轮最多 170 秒、排水 290 秒执行，确认本路关闭后等待 2 秒，等待不计入运行时长。两路可以同时作业、分别结束。关闭网页后服务端仍执行；每路 180 / 300 秒软上限与固件失联最多 5 秒关断保护保留。真正故障仍需检查后复位。':device.version==='0.8.1'?'当前固件保护：补水最多 180 秒，排水最多 300 秒。两路分别计时，重复开启不会延长计时。到时关闭全部并锁存故障，需复位。':'当前固件每路最多 120 秒，到时关闭全部并锁存故障。';
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
    const run=displayedOutputRun(output,data);
    $(output+'-volume').textContent=waterLabel(run?run.estimated_liters:sim[output+'_run_liters']);
    $(output+'-total').textContent=waterLabel(sim[output+'_total_liters']);
  }
  const net=trusted&&Number.isFinite(sim.net_lpm)?sim.net_lpm:null;
  $('net-flow').textContent=rateLabel(net,true);
  $('net-flow').dataset.direction=net===null||net===0?'steady':net>0?'fill':'drain';
  const full=trusted&&Number.isFinite(sim.eta_full_seconds),empty=trusted&&Number.isFinite(sim.eta_empty_seconds);
  $('eta-label').textContent=full?'预计满水':empty?'预计排空':'预计满 / 空';
  $('eta-value').textContent=etaLabel(full?sim.eta_full_seconds:empty?sim.eta_empty_seconds:null);
  $('estimate-status').textContent=!synced?'同步中断 · 预估暂停':!calibrated?'按现场水位校准后开始预估':sim.uncertain?'估算不确定 · 请重新校准':!data.online?'设备离线 · 预估暂停':device?.outputs_known!=='1'?'输出未知 · 预估暂停':!hasCapacity?'填写容量后可显示升数':device.fill==='0'&&device.drain==='0'?'待机 · 水量估算已保留':full||empty?'满空时间为估算 · 保护计时独立':'进出平衡 · 水位预计保持';
  $('calibration-updated').textContent=!calibrated?'尚未校准':Number.isFinite(sim.calibrated_at)?'上次校准 · '+timeLabel(sim.calibrated_at):'校准时间未记录';
}
function renderLevel(data){
  const sim=data.simulation||{},device=data.device,calibrated=sim.calibrated===true;
  const level=calibrated?Math.max(0,Math.min(100,Number(sim.level)||0)):65;
  scene?.setLevel(level);
  const known=data.online&&device?.outputs_known==='1';
  scene?.setFlow?.({fill:known&&device.fill==='1',drain:known&&device.drain==='1',circulation:true});
  $('level-fill').style.width=level+'%';
  $('level-value').textContent=!calibrated?'未校准':level.toLocaleString('zh-CN',{maximumFractionDigits:1})+'%';
  $('level-detail').textContent=!calibrated?'示意水面 · 请先校准':sim.uncertain?'估算不确定 · 需校准':data.online?'按输出状态推算':'设备离线 · 模拟暂停';
  $('scene-status').textContent=!calibrated?'场景示意':sim.uncertain?'估算不确定':data.online?'模拟同步中':'模拟已暂停';
  $('model-note').textContent=!calibrated?'没有水位传感器。填写历史满缸与空缸用时，并按现场已知水位校准，才能开始估算。':sim.uncertain?'工作期间通信中断或服务重启，实际停止时刻无法确认。请到现场核实并重新校准；旧水位估算不会继续推进。主开关仍可按已保存的完整用时执行，不会因此解除水位不确定。':'0.8.2 根据本次连接已确认的开关状态、有效心跳和校准速率估算水位，双路同时开启时按净变化计算。这里不是实测水位；心跳不会清除已有的不确定历史。';
  renderCalibrationStatus();
  if(!formLoaded){if(calibrated){$('fill-seconds').value=String(sim.fill_seconds);$('drain-seconds').value=String(sim.drain_seconds);$('anchor-level').value=String(Math.round(sim.level));$('capacity-liters').value=Number.isFinite(sim.capacity_liters)?String(sim.capacity_liters):'';}formLoaded=true;}
  renderWaterEstimate(data);
}
function jobPreview(){
  const sim=lastSnapshot?.simulation||{},raw=$('target-level').value.trim(),target=raw===''?NaN:Number(raw),level=sim.level;
  const valid=Number.isFinite(target)&&target>=0&&target<=100;
  const delta=valid&&Number.isFinite(level)?target-level:null,direction=delta===null||delta===0?null:delta>0?'fill':'drain';
  const seconds=direction&&Number.isFinite(sim[direction+'_seconds'])?Math.abs(delta)*sim[direction+'_seconds']/100:delta===0?0:null;
  return {target,valid,direction,seconds,rounds:Number.isFinite(seconds)?Math.ceil(seconds/(direction==='fill'?170:290)):null};
}
function jobStartReason(preview=jobPreview()){
  const data=snapshot,device=data?.device,sim=data?.simulation||{};
  if(!signedIn||!data||Date.now()-lastSuccess>10000)return '状态同步中断，请等待重新同步。';
  if(data.level_job?.status==='running')return '任务正在执行；先停止当前任务才能设置新目标。';
  if(hasOutputRuns(data)||outputBusy.fill||outputBusy.drain)return '请先结束补水和排水作业，再设置目标水位任务。';
  if(busy||jobBusy)return '正在提交，请等待服务端确认。';
  if(!data.online)return '设备离线，暂不能开始任务。';
  if(!webLimits(device))return '目标任务需要 0.8.2 手动模式。';
  if(device.state==='FAULT')return '设备故障锁定，请检查现场后到设备状态中复位。';
  if(!sim.calibrated||sim.uncertain||!Number.isFinite(sim.level)||!Number.isFinite(sim.fill_seconds)||sim.fill_seconds<=0||!Number.isFinite(sim.drain_seconds)||sim.drain_seconds<=0)return '水位估算未校准或已不确定，请按现场水位重新校准。';
  if(device.ready!=='1'||device.outputs_known!=='1'||device.overflow!=='0'||!['IDLE','DONE'].includes(device.state)||device.fill!=='0'||device.drain!=='0')return '启动前请先关闭两路输出，并等待设备确认待机。';
  if(data.control_limits?.source!=='web'||data.control_limits?.uncertain||data.control_limits?.timeout_pending)return '服务端控制状态待确认，请稍候。';
  if((data.commands||[]).some(item=>['queued','delivered'].includes(item.status)))return '上一条命令尚在等待设备回执。';
  if(!preview.valid)return '目标水位须为 0–100%。';
  if(!preview.direction)return '当前估算已经达到目标水位。';
  return '';
}
function exchangeStartReason(){
  return jobStartReason({valid:true,direction:'exchange'});
}
function renderLevelJob(){
  const data=lastSnapshot,sim=data?.simulation||{},job=data?.level_job,active=job?.status==='running',exchange=job?.mode==='exchange',synced=!!snapshot&&Date.now()-lastSuccess<=10000;
  if(active&&!exchange)$('target-level').value=String(job.target_level);
  const preview=jobPreview(),reason=jobStartReason(preview),summary=$('job-summary'),exchangeButton=$('exchange-button');
  exchangeButton.disabled=active&&exchange?!signedIn||jobBusy||job.reason==='cancelled_by_user':!!exchangeStartReason();
  exchangeButton.textContent=active&&exchange?(job.reason==='cancelled_by_user'?'正在停止…':'停止换水'):'一键换水';
  exchangeButton.classList.toggle('is-running',active&&exchange);
  exchangeButton.title=active&&exchange?'停止本次换水，并等待两路关闭确认。':exchangeStartReason()||'先按当前估算冲水至 0%，确认关闭后自动补水至 100%。';
  $('menu-level-job').disabled=!signedIn||jobBusy||active&&exchange;
  $('level-job-title').textContent=active&&exchange?'一键换水':'按目标水位运行';
  $('exchange-workflow').hidden=!(active&&exchange);
  $('level-job-form').hidden=active&&exchange;
  $('job-current-level').textContent=sim.calibrated&&Number.isFinite(sim.level)?percentLabel(sim.level)+(sim.uncertain?' · 需校准':!synced?' · 上次估算':''):'未校准';
  const directionPreview=active?job.direction:preview.direction,secondsPreview=active?job.total_seconds:preview.seconds,roundsPreview=active?job.estimated_rounds:preview.rounds;
  $('job-preview-direction').textContent=directionPreview==='fill'?'补水':directionPreview==='drain'?'排水':secondsPreview===0?'已达目标':'—';
  $('job-preview-time').textContent=Number.isFinite(secondsPreview)?durationLabel(Math.ceil(secondsPreview)):'—';
  $('job-preview-rounds').textContent=Number.isFinite(roundsPreview)?roundsPreview+' 轮':'—';
  $('job-disabled-reason').textContent=reason||'预计时间按累计开启时长计算，轮间等待另计。';
  $('start-level-job').disabled=!!reason;
  $('target-level').disabled=active||jobBusy;
  document.querySelectorAll('[data-target-level]').forEach(button=>button.disabled=active||jobBusy);
  $('cancel-level-job').disabled=!signedIn||!active||jobBusy||job.reason==='cancelled_by_user';
  $('cancel-level-job').textContent=active&&job.reason==='cancelled_by_user'?'正在确认关闭…':exchange?'停止换水':'停止目标任务';
  $('job-details').hidden=!job;
  summary.hidden=!active;$('control-hint').hidden=active;
  if(!job)return;
  const direction=job.direction==='fill'?'补水':exchange?'冲水':'排水',progress=Number.isFinite(job.progress)?Math.max(0,Math.min(100,job.progress)):0;
  const stage=job.stage==='fill'?'2/2 · 补水至 100%':'1/2 · 冲水至 0%',taskName=exchange?'一键换水':'目标任务';
  const state=!synced?'同步中断 · 保留任务记录':active?(job.reason==='cancelled_by_user'?'正在取消 · 等待关闭确认':({starting:'等待开启确认',active:'正在'+direction,stopping:'等待关闭确认',waiting:'轮间等待 · 2 秒'}[job.phase]||'任务执行中')):({completed:taskName+'已完成',cancelled:taskName+'已停止',failed:taskName+'已中止'}[job.status]||'任务记录');
  if(exchange&&active)observedExchangeId=job.id;
  if(exchange&&!active&&synced&&observedExchangeId===job.id){
    setFeedback(state+'。'+(job.status==='completed'?'已按校准估算先冲水至 0%，再补水至 100%。':jobReasons[job.reason]||'请检查设备状态与现场水位。'));
    observedExchangeId=null;
  }
  $('job-state').textContent=state;
  $('job-stage').hidden=!exchange;$('job-stage').textContent=exchange?'换水阶段 '+stage:'';
  $('job-progress-text').textContent=percentLabel(progress);
  $('job-progress').value=progress;
  $('job-round').textContent='第 '+job.round+' / '+job.estimated_rounds+' 轮';
  $('job-target').textContent=exchange?(job.stage==='fill'?'补水至 100%':'冲水至 0%'):percentLabel(job.target_level);
  $('job-remaining').textContent=Number.isFinite(job.remaining_seconds)?durationLabel(Math.ceil(job.remaining_seconds)):'—';
  $('job-elapsed').textContent=durationLabel(job.elapsed_seconds);
  $('job-volume').textContent=waterLabel(job.estimated_liters);
  $('job-reason').textContent=active?(job.reason==='cancelled_by_user'?'正在等待两路关闭回执，尚未确认停止。':exchange?'先冲水至估算 0%，确认关闭后补至 100%；进度与剩余运行时间覆盖两阶段。轮间等待另计。':'按校准估算 · 本轮最多 '+job.round_limit_seconds+' 秒；只在确认关闭后进入下一轮。'):(exchange&&job.status==='completed'?'已按校准估算完成冲水至 0% 和补水至 100%。':jobReasons[job.reason]||'任务已结束，请以设备回执及现场水位为准。');
  summary.textContent=(!synced?'同步中断 · ':job.reason==='cancelled_by_user'?'正在停止 · ':'')+(exchange?'换水 '+stage+' · '+percentLabel(progress)+' · 余 '+etaLabel(job.remaining_seconds):direction+' · 第 '+job.round+' 轮 → '+percentLabel(job.target_level)+' · '+percentLabel(progress));
  summary.title=state+'，点击查看任务详情';summary.style.setProperty('--job-progress',progress+'%');
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
  $('device-reason').textContent=device?(data.online?'':'上次上报 · ')+(timedStopLabel(device)||reasonNames[device.reason]||device.reason):'设备通过 4G 接入后，这里会自动更新。';
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
    if(signedIn){snapshot=null;setConnection(lastSnapshot,false);renderControls();renderProgress(lastSnapshot,false);renderWaterEstimate(lastSnapshot,false);scene?.setFlow?.({fill:false,drain:false,circulation:true});$('save-calibration').disabled=true;$('sync-status').textContent='同步中断 · 自动重试';$('scene-status').textContent='服务连接中断';$('level-detail').textContent='同步中断 · 保留上次估算';}
    setMessage(retryNotice);
  }finally{clearTimeout(timeout);if(refreshRequest===request)refreshRequest=null;}
}
function updateClock(){if(!signedIn||!lastSnapshot||document.hidden)return;setConnection(lastSnapshot,snapshot!==null&&Date.now()-lastSuccess<=10000);renderWaterEstimate(lastSnapshot,snapshot!==null);renderProgress(lastSnapshot,snapshot!==null);if(snapshot)renderControls();}

$('login-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;try{await api('login',{key:$('key').value});cancelRefresh();refreshEnabled=true;$('key').value='';setMessage('');await refresh();}catch(error){setMessage(error.message);}finally{button.disabled=false;}});
$('logout').addEventListener('click',async()=>{try{await api('logout',{});showLogin();setMessage('已退出。');}catch(error){setMessage(error.message);}});
async function confirmReset(){const dialog=$('confirm');dialog.returnValue='cancel';dialog.showModal();return new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue==='ok'),{once:true}));}
async function submitOutputRun(output){
  const button=$(output+'-button');if(!canOutputAction(button))return;
  const run=runningOutput(output),epoch=authEpoch,name=output==='fill'?'补水':'排水';
  outputBusy[output]=true;renderControls();
  try{
    const body=run?{direction:output,run_id:run.id}:{direction:output,id:Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join('')};
    await api(run?'output-run/cancel':'output-run',body);
    if(epoch!==authEpoch)return;
    setFeedback(name+(run?'停止请求已提交，等待本路关闭确认。':'作业已提交，等待设备回执。'));
    await refresh();
  }catch(error){if(epoch===authEpoch){if(error.status===401){showLogin();return;}setFeedback(error.message+' 请等待同步后核实本路作业状态。');}}
  finally{if(epoch===authEpoch){outputBusy[output]=false;renderControls();}}
}
for(const button of document.querySelectorAll('[data-command]'))button.addEventListener('click',async()=>{
  if(button.dataset.output&&!canOutputAction(button))return;
  if(outputUsesRun(button)){await submitOutputRun(button.dataset.output);return;}
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
  event.preventDefault();if(!canCalibrate()){$('calibration-message').textContent=calibrationWaitNotice;return;}const button=$('save-calibration');button.disabled=true;
  try{await api('simulation',{level:Number($('anchor-level').value),fill_seconds:Number($('fill-seconds').value),drain_seconds:Number($('drain-seconds').value),capacity_liters:$('capacity-liters').value.trim()===''?null:Number($('capacity-liters').value)});$('message').textContent='';$('calibration-message').textContent='模拟水位已校准，本次与累计水量已清零。请以现场实际水位为准。';await refresh();}
  catch(error){setMessage(error.message);}finally{renderCalibrationStatus();}
});
for(const button of document.querySelectorAll('[data-target-level]'))button.addEventListener('click',()=>{$('target-level').value=button.dataset.targetLevel;renderLevelJob();});
$('target-level').addEventListener('input',renderLevelJob);
async function submitLevelJob(cancel=false,exchange=false){
  const preview=jobPreview(),epoch=authEpoch,job=lastSnapshot?.level_job,isExchange=exchange||cancel&&job?.mode==='exchange';
  if(cancel?(!signedIn||job?.status!=='running'||jobBusy||job.reason==='cancelled_by_user'):!!(exchange?exchangeStartReason():jobStartReason(preview)))return;
  const taskName=isExchange?'一键换水':'目标任务';
  jobBusy=true;$('job-message').textContent=cancel?'正在请求停止，等待设备确认关闭。':'正在提交'+taskName+'，等待服务端确认。';
  if(isExchange)setFeedback($('job-message').textContent);renderControls();
  try{
    const id=cancel?null:Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join('');
    await api(cancel?'level-job/cancel':'level-job',cancel?{job_id:job.id}:exchange?{mode:'exchange',id}:{target_level:preview.target,id});
    if(epoch!==authEpoch)return;
    if(exchange&&!cancel)observedExchangeId=id;
    $('job-message').textContent=cancel?'停止请求已提交；两路关闭以设备回执为准。':taskName+'已提交；运行状态以设备回执为准。';
    if(isExchange)setFeedback($('job-message').textContent);
    await refresh();
  }catch(error){if(epoch===authEpoch){if(error.status===401){showLogin();return;}$('job-message').textContent=error.message+' 若提交时连接中断，请等待同步后核实任务状态。';if(isExchange)setFeedback($('job-message').textContent);}}
  finally{if(epoch===authEpoch){jobBusy=false;renderControls();}}
}
$('level-job-form').addEventListener('submit',event=>{event.preventDefault();submitLevelJob();});
$('cancel-level-job').addEventListener('click',()=>submitLevelJob(true));
$('exchange-button').addEventListener('click',()=>submitLevelJob(lastSnapshot?.level_job?.status==='running'&&lastSnapshot.level_job.mode==='exchange',true));
for(const button of document.querySelectorAll('[data-panel]'))button.addEventListener('click',()=>openPanel(button.dataset.panel));
for(const button of document.querySelectorAll('[data-close]'))button.addEventListener('click',()=>$(button.dataset.close).close());
for(const dialog of document.querySelectorAll('.menu-dialog'))dialog.addEventListener('close',()=>{document.querySelectorAll('[data-panel]').forEach(button=>{if(button.dataset.panel===dialog.id)button.setAttribute('aria-expanded','false');});});
for(const [button,panel,otherButton,otherPanel] of [['tab-commands','command-panel','tab-connection','connection-panel'],['tab-connection','connection-panel','tab-commands','command-panel']])$(button).addEventListener('click',()=>{$(button).setAttribute('aria-selected','true');$(otherButton).setAttribute('aria-selected','false');$(panel).hidden=false;$(otherPanel).hidden=true;});
setInterval(updateClock,1000);
setInterval(refresh,refreshInterval);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
window.addEventListener('focus',refresh);
window.addEventListener('online',refresh);
refresh();
