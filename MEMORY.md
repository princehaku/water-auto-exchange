# 项目记忆

## 2026-09-13：0.7.7省流量与工作常亮灯

用户确认采用待机30秒、工作1秒心跳、每5分钟上报流量，覆盖此前空闲也每秒心跳的要求。`src/water_network_config.lua`设置`heartbeat_ms=30000`、`active_heartbeat_ms=1000`、`traffic_interval_s=300`；统计周期按秒传给当前LuaTask V2.4.4的`socket.setIpStatis`，允许60–3600整数秒，省略时默认300。无统计API仍可联网。

WSS持续保持，服务端推送命令不等下一次心跳；执行命令时刷新活动通信计时，工作期间仍每秒心跳。状态变化下个500ms网络检查立即上报，USB/trace仍每5秒打印；流量上报变慢不延长活动10秒失联停止、75秒空闲检测或单次120秒输出上限。120秒到期关断并锁存`fill_timeout`/`drain_timeout`，需要复位；重连不恢复输出。网页仍每2秒查询服务器，流量数字取最新已上报样本。

GPIO12/物理53由`netLed`单独控制；真实输出记录明确开启时，所有网络状态（包括未联网/搜网）都设为常亮，停止后恢复V2.4.4原有各状态闪烁时长。复用`updateBlinkTime(state,65535,0)`，不另启GPIO写入任务与库争用。控制器在应用输出后通知灯效，异常被隔离，不能阻止STOP；USB独立控制也能亮灯。常亮反映软件输出记录，不是水流或电压反馈。灯闪烁与WSS心跳独立，不消耗SIM数据。

截至00:17，真实日志/API已确认0.7.6、流量持续上报；旧卡联通（3gnet），新卡移动（CMIOT），23:51:26重启由用户换卡触发。移动卡00:03:41上线后至00:17仍有通信；此前两卡均出现过超时，短时稳定不能证明网络问题永久解决。00:17真实状态为FAULT/drain_timeout、fill=0/drain=0。

旧配置两段60秒样本12844/12959字节，折算约557MB/30天，属于库估算而非运营商账单。新配置按每天工作10分钟、其余待机，模型约25–40MB/30天；重连、TLS和重传会影响实际值。此数不是0.7.7实测节省。5分钟未上报区间在掉电时可能丢失。

源码/生成包0.7.7，下载项目`water-online-0.7.7`，仍8Lua+CA、原CORE与默认库。LED及省流量需要用户下载脚本；本轮没有恢复Escape停止的桌面输入或操作真实输出。

本地195项Lua（12+15+20+36+23+53+36）、72项Python及Edge桌面/手机联调通过；覆盖30秒待机边界、服务端主动命令、活动1秒心跳与10秒停止、5分钟统计、75秒空闲期限、GPIO灯效切换/超时恢复及灯效异常不阻断STOP。LuaFLOAT、JS、PowerShell语法通过；9项生成包/版本/源码/私有凭据/超时/输出映射/原CORE核对通过。测试未操作真实GPIO，不等同0.7.7实板验证。

后端/网页0.7.7兼容已部署，备份`/apps/water-auto-exchange/backups/20260912T161911Z-3944482`；容器healthy/restart=0，公网四静态文件与本地一致，TLS/WSS凭据probe、HTTPS登录/status/退出、999天会话和water/sms健康通过。部署前确认真实两路均关闭；本轮未注册模拟设备或发送开水命令。实板仍需用户下载0.7.7，服务端更新不会改变板端心跳或灯效。

## 2026-09-12 最新：Web自动刷新与故障恢复

- 用户要求不再手动点刷新并push。原来已有登录后每2秒查询；本次补上首次加载失败后的自动重试、focus/online即时同步、最近网页同步时间。可见页面自动同步在线/离线、开关、回执、连接记录和最新流量，后台暂停查询，返回恢复。流量仍约60秒由板端统计上报；网页查询只访问服务器，不增加板端请求。
- 查询最多6秒且不重叠，临时故障不会终止轮询；401或退出才停查，登录重新启用。退出取消旧请求并使其响应失效，防止迟到成功重新显示控制台；恢复后同步提示自动正常，不覆盖命令回执。手动刷新保留作即时查询入口。
- Edge本地HTTP/模拟设备测试全程0次点击刷新，自动状态/流量/命令回执、初始与后续中断恢复、慢查询并发限制、退出竞态、版本/模式限制、桌面和手机布局均通过；JS语法与diff检查通过。网页单独部署备份`/apps/water-auto-exchange/backups/20260912T153504Z-web`，四公网文件一致、TLS/WSS probe与API健康通过；容器healthy、StartedAt未变，无服务重启。
- 同步更新AGENTS及Web文档；本轮无需刷板，未修改Lua/后端、未发真实开关或模拟设备到生产。固件0.7.6的实板流量与长期稳定性仍按上一节待验证，网页同步成功不可代替设备通信证据。

## 2026-09-12 最新：0.7.6连接详情、估算流量与会话恢复

- 用户要求展开Web在线/离线、展示板子流量，并继续修复后重新部署。网页新增始终展开的连接与流量区域：状态开始/持续时长、距最近通信、最近60次在线/离线记录、累计已上报/本次运行/最近统计段流量。区分设备离线与浏览器/API连接中断，后者状态未知且禁用控制，不伪造设备离线事件；历史输出及统计继续保留并标明时间。
- 服务端SQLite保存连接变化、最近设备快照和流量计数；正常断线保留last_seen，清除活跃会话后立即离线。重启保留历史并记录server_restarted，不把历史遥测认作当前在线，也不恢复旧命令。流量用每次脚本运行的累计计数幂等入库，跨重连、重复上报、服务重启不重复累加；累计从启用后收到的上报开始，掉电前未上报部分可能丢失，不能追溯此前实际计费。
- 板端0.7.6启用官方本机LuaTask V2.4.4 socket.setIpStatis(60)，订阅LIB_IP_STATIS_RPT；源码计数包括payload及固定估算开销（TCPSSL建连800字节、每次TCP收发额外80等），不是运营商账单，也不拆出无法可靠取得的上/下行计费数据。约每60秒有样本才上报，第一次认证分配meter id并在脚本运行期间跨重连保留；缺API时功能不可用但不阻断控制或联网。无数据展示未知，不虚填0。
- 本轮读取到完整19:02日志：0.7.5在19:02:35.115实际上online，随后ack增长；用户所贴片段只缺了下一行。因此不能称这次认证失败。后段20:15 CPIN READY、CEREG:2,0、CSQ99及waiting_pdp/UNREGISTER是保存日志里的未注册状态，不能说这是23点的实时串口状态。0.7.5已刷入由现场日志证明，替代上一节待下载快照；本轮助手未刷写或发送真实开关命令。
- 服务器SSH曾在认证前持续关闭，后来23:20恢复；只读检查nginx active、容器healthy/restart=0、磁盘23%、water及sms健康200，公网四静态文件及WSS凭据probe可用，不能声称整个服务器宕机。容器日志19:27–19:43有多条another_device_active，确认存在新连接被旧会话拒绝的情况，但不据此认定所有发送超时的唯一原因。
- 0.7.6服务端允许超过既有75秒空闲/10秒活动期限的旧WS会话被新认证连接回收，仍拒绝抢占健康会话；旧线程随后关闭不会清除新会话，旧命令不重放。服务端认证等待从5秒改为30秒；板端新增auth_timeout_ms=30000，与30秒发送等待配合。认证成功后持续复用连接；每秒心跳、10秒活动失联停止、75秒空闲检测、重连不恢复输出均保留。该容错调整不是19:02片段的确定根因。
- 189项Lua（12+15+20+36+23+47+36）、70项Python及Edge桌面/手机本地HTTP联调通过，新增覆盖统计/去重/持久化、掉线保留last_seen、旧会话回收边界与旧线程清理、认证延迟、十分钟模拟心跳不重复认证、浏览器API故障与恢复。截图已检查无页面横向溢出。新下载包仍8Lua+CA，项目water-online-0.7.6，输出映射及CORE保留；0.7.6实板流量与稳定性待用户下载验证。没有恢复此前Escape停止的computer-use。

- 接续增加同一设备会话交接：板端在认证成功后记住previous_session，重连auth携带上次标识；服务器在设备密钥验证后，仅允许持有当前会话标识的设备立即替换自己的旧连接。无标识、错误/过期标识仍不能抢占健康连接；旧命令标为不确定，旧线程退出不清除新会话。它解决同一板子的半开连接仍占位时反复another_device_active的问题，不代替4G无线链路恢复。
- 最终0.7.6后端与网页已部署，备份`/apps/water-auto-exchange/backups/20260912T152752Z-3926877`（本轮首次部署为`20260912T152309Z-3924958`）。容器healthy，公网四静态文件逐字节一致，新connection/traffic状态字段、TLS/WSS凭据probe、HTTPS登录/status/退出、999天Cookie/持久到期和water/sms健康通过；未注入模拟设备或真实开关命令。189Lua、70Python、LuaFLOAT/PS/JS及9项下载包检查通过。
- 本地真实WS单连接85秒、85次ping/pong、只认证一次且未重连通过；此检查早于最后增加的会话交接，交接另有真实WS回归及旧线程/命令隔离测试。首次部署后对真实设备观察80秒，均显示0.7.5/IDLE/online，但后3次last_seen停在同一时间，不能称稳定通信；最终部署后再次在线，流量仍未上报。0.7.6实板尚待用户下载验证，不将服务器在线或本地模拟当成固件已刷入/流量已实测。

## 2026-09-12 最新：0.7.4实板就绪，0.7.5修正上线后发送等待与积压

- 用户17:22日志确认实板0.7.4/manual/IDLE/ready=1/outputs_known=1、fill=0/drain=0，替代上一节“0.7.4待下载”的快照。17:22:26.792 TLS成功（7520ms），26.980应用认证online，31.849心跳seq=4/ack=0，33.329 socket:send TIMEOUT。该次失败发生在上线后的发送阶段，不能再称为TLS建连超时或映射未配置；seq表示心跳入队，不证明发送完成。
- 源码0.7.5增加send_timeout_ms=30000（5–60秒整秒可配）；旧代码每次send只传5秒，与SMS所用旧websocket.lua不指定send超时、落到socket4G默认120秒不同。新配置按秒传底层，不照搬120秒。待发ping仅保留最新一条，auth/claim/ack/status保持顺序且不合并；每发一帧先收包，防止持续心跳让接收饥饿。发送取消后不继续发送旧队列。
- 保留每秒心跳、60秒TLS建连、活动10秒无有效回执停止、75秒空闲检测、无限退避/PDP恢复及重连不恢复输出。30秒socket等待不延长独立控制定时器的10秒停止期限。GPIO23补水、GPIO5排水及LOW后release关断保持0.7.4配置；没有重新扫描GPIO、发送真实开水命令或改APN/CORE。
- 新日志包含tx（底层确认发送的帧数）、queued、sending，以及慢发送/失败的kind、耗时和等待预算；不打印帧内容或密钥。板端记录WS关闭码，服务端仅记录白名单错误原因，可区分another_device_active等拒绝，任意异常详情不入日志。旧认证断开原因仍未证实，不用新诊断代码反推历史根因。
- CESQ最后两项4,6按标准量化表约为RSRQ[-18,-17.5)dB、RSRP[-135,-134)dBm，提示弱信号；仅是单次样本，不能认定唯一故障原因。已询问4G天线和移至窗边对比，未收到答复时不假定已接/已改善；参考及具体限制见docs/network-recovery.md。
- 183项Lua（12+15+20+36+23+41+36）、58项Python和Edge本地HTTP开关联调通过。新增覆盖超过5秒成功、收发公平、心跳合并和队列上限、命令顺序、长发送取消、10秒停止、0.7.5兼容及服务端日志脱敏。模拟通过不能代替实板0.7.5验证。未恢复被Escape停止的computer-use；新版本仍需用户下载并观察实际seq/ack持续增长。
- 0.7.5完整8Lua+CA下载包与water-online-0.7.5项目已准备，源文件/版本/联网凭据/30秒发送预算/两路映射及原CORE路径核对通过，LuaFLOAT/PS/JS语法通过。后端和网页兼容已部署，备份`/apps/water-auto-exchange/backups/20260912T093214Z-3813795`；容器healthy，四个公网静态文件一致，公网TLS/WSS凭据probe及HTTPS登录/status/退出、999天Cookie与持久到期、water/sms健康检查通过。未向生产注册模拟设备、提交开关命令或下载实板；0.7.5实际4G稳定性待验证。

## 2026-09-12 最新：沿用旧扫描关断流程，0.7.4启用两路输出

- 用户补充“切到别的gpio就没电压了”，这是对旧扫描完整关断流程有效的新增测量反馈；不能继续把仅未确认单独LOW解释为没有关断依据。已复核src/gpio_probe.lua：每脚HIGH/LOW各5秒，切脚/STOP时setval(0)后pins.close。依此采用完整序列，无需用户重新证明单独保持LOW也能关断；不把close推断为特定内部上下拉或独立测量结论。
- 用户确定用途：补水接DO2/GPIO23，排水接出纸电机口/GPIO5。源码及生成包0.7.4设manual、enabled/mapping_confirmed/wiring_confirmed=true，两路on_level=1、off_level=0、off_mode=release。这里的off_level只是释放前的写入，完整关断还必须close。约12V/6.1V为此前接口观察，非GPIO逻辑电压或负载额定证明。
- 适配器新增可选release关断；旧未指定/hold配置仍持续保持OFF。开机USB STOP先建立，然后两路写LOW并释放，保持待机；发开水命令才重新setup LOW再写HIGH。关闭已释放输出不重复写GPIO；LOW失败也尝试close，任何错误保留未知/FAULT且阻止另一输出开启。重开前恢复输出模式，旧回调不能在STOP后拉高。保留两路互锁、默认120秒、无传感器manual、断网停止及不自动恢复。
- 175项Lua（12+15+20+36+23+39+30）、55项Python、Edge本地HTTP联调通过。新增9项Lua覆盖真实默认配置/USB启动顺序、两路独立重复开关、释放与LOW失败、重开失败、120秒超时、缺少close及STOP竞态；模拟测试不等于实物新版本验证。LuaFLOAT、PS、JS语法通过，生成包9项/源码一致/私有联网凭据/映射/CORE及water-online-0.7.4项目核对通过。
- 0.7.4仍待下载。此次COM4存在但STATUS请求3秒无应答，最近实板已确认仍沿用0.7.3；未恢复被Escape停止的computer-use、未刷写或发实板FILL/DRAIN。用户可选择新项目点击下载脚本，保留CORE；板端须实读版本、manual/ready=1/outputs_known=1、初始fill=0/drain=0及真实在线。不要写成两个物理接口本版已测试通过。
- 服务端和网页0.7.4兼容已部署，备份`/apps/water-auto-exchange/backups/20260912T091638Z-3808181`，容器healthy。四个公网静态文件与本地一致，真实HTTPS登录/status/退出、999天Cookie与持久化到期时间、water/sms健康及凭据WSS probe通过；未注册模拟设备或发控制命令。沿用999天会话持久化；不改变WSS重试/证书参数，不将之前认证阶段断开解释为已修复。

## 2026-09-12 最新：网页登录 token 保留 999 天，输出仍待配置

- 用户要求“服务端token保留999天”。网页登录会话从8小时内存保存改为登录后固定999天（86,313,600秒），保存到现有data/water.db的admin_sessions表，正常容器重启和部署后保留。仅存token摘要、到期时间和管理密钥摘要；退出立即撤销当前token，更换管理密钥并重启撤销旧会话。设备密钥、WSS会话/命令时限及板端0.7.3不变，无需刷板。
- Cookie下发Max-Age=86313600，保留Secure/HttpOnly/SameSite=Strict、Origin校验和登录限频。首次从旧版内存会话升级后须重新登录一次；浏览器保存限制或清理Cookie可要求提前登录，不把服务器999天写成所有浏览器保证保存999天。
- 54项Python测试及Edge本地HTTP联调通过；新增6项真实HTTP/临时持久库测试覆盖999天精确到期、读取不续期、重启保留、退出撤销跨重启、其他登录不受影响、数据库摘要不能登录、管理密钥轮换及过期清理。本轮未修改Lua，不重复上一轮166项Lua测试，也未发送实物开关命令。
- 已部署，备份`/apps/water-auto-exchange/backups/20260912T084221Z-3796865`，容器healthy。真实公网HTTPS验证999天Cookie/数据库截止时间、认证status、退出后401，water及sms健康200；测试登录已退出，未注册模拟设备或发送控制命令。首次SSH握手中断发生在远端变更前，重试部署成功。
- 用户随后报告网页曾显示在线、最近通信16:41:12，并贴出16:42:05 TLS成功（2220ms）、WS升级成功后认证阶段断开。该段不能称为TLS连接超时，具体关闭原因仍未证实；服务端通用关闭日志无法区分认证、旧会话占用及帧错误。此次部署约16:42:27停止旧容器，新容器启动于16:42:33.554，不能据此解释16:42:05的断开，也不能把曾在线当作稳定在线。
- 用户问在线为什么不能补水/放水：实板0.7.3仍UNCONFIGURED/mapping_not_confirmed/ready=0/outputs_known=0，src/water_config.lua的enabled/mapping_confirmed/wiring_confirmed为false，两路GPIO及on/off电平为nil。网页按该状态禁用开水是当前设计；manual不需要液位或水流设备。用户已明确用途：补水接顶部DO2，排水接出纸电机口。用途确认不等于GPIO/电平确认：GPIO23为DO2强候选，GPIO5拉高时出纸口约6.1V；历史DO2高低均约12V、STOP后约0V仍不能证明低电平关断。已询问是否两路实测高开低关，未收到新测量前保持未配置。未恢复被Escape停止的computer-use或修改GPIO。

## 2026-09-12 最新：用户要求证书与短信项目一致，0.7.3

- 已通过computer-use刷新项目列表、选water-online-0.7.3并点击下载脚本，16:29:47.736工具报告下载成功（保留CORE）。随后COM4实读0.7.3/manual/UNCONFIGURED；临时打包脚本版本、enabled=true、long_connection_cert=nil及私有密钥匹配均核对通过。
- 16:30:08已注册并IP_READY；16:30:09.239实板打印ca=disabled sni=disabled，底层TCPSSL证书参数nil、timeout60，证明确实按SMS默认参数运行。截止最后读取16:31:01尚未看到本轮online。用户随后按物理Escape停止computer-use，立即停止所有后续UI操作，不将其写成联网修复成功。
- 0.7.3后端/网页已部署，备份`/apps/water-auto-exchange/backups/20260912T082733Z-3791828`。四个公网静态文件一致、water/sms健康200，PC以CA/SNI及SMS式无CA/无SNI两种TLS1.2均完成凭据WSS probe，未注册模拟设备或发开水命令。后续SSH健康查询因banner超时未取得新结果；不将部署后的PC检查当板端联网证明。
- 用户明确“改啊，也改成和sms一致”，授权WSS证书参数可选、默认不校验，覆盖此前必须保留CA/SNI的约定。src/water_network_config.lua改为long_connection_cert=nil，底层socket.tcp(true,nil)，采用旧库默认无CA校验、hostNameFlag=0/SNI不启用、insist=1。仍使用WSS加密，未引入HTTP回退。需要校验时显式填写{caCert="water-ca.crt",hostNameFlag=1,insist=0}。
- 配置表存在CA时仍检查文件，缺失/读取失败直接报network_ca_missing，不自动降级。无表或无CA不读取CA文件；每次连接复制证书表，避免socket4G将相对路径改写后污染重试配置。日志按实际配置打印ca/sni启用状态，保留60秒连接等待、1秒心跳、活动10秒失联停止和重连不重开水。
- 166项Lua（12+15+19+28+23+39+30）、48项Python及Edge联调通过，新增覆盖nil证书实际传参、CA读取跳过/失败、非法参数、重连配置不被改写和0.7.3兼容。生成包仍8Lua+CA共9项，CA仅为可选资源，默认不读取；water-online-0.7.3已准备。
- 本轮USB已重现，COM4实读0.7.1/manual/UNCONFIGURED，更新之前仅COM1的快照。本轮已找到node_repl/@oai/sky工具并读取computer-use技能，随后刷写结果见本节顶部。GPIO保持未配置，不发开水命令。

## 2026-09-12 最新：对照短信项目，0.7.2放宽建连等待并分层诊断

- 0.7.2兼容后端/网页已部署，备份`/apps/water-auto-exchange/backups/20260912T082015Z-3789216`。容器healthy，四个公网静态文件与本地一致，water/sms健康接口200，保留CA/SNI的TLS1.2及既有凭据WSS probe通过；probe未注册模拟设备、未提交控制命令。
- 用户指定对照E:/sms-forward，并指出01:55:43曾成功。已核对trace：0.5.2在01:55:11.091 TLS成功、01:55:11.995 WATER WS online，01:55:43仍通信；01:56:37发送超时。凌晨CA/SNI可用，旧心跳时基已于0.5.3修复，不能将它解释为下午建连前的失败根因。
- 短信HTTPS源码30秒，WSS传30000给旧websocket库；本机V2.4.4原样传入以秒计的socket.connect/send，存在单位混用，不照搬。短信有HTTPS回退；源码HTTP仅SNI，WSS证书来自可选配置；本机没有短信实际config.lua/trace，不能断言运行时CA设置。服务端日志有短信WS101记录，不能当作现在在线。短信项目没有修改。
- 源码/生成包0.7.2：tls_connect_timeout_ms=60000，校验15–120秒整秒并除1000传底层；增加连接耗时/注册/PDP日志。连续连接失败达到3且本次TLS失败，关闭原会话/socket后只建立普通TCP到同域名443并关闭，不发送业务数据、密钥，不作TLS回退或上线凭据；最多每300秒一次，无IP/未注册跳过。
- CA/SNI/insist=0、1秒心跳、活动10秒失联停止、75秒空闲检查、无限退避和受限频率PDP恢复保留，重连不重开水。默认控制GPIO继续禁用；用户现阶段不要求传感器或水流验收。
- 160项Lua（12+15+19+28+23+35+28）、47项Python与Edge本地HTTP联调通过。新增覆盖秒/毫秒、25秒连接、取消后迟到成功、TCP诊断关闭/限频/协程yield、长建连等待不延长活动停止、0.7.2前后端/USB兼容。build/firmware仍8Lua+CA，项目water-online-0.7.2，保留CORE。
- 两种枚举均只见COM1，未刷写和操作真实输出，实板最近证据仍15:49的0.7.1/UNCONFIGURED。此次失败唯一根因及0.7.2实板效果未确认。已问是否与短信项目同板同卡，未有答复时不得假定相同；旧短信记录电信，当前换水移动46000/CMIOT。详见docs/network-recovery.md最新节。

## 2026-09-12 最新：0.7.1已运行，注册及IP已成功，仍连接超时

- 本机trace在15:49:26记录CEREG:2,1（已注册）、CPIN:READY、CSQ10；15:48:28已获得CMIOT承载IP，15:49:54恢复后再次IP_READY。不能再把15:07的注册拒绝作为当前状态。0.7.1在15:49多条STATUS中得到确认，属于现场trace证据，并非助手刷写或本轮COM4成功实读。
- 15:49:52.373实际执行pdp_recover reason=consecutive_failures，随后重新IP_READY，证明新增恢复路径运行；尚无WATER WS online，不代表TLS已恢复。源码GPIO仍禁用。本轮后段COM4打开报不存在，重新用GetPortNames/PnP确认仅COM1，无LUAT端口。
- 按用户要求检索同类问题：Air724原始论坛帖有TCP成功/SSL失败现象，官方历史AT固件有DNS和加密套件修复，官方FAQ提及物联卡白名单。仅为排查线索，不能把旧AT/新LuatOS修复直接套用当前V4035+LuaTask2.4.4。
- PC域名解析本次返回198.18.0.6，域名TLS EOF；改用真实服务器47.97.255.190并保留bytegallop.com SNI、同一CA及TLS1.2验证后约0.05秒成功。此结果只证明PC直连服务器可用，不代表板端DNS结果或4G路径。公网DNS在服务器解析为47.97.255.190，服务健康。短时SYN抓包因SSH连接中断无有效数据，不得写成服务器未收到板端连接。
- 下一步应分离板端域名解析、纯TCP建连与TLS握手，现有TCPSSL TIMEOUT只是综合连接截止，不能等同证书失败。未改证书校验、APN、CORE或控制GPIO。本轮仅更新调查记录。

## 2026-09-12 接续更正：USB已连接，COM4实读0.7.0

- 不可用Win32_SerialPort只列出COM1推断Air724未连接。该WMI类漏列本机LUAT端口；SerialPort.GetPortNames与Get-PnpDevice -PresentOnly实际确认COM3/4/5/6均存在且OK。后续枚举至少核对这两种来源。
- 本次通过tools/water-command.ps1 -Port COM4 -Command STATUS实读0.7.0/manual/UNCONFIGURED，输出未配置。COM3只读AT诊断在Open时报Access denied，未发送AT、未终止占用程序。
- 用户15:13:25 connecting_tls的后续在15:13:35.279 TIMEOUT，继而按8/16/32/60秒持续重试；15:16:11仍TIMEOUT。固件没有停在STATUS打印，也没有升级为0.7.1。当前注册原因未新增实证，不能把历史CREG:3直接归为当前TLS超时的确定根因。
- 0.7.1生成包及项目已准备，应用源码和后端未再更改；本轮未刷写或发送开水命令。下节“仅COM1、未新读STATUS”为已被纠正的历史观察。

## 2026-09-12 最新：0.7.1重连恢复，实板日志0.7.0

- 0.7.1兼容后端和网页已部署，备份 `/apps/water-auto-exchange/backups/20260912T071450Z-3768177`；容器healthy，四个公网文件与本地一致，API及/sms200，真实凭据WSS probe通过。未向生产注入模拟设备状态或开关命令。
- 用户要求增加断网重试，并报告网络注册被拒绝。用户15:03日志与本机trace确认实板0.7.0/manual/UNCONFIGURED，替代旧0.5.3快照。原retry_ms=2000→4000已是socket退避，不得说旧代码完全没有重试。
- 本机15:07–15:08 trace为CREG:3与2交替、CEREG:2搜索、CSQ10–11，随后waiting_pdp。没有具体拒绝原因码，不认定欠费/锁卡/APN错误；CREG拒绝不能直接当LTE拒绝。询问SIM类型待答复。本轮枚举只见COM1，未发串口AT或刷写。
- 源码/生成包0.7.1：保留1→2→4→8→16→32→60秒无限退避，稳定60秒复位；已注册时连续6次连接失败，或无IP超过120秒，调用官方本机V2.4.4 link.shut恢复就绪状态，至少300秒间隔。它不是射频重启/强制去激活LTE默认承载；未注册时等待底层搜网，不自动CFUN/改APN/切SIM。
- 增加每60秒最多一批只读CPIN/CREG/CGREG/CEREG/CSQ诊断，前批未结束不追加；保留URC处理。IP_ERROR立即作废远程会话、尝试停止所属输出；socket正常断开也先通知控制器再关闭socket。重连不恢复输出或重放旧命令，CA/SNI、互锁、120秒输出上限及原通信超时保留。
- 网页、API和USB工具同时兼容0.7.0/0.7.1手动模式。152项Lua（12+15+19+28+23+33+22）、46项Python和隔离Edge浏览器联调通过，生成包仍8Lua+CA。0.7.1未刷写，GPIO配置仍禁用，未发真实开水命令；继续不要求水流验收。
- 下载项目water-online-0.7.1，所有同类项目仍指向共享build/firmware，不能用项目名称代替包内版本核对。详见docs/network-recovery.md。

## 2026-09-12 最新：0.7.0手动开关，暂不接设备测水流

- 网页与后端已部署，备份 `/apps/water-auto-exchange/backups/20260912T023723Z-3681089`；容器healthy，四个公网静态文件一致、API/sms200、凭据WSS probe通过。未操作实板开关或向生产注入模拟数据。
- 用户最新明确不用水流验证、暂无设备，先提供开关。补水/冲水各一只网页开关，点开FILL/DRAIN、再点STOP关闭；取消按水位到位停止及自动流程入口。不要再追问泵/水流验收作为本阶段前提。
- water_config.mode默认manual，need_fill无需配置且不访问对应GPIO，状态保持unknown；确认输出映射后就能手动控制，不需要液位板。输出映射仍空且禁用，未凭旧候选GPIO猜填。可选超高、两路互锁、默认120秒上限及远程掉线停止保留。
- 当前源码/生成包0.7.0，STATUS新增control_mode，网页要求0.7.0/manual以免向旧自动固件发出错误意图；自动模式及旧协议仅保留兼容。142Lua、45Python、本地Edge无头开关联调和语法检查通过；设备回报前开关不显示成功，失败/未知显示真实状态。
- 本机LuaTools项目water-online-0.7.0已准备、仍完整8Lua+CA。未刷写、未发真实开关命令，最近板端证据为0.5.3/UNCONFIGURED；此阶段不要求实物水流测试。

## 2026-09-12 最新：两个独立按钮与0.6.0

- 页面及后端已部署；备份 `/apps/water-auto-exchange/backups/20260912T022704Z-3677505`。容器healthy，四个公网静态文件与本地一致，API和/sms健康200，凭据WSS probe通过；没有向生产注入模拟数据。
- Web新增并排“补水/FILL”和“冲水/DRAIN”；按已告知的暂定语义，冲水只排到低位停止，不自动补水，完整换水START保留折叠入口。语义问题尚未收到答复。DRAIN已贯通固件、USB、WSS、API及网关，沿用超时/防抖/互锁/故障/断网停止；旧固件禁止DRAIN。
- 源码和build/firmware当前0.6.0，完整8Lua+CA，water-online-0.6.0项目已准备；控制GPIO仍禁用。138Lua、42Python及隔离Edge无头浏览器HTTP联调通过，LuaFLOAT/PS检查通过，未操作真实泵。
- 实板已更新为0.5.3：10:20:41trace启动、10:22左右COM4实读确认UNCONFIGURED。SIM已识别CPIN:READY，但CEREG:2、CSQ8–10并持续waiting_pdp；目前未证实新版成功联网。此前0.5.2的十秒TLS TIMEOUT发生在心跳前，应与旧75秒断线缺陷区分。
- 电脑到公网TLS1.2校验通过、API200，生产设备仍离线、历史0.5.2状态及一条真实STOP保留。本轮未发送START/FILL/DRAIN到生产；COM3打开即占用失败，未发AT。已向用户询问天线与位置。
- computer-use技能已读，本轮工具无node_repl/sky执行入口，未操作UI或刷写；0.6.0尚未上板。此前刷写授权持续有效，阻塞原因是执行工具和现场移动网络，不是待审批。旧LuaTools项目同样指向build/firmware，刷入时务必核对包内VERSION。

## 2026-09-12 最新：真实WSS及STOP已通，0.5.3修正版待刷写

- 用户明确“我要去睡觉了，你自己用computer use测试，一定要测完”，已授权本次助手点击下载并联调，覆盖早先“用户亲自刷写”的限制。01:26:12通过computer-use下载完整0.5.2包成功，原CORE保留；不是src禁用网络包。
- 板端01:26:25及01:27:45等真实 `WATER WS online`，服务器收到0.5.2/UNCONFIGURED状态；真实远程STOP一次回执succeeded/`OK STOP stopped`，未发送START/FILL，泵及液位GPIO仍禁用。
- 稳定性发现缺陷：认证后没有每秒心跳，约75秒断开并重连。官方Air724文档明确 `rtos.tick()` 单位5ms（wiki_page_id=2247），原 `/16` 错误使1秒心跳变约80秒，同样影响控制超时。旧MEMORY/AGENTS中按patch.lua认定16ticks/ms的描述作废。
- 当前源码/生成包0.5.3已改为tick差值乘5；同时修复延迟pong、重复/过期/旧会话pong处理，打印每约5秒seq/ack/age_ms。130项Lua+39项Python、LuaFLOAT及PowerShell检查通过。build/firmware仍8Lua+1CA，私有凭据未提交。本地已准备water-online-0.5.3.ini，需刷新项目列表选择。
- **0.5.3尚未刷入。** computer-use检查Chrome时因无法确认当前URL自动停止本轮界面操作，未重试或绕过界面限制；后续用日志、服务器接口及本地测试完成能继续的工作。板上仍为有心跳缺陷的0.5.2，不能说稳定性测试完成。待恢复工具后刷0.5.3，验证连续心跳、75秒以上稳定连接、重连及实际tick计数。完整证据见docs/verification-20260912.md。
- 服务器已部署0.5.3兼容，备份 `/apps/water-auto-exchange/backups/20260911T175713Z-3513927`。容器healthy、公网API和/sms均200、既有凭据WSS probe通过；容器内无副作用校验确认接受0.5.3。此为后端验证，不表示修正版已上板。

## 2026-09-12 最新接续：0.5.2 已上板，纠正下载项目

- 用户下载后仍显示“等待设备连接”。真实 COM4 STATUS 已确认 `water_auto_exchange 0.5.2 state=UNCONFIGURED`，替代下方板上0.3.0快照；尚未证明板端联网。
- LuaTools 00:49:53 下载成功，但界面仍选 `water-exchange`，清单指向 `src`。最新 `_temp/script/temp_script/water_network_config.lua` 实际为 `enabled=false`、空 device_key，且该目录无CA；版本更新不等于联网配置已下载。trace 在下载后重新接入，只见周期 WATER STATUS，未捕获开机联网日志。
- 已通过界面“刷新列表”并选中 `water-online-0.5.2`，现场确认清单为 `build/firmware` 下8 Lua+1 CA；无需关闭重开工具。联网启用、设备凭据与私有配置一致、CA及9项清单检查通过，未打印或提交密钥。
- 已通知用户在当前项目亲自点“下载脚本”，助手没有点击刷写。接下来核对实际新打包配置、板端WATER NET/WS日志和服务端在线状态；不能提前写成已连通。不需要仅为切项目修改版本或重复部署后端。

## 2026-09-12 前次接续：准备0.5.2联网包（板端0.3.0为历史快照）

- 远程已拉到7482969。COM4实际STATUS为water_auto_exchange 0.3.0、UNCONFIGURED，USB识别正常；该版本没有4G/WSS，替代此前0.2.8板上记录。
- 用户确认网络指示灯GPIO12/物理53/SPI1_DIN，按netLed.setup(true,pio.P0_12)实现；联网启用时初始化，GPIO12从控制器输入/输出集合移除。泵及液位映射仍禁用、未确认，不靠网络灯推定WSS认证成功。
- 当前源码0.5.2，增加PDP/TLS/HTTP升级/认证/重试阶段日志，源码网络关闭会明确打印disabled。后端/USB工具/调试网关增加版本兼容。
- 服务器容器健康、公网HTTPS与WSS+既有设备密钥probe通过；认证后的网页状态离线、device=null、命令数0，未填入模拟设备/未发送控制命令。真实4G还需下载后验证。
- build/device.private.json从既有服务器提取设备凭据；生成包build/firmware/flash-files.txt仍是8 Lua+1 CA共9项，勿分享/提交凭据。用户必须下载完整生成包，不能沿用旧src清单。
- 124项Lua与39项Python测试通过，生成包LuaFLOAT语法通过。当前Python3.10所需测试依赖放build/debug-python，目录被Git忽略。
- 服务器已部署0.5.2兼容，备份/apps/water-auto-exchange/backups/20260911T163813Z-3487003，容器/API/sms健康与包内CA的TLS1.2校验通过。本地新增LuaTools项目water-online-0.5.2，9项均指向build/firmware；已通知用户关闭重开工具后选择该项目下载，尚待下载后的实板验证。

## 2026-09-08 最新接续：集中目录与 Docker 部署

- 用户要求服务器文件统一放 `/apps/water-auto-exchange` 并通过 Docker 启动。现行入口为 `deploy/deploy.ps1` → `/apps/water-auto-exchange/deploy/install.sh`；部署细节见 docs/deployment.md。本节优先于下方分散路径/systemd 历史记录。
- 真实 Docker Engine 29.8.0 专用实例放 runtime/，使用独立 socket；服务器默认 docker 命令仍属 Podman，请用 `bash /apps/water-auto-exchange/deploy/docker.sh ...` 管理本项目。保留其他 Podman 业务及网络。
- 容器 water-console 使用 Python 3.12、UID 10001、只读根目录、host 网络，仅监听 127.0.0.1:8790；unless-stopped 自动启动。数据 data/、私有配置 config/water.env、网页 www/water/、Nginx 项目配置 config/nginx-water.conf；共用宿主 Nginx/TLS，仅在系统目录保留项目符号链接。
- 保留管理员密码与设备密钥，旧 systemd 应用服务停用；旧部署目录、应用、数据库、静态文件、私有配置和单元迁至 backups/legacy-systemd/。更新会先构建，再停旧服务、备份、重建容器并检测；自动回滚保留旧镜像。
- Python 3.12 容器内 39 项 API/WS/网关测试通过，容器重启及重复部署验证通过。首次容器部署备份 backups/20260907T171333Z-1711105/；公网 WSS probe/鉴权拒绝、Web 登录/离线/退出、静态资源一致、/sms 健康和凭据一致检查通过。本轮未操作真实设备。

## 2026-09-08 最新接续：0.5.1 每秒心跳

- 按用户要求，空闲与换水期间的 WSS 应用心跳统一为 1000ms，状态变化继续立即上报。活动通信超时仍为 10 秒，空闲半开检测仍为 75 秒；每秒心跳不表示一秒断网停机。
- 固件 0.5.1 下载包仍为 8 个 Lua 文件和 1 个 CA 证书；后端与 USB 网关增加该版本兼容，保留旧版兼容。
- 本地 123 项 Lua、39 项 Python 测试通过；包含连续每秒心跳边界、活动心跳跨过 10 秒且有回执时保持连接、新版 WSS 接入。官方 LuaFLOAT 检查下载包通过。
- 后端已重新部署，公网 WSS 101、鉴权 probe、错误密钥拒绝及 Web 登录/离线/退出检查通过；未发送设备控制命令。部署备份 `/root/apps/water-auto-exchange/backups/20260907T170538Z-1709504/`。
- 默认 GPIO 仍禁用，本轮未刷写和操作输出。用户重新导入 build/firmware/flash-files.txt 的 9 项后亲自点下载脚本；板端生效需核对 0.5.1 和真实联网日志。溢水保护依赖板端液位反馈和实际接线，不能用网站心跳证明。

## 2026-09-07 最新接续：0.5.0 WSS 长连接

本节优先于下方 0.4.0 HTTP 轮询的历史记录。用户明确要求改用 WS 长连接以降低设备流量。

- 主连接改为 `wss://bytegallop.com/water/api/device/ws`，无自动 HTTP 回退。空闲 30 秒小心跳，状态变化立即上报；活动期间 2 秒心跳、10 秒通信超时尝试停止远程周期。空闲半开连接最多 75 秒检测；正常断线立即离线。
- `water_ws_transport.lua` 使用单一 socket 所有者任务、发送队列和有界帧解析。yield 操作不包在 pcall 中，支持分包/粘包/分片/126 字节边界；不打印认证内容。CA + SNI + insist=0；失败指数退避 1 至 60 秒，稳定 60 秒后重置。永久任务/时钟异常安全停机，等待排查重启。
- 命令采用 offer/claim/execute/ack，8 秒有效期与 claim 本地往返时间共同防止过期执行，重复 ID 不执行两次。USB STOP 清除 pending 并关闭会话，重连不恢复旧命令。
- 下载包更新为 **8 个 Lua 文件 + 1 个 CA 证书**（共 9 项），精确列表由 `tools/build-firmware.py` 生成到 `build/firmware/flash-files.txt`。板端版本 0.5.0；GPIO 仍禁用且未映射。用户亲自下载，不能代刷。
- 后端增加 wsproto 端点和独立依赖目录，API 1.1.0，Nginx 精确 WSS 路由已上线；管理员密码与设备密钥均保留，不写入 Git。旧 HTTP 端点仅保留手动 USB 调试兼容。
- 测试：122 项 Lua、39 项 Python API/WS/网关测试通过。隔离的真实 WS 连接跨过 31 秒空闲后心跳、命令和回执成功；公网 101、鉴权 probe、错误密钥拒绝、Web 登录与离线状态、/sms 健康已验证。probe 不向线上写入模拟设备状态或命令。
- 本轮仍未刷写、未操作物理输出；实板 4G/WSS 长时间稳定性、运营商流量和真实水流尚待核验。沿用历史 0.2.8 STOP 记录，不将网站/API 成功作为实际设备联网证明。

## 2026-09-07 最新接续：0.4.0 + Web 控制台

本节优先于下文 0.3.0 和静态网站阶段的历史描述。

- 用户要求“继续完成功能和web端”，并明确设备需通过 **4G 独立联网**。已实现 `water_network.lua` / `water_network_config.lua`，基于旧 LuaTask V2.4.4 的 HTTPS 回调接口，启用 CA 校验和 SNI；保留 USB 调试网关。
- 板端源码升级为 **0.4.0**，控制状态机与未确认的 GPIO 配置保持原状。默认源码的网络也禁用且没有凭据；`tools/build-firmware.py` 仅在被忽略的 `build/firmware/` 中启用 4G 和填入设备密钥。
- 下载清单为 7 个 Lua 文件及 `water-ca.crt`，精确列表在 `build/firmware/flash-files.txt`；全部使用同一生成目录，保留原 CORE 和默认库。JSON 使用 CORE 全局模块，不 require 不存在的 json.lua。用户亲自点击下载，禁止代刷。
- 远程会话重建取消旧命令；8 秒命令有效期、单次交付、回执跟踪、STOP 优先。5 秒网络请求总截止及 10 秒通信看门狗会尝试停止远程启动周期；USB STOP 作废在途远程响应。网络请求不重叠，不自动恢复泵。全部输出仍由原适配器检查互锁/液位/超时。
- 网站新增登录、在线状态、液位请求、软件输出、故障、START/FILL/STOP/RESET、最近 60 条操作记录和手机布局。Python 标准库 + SQLite API 绑定 127.0.0.1:8790，由 Nginx `/water/api/` 代理，systemd 非特权用户运行。管理密钥与设备密钥分离、会话 Cookie、Origin 校验。部署说明见 docs/deployment.md。
- 本地测试：原有 82 项 Lua + 20 项网络 + 3 项启动集成，共 **105 项**通过；Python 后端/网关 **28 项**通过；Edge 浏览器真实 HTTP 联调通过，覆盖登录、离线、提交/取消/回执、停止、补水条件、故障、XSS 文本、手机布局、掉线和退出。官方 LuaFLOAT 全部 src 语法与 PowerShell 解析通过。
- 本轮电脑未枚举到串口，未刷写、未启动真实输出。最近历史实读仍是已 STOP 的 0.2.8。GPIO、液位板接线、Air724UG 实板 TLS/4G 及真实水流都仍需核验。不得把模拟设备或公网网站健康写成实板在线。
- 当前服务器证书链根为 AAA Certificate Services，公开 CA 放 certs/，并已用服务器 OpenSSL 验证。物理设备握手待验证，不允许去掉 CA 来绕过失败。
- 已部署并实测公网 HTTPS 登录/退出、设备离线与控制禁用、空操作记录及桌面/手机布局；四个静态文件与本地逐字节一致。API health 200、匿名 status 401、缺失资源 404、/water 308 保留查询参数，主站及 /sms 健康正常，Nginx 检查通过、water-console 服务 active。未向线上设备发送测试命令。部署备份：`/root/apps/water-auto-exchange/backups/20260906T182029Z-1468822/`。
- 本轮凭据与准备包均放 build/（Git 忽略）；详细用法见 docs/web-console.md。不要在聊天、日志、Git 中打印密钥。后续修改验证后仍须使用中文标准提交信息 commit/push。


更新日期：2026-09-06。用于快速接续本项目；全仓库工作约定和详细实测历史见 [AGENTS.md](AGENTS.md)。用户的新指示与新实测结果优先。

## 当前成果

- 项目用途：鱼缸/水箱的水位监测、自动排旧水和补新水。用户已认可现有三探针液位板方案并要求写代码。
- 当前源码：`water_auto_exchange 0.3.0`，旧版 LuatOS-Air / Lua 5.1 FLOAT / LuaTask V2.4.4。保持既有 CORE `LuatOS-Air_V4035_RDA8910_TTS_NOLVGL_FLOAT`。
- 单次换水流程：`START` 排水到低位，关排水并等待 1 秒，再补水到高位，全部关闭后保持 `DONE`。`FILL` 只补水到高位；`STOP` 停止；`RESET` 清除已恢复的故障并回待机。
- 正常输入每 100ms 采样、防抖 500ms；排水/补水各 120 秒初始超时，需结合实际容积与流量调整。包含进排水互锁、故障锁存、输出写入异常清理和可选超高输入。额外软件输入不等于独立硬件断流。
- 开机待机，立即打印状态，每 5 秒继续打印；不再开机扫描 GPIO。无需 SIM/网络；当前先手动命令启动一轮，无定时计划、无空闲自动补水、无断点恢复。
- 配置文件 `src/water_config.lua` 保持 `enabled=false`、`mapping_confirmed=false`、`wiring_confirmed=false`，GPIO 与有效电平均未填。默认 `UNCONFIGURED`，程序不配置输出。不能将待机源码描述成已完成实机换水。
- 0.3.0 已通过 82 项本地 Lua 5.1 模拟测试：状态机17、硬件适配器24、USB/启动14、旧电机/USB15、旧诊断12；官方 LuaFLOAT 语法、PowerShell 解析、LuaTools 文件清单/依赖/版本核对通过。

## 板上实测与接线边界

- 定制板：工科物联 `GK21-SPTM_rev0.3`，模组 `Air724UG-NFM`，不是官方开发板。
- 最近实读板上仍是 `gk21_motor_test 0.2.8`：2026-09-06 11:09:33 左右确认 GPIO13/22/23 三脚、30 秒循环，随后收到 `OK STOP stopped`。**0.3.0 尚未由用户刷入并核对**；旧程序重启仍会自动扫描。
- GPIO5 / 模块物理49脚：用户在 HIGH 阶段测得出纸端约 6.1V；LOW 是否关断未确认。GPIO12 / 物理53脚：用户确认 HIGH 阶段对应板上灯，LOW 未单独确认。
- GPIO23 / 物理8脚：DO2 在 HIGH 和 LOW 阶段均约 12V，STOP 后用户实测近 0V。STOP 同时写 LOW 和释放脚，不能据此确定 `on_level=1/off_level=0`。GPIO23 是 DO2 强候选，尚未确定启停方法；它还与 SIM 在位检测 USIM_CD 复用，未查询 CORE 的实际检测配置。
- `TP18/TP29/TP19/TP20/TP21` 是定制板测试点编号，不是 GPIO 编号；用户早期带电蜂鸣/电阻测量不是可靠连通性证据。详细观察按 AGENTS 保留，不能据图猜成已验证网表。
- 用户已有 12V 三探针液位控制板的卖家图：A 最低公共探针、B 低位开始补水、C 高位停止。输出为**一路迟滞继电器状态**，不是两个独立水位信号，也不能测连续水深；B/C 之间保留前一状态。
- 12V 是液位板供电；图中继电器泵侧另接市电。反馈到 Air724 必须使用确认隔离且无外来电压的干接点，或匹配 1.8V 的隔离接口，不能直连探针、12V 或泵侧带电触点。液位板自动补水不能绕过程序的补水许可。

## 下一步所需信息

1. 确认补水/排水设备型号、电源与驱动方式，并实测每个控制输出的开启及关闭电平。
2. 确认液位板反馈 GPIO、触点隔离和请求补水的有效电平，实测低于 B、到 C 及两者之间的保持状态；确定探针和实际水质适用性。
3. 更新映射与超时参数后，由用户下载脚本并联调；先核对 `STATUS` 项目/版本，再验证实际水流和关断。
4. 用户尚未答复泵/阀规格、是否定时启动；不要填入猜测的设备配置或定时计划。独立超高保护尚未采购或接线确认。

## 使用与协作约定

- 中文直接沟通，延续已确认的信息。用户亲自操作 LuaTools“下载脚本”，保留原 CORE；完成全部文件和检查后再通知下载。
- 本地 LuaTools 项目 `tools/vendor/luatools/project/water-exchange.ini` 已配置五文件：`main.lua`、`water_config.lua`、`water_cycle.lua`、`water_control.lua`、`water_usb.lua`。若界面仍是旧清单，重新载入核对。默认库已含 `sys`，每个 `require` 单独一行以兼容依赖解析。
- 新主机工具：`tools/water-command.ps1 -Port COM4 -Command STATUS`，支持 `STATUS/START/FILL/STOP/RESET`，先核对 `water_auto_exchange 0.3.0`。COM 编号可能变化；旧 motor/probe 命令仅用于历史诊断。
- `fill/drain` 是软件记录，不是电压或流量测量；`outputs_known=0` 表示未初始化或写入结果不确定。API 使用旧 `pins/pio/sys`，不能套新版 `gpio.setup`。
- Git：分支 `main`，远程 `git@github.com:princehaku/water-auto-exchange.git`。用户最新持续要求：每次完成代码修改并验证后都要 commit 和 push；使用中文标准提交信息（feat:、fix: 等），提交前同步必要的项目记录。
- `.gitignore` 排除 `logs/`、`build/`、`tools/vendor/` 及固件二进制。此次同步代码、测试和文档，不把工具包、驱动、日志或临时照片加入仓库；其他机器按 README 重建本地 LuaTools 项目。

详细使用见 [自动换水说明](docs/water-control.md)，硬件依据见 [板级分析](docs/board-control-analysis.md)，原始诊断过程见 [诊断历史](docs/diagnostic-history.md)。

## 网站部署接续（2026-09-06）

- 仓库已克隆到 `E:\water-auto-exchange`，分支 `main`。
- 新增独立入口 `https://bytegallop.com/water/`；当前为静态项目页，设备网络接入和网页控制尚未配置。网站健康状态不表示设备在线。
- 部署资料统一放在 `deploy/` 和 [docs/deployment.md](docs/deployment.md)，包含环境路径、SSH 端口、更新、备份和回滚；后续部署先读该文档。
- 网站上线不改变板端源码版本、刷写状态或 GPIO 配置。原有硬件约束继续有效。
- 已验证公网跳转、页面/健康文件内容、缺失资源 404 和 `/sms` 健康；Nginx 配置检查通过，浏览器确认入口正常显示。
