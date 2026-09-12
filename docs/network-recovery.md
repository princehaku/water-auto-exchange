# 0.7.7 WSS省流量、指示灯与网络恢复

## 2026-09-13：0.7.7省流量与工作常亮灯

用户确认采用待机30秒、工作1秒心跳、每5分钟上报流量，覆盖此前空闲也每秒心跳的要求。`src/water_network_config.lua`设置`heartbeat_ms=30000`、`active_heartbeat_ms=1000`、`traffic_interval_s=300`；统计周期按秒传给当前LuaTask V2.4.4的`socket.setIpStatis`，允许60–3600整数秒，省略时默认300。无统计API仍可联网。

WSS持续保持，服务端推送命令不等下一次心跳；执行命令时刷新活动通信计时，工作期间仍每秒心跳。状态变化下个500ms网络检查立即上报，USB/trace仍每5秒打印；流量上报变慢不延长活动10秒失联停止、75秒空闲检测或单次120秒输出上限。120秒到期关断并锁存`fill_timeout`/`drain_timeout`，需要复位；重连不恢复输出。网页仍每2秒查询服务器，流量数字取最新已上报样本。

GPIO12/物理53由`netLed`单独控制；真实输出记录明确开启时，所有网络状态（包括未联网/搜网）都设为常亮，停止后恢复V2.4.4原有各状态闪烁时长。复用`updateBlinkTime(state,65535,0)`，不另启GPIO写入任务与库争用。控制器在应用输出后通知灯效，异常被隔离，不能阻止STOP；USB独立控制也能亮灯。常亮反映软件输出记录，不是水流或电压反馈。灯闪烁与WSS心跳独立，不消耗SIM数据。

截至00:17，真实日志/API已确认0.7.6、流量持续上报；旧卡联通（3gnet），新卡移动（CMIOT），23:51:26重启由用户换卡触发。移动卡00:03:41上线后至00:17仍有通信；此前两卡均出现过超时，短时稳定不能证明网络问题永久解决。00:17真实状态为FAULT/drain_timeout、fill=0/drain=0。

旧配置两段60秒样本12844/12959字节，折算约557MB/30天，属于库估算而非运营商账单。新配置按每天工作10分钟、其余待机，模型约25–40MB/30天；重连、TLS和重传会影响实际值。此数不是0.7.7实测节省。5分钟未上报区间在掉电时可能丢失。

源码/生成包0.7.7，下载项目`water-online-0.7.7`，仍8Lua+CA、原CORE与默认库。LED及省流量需要用户下载脚本；本轮没有恢复Escape停止的桌面输入或操作真实输出。

本地195项Lua（12+15+20+36+23+53+36）、72项Python及Edge桌面/手机联调通过；覆盖30秒待机边界、服务端主动命令、活动1秒心跳与10秒停止、5分钟统计、75秒空闲期限、GPIO灯效切换/超时恢复及灯效异常不阻断STOP。LuaFLOAT、JS、PowerShell语法通过；9项生成包/版本/源码/私有凭据/超时/输出映射/原CORE核对通过。测试未操作真实GPIO，不等同0.7.7实板验证。

后端/网页0.7.7兼容已部署，备份`/apps/water-auto-exchange/backups/20260912T161911Z-3944482`；容器healthy/restart=0，公网四静态文件与本地一致，TLS/WSS凭据probe、HTTPS登录/status/退出、999天会话和water/sms健康通过。部署前确认真实两路均关闭；本轮未注册模拟设备或发送开水命令。实板仍需用户下载0.7.7，服务端更新不会改变板端心跳或灯效。

同一设备重连时，0.7.6的auth携带previous_session，密钥验证成功且标识匹配当前会话后可立即交接连接；无标识/旧标识仍遵循旧会话超时回收规则。旧线程退出不影响新会话，控制命令不重放。

## 2026-09-12：0.7.6认证等待和超时会话回收

19:02:35.115完整板端日志实际已有online，之后ack增长；所以认证已成功，反复看到upgraded/authenticating是后续断开后的新连接流程。后段20:15保存日志为CPIN READY、CEREG:2,0、CSQ99和UNREGISTER，不能当作当前实时状态。本轮SSH短时拒绝握手后于23:20恢复，Nginx/API/容器检查正常；服务器历史日志明确多次another_device_active，但没有证据证明它解释全部发送超时。

服务端auth等待由5秒改为30秒，板端auth_timeout_ms默认30000，允许5000–60000整秒毫秒值。认证完成后不会周期性重新认证。活动输出依旧10秒无有效回执停止，空闲期限75秒。旧WS会话超过相应期限后，新认证连接可清理其占用并接管；健康会话仍拒绝另一连接抢占。旧会话待确认命令标为不确定，旧线程退出不能关闭新会话或恢复输出。

新增SQLite连接事件和最近快照，断线保留最近通信时间，服务重启保留历史但保持离线，Web默认展开详情。设备/API不可达分开表示，不能把浏览器请求失败直接写作设备离线。

流量采用本机LuaTask V2.4.4 socket.lua的setIpStatis(60)和LIB_IP_STATIS_RPT，源码socket4G.lua按payload和固定开销累加：TCPSSL建连800、TCP收发各加80、TCP关闭120字节等。故Web明确标注估算流量；不称SIM计费实测，不凭它推算确切套餐余额。报告为每次脚本运行的累计值，meter id由首次认证ready的服务器会话值生成并跨重连保留，直到脚本重启；已上报值持久去重。未上报掉电数据不保证保存，SDK没有产生样本时显示等待统计。

本轮189Lua、70Python及桌面/手机Web联调通过，0.7.6固件包已准备。模拟和PC网络检查不能代表板端4G问题已经消失。

最终部署备份`/apps/water-auto-exchange/backups/20260912T152752Z-3926877`。容器healthy，公网静态/API/WSS与999天登录验证通过。189Lua、70Python及本地浏览器通过；本地85秒单连接ping/pong通过。真实设备仍0.7.5，观察时last_seen后段停更，虽然最终部署后再次在线，也不能断言长期稳定；0.7.6流量及会话交接需用户下载后验证。

## 2026-09-12：0.7.5处理上线后发送超时

用户17:22日志已确认0.7.4就绪，TLS在7520ms完成，随后WS认证online；约6秒后socket:send TIMEOUT。这次不是TLS超时。心跳seq=4/ack=0只说明4次心跳已入队且尚无有效pong，不能说4条已经发到服务器。对照本机LuaTask V2.4.4源码，water旧实现send(...,5)只等5秒；SMS所用websocket.lua发送未指定超时，socket4G默认等待120秒。现改为有界30秒，仍不照搬旧库的过长默认值。

```lua
tls_connect_timeout_ms = 60000,
send_timeout_ms = 30000, -- 5000到60000，必须为整秒的毫秒值
long_connection_cert = nil,
```

HTTP升级请求和WS帧使用该发送预算，底层传秒；升级收包和应用认证另有原期限，因此30秒不是总握手期限。活动输出仍由独立500ms定时回调检查，10秒没有有效应用回执便停止所属输出并作废会话。发送等待可继续清理旧socket，但不能重新打开输出或发送遗留队列。空闲检测75秒，每秒心跳和原退避/PDP恢复保留。

旧实现先清空发送队列再收包，慢链路下每秒产生的心跳可能延迟回包处理。新版每发送一帧便调用recv处理缓冲回包；只合并尚未发送的ping，保留最新seq。auth/claim/ack/status不合并、不重放。最多8条待发帧，异常仍关闭会话。

新增日志字段：`tx`是底层确认发送帧数（含auth），`queued`是待发帧数，`sending=1`表示底层发送尚未返回；`seq`仍是入队的心跳序号。发送耗时超过1秒或失败时记录`send_result/kind/attempt/bytes/elapsed_ms/timeout_ms`。收到WS关闭帧打印`peer_close code`；服务器记录白名单拒绝原因如`another_device_active`，其他异常只记录`protocol_or_internal_error`，不写帧内容、密钥或任意异常详情。新日志不能证明历史认证断开的原因。

`+CESQ:99,99,255,255,4,6`最后两项按标准CESQ量化对应RSRQ[-18,-17.5)dB、RSRP[-135,-134)dBm，提示弱信号，但一个样本不能认定本次失败唯一由信号导致。量化参考[合宙CESQ标准字段说明](https://docs.openluat.com/air780e/at/app/Command_List/Network_service/CESQ/)（该网页为Air780E系列，引用的是3GPP同名字段定义，不据此替代Air724固件说明）。已询问4G天线是否接好并移至窗边对比，等待用户反馈。

183项Lua、58项Python及Edge本地HTTP开关联调通过，覆盖慢发送、收发交替、心跳合并、队列上限、取消后迟到成功不重放、10秒活动停止及0.7.5端到端兼容。实板最近仍0.7.4，0.7.5需要下载验证；没有恢复被Escape停止的computer-use或发送实物开关命令。

0.7.5后端兼容已部署，备份`/apps/water-auto-exchange/backups/20260912T093214Z-3813795`；容器healthy，公网静态文件/WSS probe/登录与999天会话/健康检查通过。下载包9项、项目及官方LuaFLOAT语法检查通过，板端仍待下载。

## 2026-09-12：按用户要求与短信WSS参数一致

用户明确要求证书配置也改成和sms-forward一致，覆盖下文0.7.2保留强制CA/SNI的旧约定。0.7.3默认配置为：

```lua
long_connection_cert = nil,
tls_connect_timeout_ms = 60000,
```

传给旧LuaTask库的调用是`socket.tcp(true, nil)`。仍走TLS/WSS加密，但默认不校验服务端证书、不启用SNI，采用与短信项目未配置证书表时相同的底层默认值。设备应用密钥认证保留；没有新增HTTP回退。要启用CA和域名校验，可以显式设置：

```lua
long_connection_cert = {caCert="water-ca.crt", hostNameFlag=1, insist=0},
```

显式配置CA却找不到文件时仍拒绝连接，不会自动切换到无校验。证书表每次连接复制，避免底层改写文件路径影响重试。日志真实显示`ca=disabled sni=disabled`或对应启用值。包内保留water-ca.crt便于以后开启校验，默认不读取它；旧顶层ca_cert配置已改由long_connection_cert.caCert指定。

本版保持60秒建连、每秒心跳、活动10秒失联停止、退避/PDP恢复、TCP诊断及输出互锁。166项Lua、48项Python与Edge本地HTTP联调通过，完整8Lua+可选CA资源已生成。COM4在本轮下载前确认运行0.7.1/UNCONFIGURED；后续上板与联网结论另行记录。

- 已通过computer-use刷新项目列表、选water-online-0.7.3并点击下载脚本，16:29:47.736工具报告下载成功（保留CORE）。随后COM4实读0.7.3/manual/UNCONFIGURED；临时打包脚本版本、enabled=true、long_connection_cert=nil及私有密钥匹配均核对通过。
- 16:30:08已注册并IP_READY；16:30:09.239实板打印ca=disabled sni=disabled，底层TCPSSL证书参数nil、timeout60，证明确实按SMS默认参数运行。截止最后读取16:31:01尚未看到本轮online。用户随后按物理Escape停止computer-use，立即停止所有后续UI操作，不将其写成联网修复成功。
- 0.7.3后端/网页已部署，备份`/apps/water-auto-exchange/backups/20260912T082733Z-3791828`。四个公网静态文件一致、water/sms健康200，PC以CA/SNI及SMS式无CA/无SNI两种TLS1.2均完成凭据WSS probe，未注册模拟设备或发开水命令。后续SSH健康查询因banner超时未取得新结果；不将部署后的PC检查当板端联网证明。

## 2026-09-12：与 sms-forward 对照后的修正

本节为最新结论。已修复连接等待过短的问题并增加分层诊断；这次现场连接失败的唯一根因仍未确定，不能将本地验证写成实板联网恢复。

### 凌晨确实连通过

本机 `trace_2026-09-12_002549.txt` 中，0.5.2 在01:55:08.820开始TCPSSL连接、01:55:11.091连接成功、01:55:11.995打印 `WATER WS online`；01:55:43.758继续发送数据并处理控制器状态。前一个连接也在01:52:53.199完成认证。说明同一主机及当时的CA/SNI参数曾经能在板上工作，不支持“板子始终不支持TLS”的判断。

旧0.5.2时基错误导致心跳过慢，0.5.3已修正；下午日志停在TCPSSL连接、尚未到WebSocket/认证，不能直接用旧心跳问题解释。01:55这一连接在01:56:37有发送超时，历史成功不等于长期稳定。

### 对照结果

比较对象为 `E:/sms-forward/device/main.lua`、`sms_center.lua`、`config.example.lua` 和本机官方LuaTask V2.4.4库。短信目录中没有实际 `device/config.lua` 或运行trace，因此示例配置不当成板端已确认值。短信AGENTS记录过电信卡，换水最新日志为移动46000/CMIOT；是否同板同卡待用户确认。

| 项目 | 短信项目源码/示例 | 换水项目原0.7.1及本次处理 |
| --- | --- | --- |
| 主机/端口 | bytegallop.com:443，/sms路径 | 同主机443，/water路径；TLS成功后才发送HTTP路径 |
| CORE | 项目记录V4035 FLOAT | 同系列V4035 FLOAT，保留现有CORE |
| 建连等待 | HTTPS 30000ms经http库换算为30秒 | 原10秒，0.7.2改60秒，可配置15–120秒整数秒 |
| WSS库单位 | 传入30000；本机旧websocket库原样交给以秒计的connect/send，存在单位混用 | 继续使用自有单任务传输，毫秒配置明确除1000，recv仍毫秒；不照搬30000 |
| TLS验证 | HTTP只传hostNameFlag=1；WSS传可选long_connection_cert，示例未配置CA | 保留water-ca.crt、hostNameFlag=1、insist=0；凌晨已有实板成功依据 |
| 在线机制 | WSS加HTTPS回退，HTTP成功也打印smsCenter.device online | 继续WSS单连接，不引入HTTP命令回退；WATER WS online表示设备认证完成 |
| 心跳 | 示例60秒HTTP、600秒WS状态，服务端也可下发间隔 | 每秒应用心跳；活动10秒失联停止，空闲75秒，保持原规则 |

只读服务器访问日志尾部也有 `/sms/api/device/ws` 的101记录（最后一条日志时间12/Sep/2026:13:49:18 +0800）。这证明发生过WS升级，日志时间可能是请求结束时间，不能当作目前仍在线或设备认证成功。短信项目未修改。

### 本次实现

- `tls_connect_timeout_ms=60000`，对底层调用传60秒；增加成功/失败耗时、注册状态与PDP就绪日志。连接等待与输出通信看门狗分开，延长重连等待不会延长开水后的10秒失联停止。
- 连续连接失败计数达到3、当次为TLS建连失败时，在关闭旧会话和旧socket后，对同一域名443只建立一次普通TCP再关闭。不发送HTTP、设备密钥或控制指令，不算上线，不作为TLS回退。两次诊断至少间隔300秒；未注册/无IP时跳过。
- `tcp_probe result=reachable` 只说明当次域名解析及TCP路径可用，仍需看TLS/认证；`failed`时优先查DNS/数据出口/目标可达性。这个检查不提供DNS解析出的IP，也不能用两次不同时刻的结果证明唯一根因。
- 保留无限指数退避、受限频率的link.shut恢复、CA/SNI、USB STOP、输出互锁与重连不恢复旧命令。

新日志示意（耗时为示例）：

```text
WATER WS connecting_tls host=bytegallop.com timeout_ms=60000 ca=enabled sni=enabled
WATER WS tls_connected elapsed_ms=25000
WATER WS upgraded; authenticating
WATER WS online
```

失败时会打印 `tls_connect_failed elapsed_ms=... registration=... pdp_ready=...`；具体TIMEOUT/RESPONSE仍由底层socket日志提供，应用不能从单一false返回值伪造错误码。

0.7.2本地验证：160项Lua（12+15+19+28+23+35+28）、47项Python和Edge真实本地HTTP联调通过；覆盖25秒连接、毫秒/秒换算、迟到成功取消、TCP诊断无业务发送/关闭/冷却/协程yield、长连接预算不延长10秒停止、0.7.0/0.7.1/0.7.2兼容。完整8Lua+CA下载包及water-online-0.7.2项目已准备。SerialPort.GetPortNames与PresentOnly PnP均只列COM1，本轮未刷写、未发送实板开水命令；最新实板版本沿用15:49的0.7.1。

- 0.7.2兼容后端/网页已部署，备份`/apps/water-auto-exchange/backups/20260912T082015Z-3789216`。容器healthy，四个公网静态文件与本地一致，water/sms健康接口200，保留CA/SNI的TLS1.2及既有凭据WSS probe通过；probe未注册模拟设备、未提交控制命令。

官方依据：[Air724 TCP说明](https://docs.openluat.com/air724ug/luatos/app/socket/tcp/)、[HTTP说明](https://docs.openluat.com/air724ug/luatos/app/socket/http/)，具体调用单位另以本机实际V2.4.4 `socket4G.lua`、`http.lua`、`websocket.lua`源码核对。

## 2026-09-12 最新：0.7.1已运行，注册及IP已成功，仍连接超时

- 本机trace在15:49:26记录CEREG:2,1（已注册）、CPIN:READY、CSQ10；15:48:28已获得CMIOT承载IP，15:49:54恢复后再次IP_READY。不能再把15:07的注册拒绝作为当前状态。0.7.1在15:49多条STATUS中得到确认，属于现场trace证据，并非助手刷写或本轮COM4成功实读。
- 15:49:52.373实际执行pdp_recover reason=consecutive_failures，随后重新IP_READY，证明新增恢复路径运行；尚无WATER WS online，不代表TLS已恢复。源码GPIO仍禁用。本轮后段COM4打开报不存在，重新用GetPortNames/PnP确认仅COM1，无LUAT端口。
- 按用户要求检索同类问题：Air724原始论坛帖有TCP成功/SSL失败现象，官方历史AT固件有DNS和加密套件修复，官方FAQ提及物联卡白名单。仅为排查线索，不能把旧AT/新LuatOS修复直接套用当前V4035+LuaTask2.4.4。
- PC域名解析本次返回198.18.0.6，域名TLS EOF；改用真实服务器47.97.255.190并保留bytegallop.com SNI、同一CA及TLS1.2验证后约0.05秒成功。此结果只证明PC直连服务器可用，不代表板端DNS结果或4G路径。公网DNS在服务器解析为47.97.255.190，服务健康。短时SYN抓包因SSH连接中断无有效数据，不得写成服务器未收到板端连接。
- 下一步应分离板端域名解析、纯TCP建连与TLS握手，现有TCPSSL TIMEOUT只是综合连接截止，不能等同证书失败。未改证书校验、APN、CORE或控制GPIO。本轮仅更新调查记录。

本版延续补水/冲水手动开关，无需水位传感器或水流验收。网络恢复不恢复之前的开启状态，也不重放失效会话的命令。GPIO配置仍默认禁用。

## 重试规则

- TLS、HTTP升级、socket创建或连接失败后，关闭旧socket并持续重试，等待1、2、4、8、16、32、60秒；之后保持最多每60秒重试一次。每次连接自身仍有超时，实际两次尝试间隔还包含本次连接/清理时间。
- 连接稳定达到60秒后，退避和失败计数恢复初始值。
- 已注册网络但连接连续失败6次，调用`link.shut()`尝试恢复本地链路状态；已注册却持续拿不到IP超过120秒，也走这条恢复路径。两次恢复至少相隔300秒，避免频繁打断搜网。
- 已核对本机LuaTask V2.4.4 `lib/link.lua`：`shut()`清除CELLULAR就绪标志、发布IP_ERROR_IND；已注册时约2秒后重新检查/激活链路。LTE默认承载不能据此宣称已被强制去激活，此操作不是射频复位。
- 未注册时，每秒检查是否拿到IP，每约5秒输出`waiting_pdp registration=UNREGISTER`，由底层继续搜网。程序不执行CFUN、恢复出厂、修改APN或切换SIM。
- 失败/等待期间每60秒最多提交一批只读`CPIN? / CREG? / CGREG? / CEREG? / CSQ`查询；上一批没有结束就不追加，避免AT队列堆积。结果由原库处理/输出，保留既有URC处理函数。诊断不查询设备密钥或SIM标识。
- `IP_ERROR_IND`立即使应用会话失效，并尝试停止该远程会话开启的输出；正常断开也先通知控制器、再执行可能等待的socket关闭。原10秒活动通信超时、75秒空闲检查、CA/SNI验证和永久异常停机策略保留。

典型新增日志：

```text
WATER WS retry_ms=4000 failures=3
WATER WS waiting_pdp registration=UNREGISTER
WATER NET diagnostic csq=11; see CPIN/CREG/CGREG/CEREG above
WATER WS pdp_recover reason=consecutive_failures cooldown_ms=300000
WATER WS pdp_recover reason=pdp_wait_timeout cooldown_ms=300000
```

重连成功仍需看到`WATER WS online`及持续增长的心跳seq/ack。仅出现connecting_tls、IP_READY或重试日志均不表示应用已在线。

## 2026-09-12 下午现场证据

用户15:03提供的日志确认运行0.7.0/manual/UNCONFIGURED，TLS失败类型RESPONSE，随后由2秒退避到4秒，说明原有socket重试已经执行。

本机`trace_2026-09-12_150212.txt`在15:07至15:08显示`+CREG: 3`、`+CREG: 2`交替，LTE的`+CEREG: 2`仍在搜索，CSQ约10–11，随后持续waiting_pdp。这是注册未完成的证据，尚没有足以定位原因的拒绝原因码。CREG和CEREG代表不同注册域，不能将CREG的3直接当作已经读取到LTE拒绝原因。此时net库的no match提示也不能当作服务器认证失败。

已向用户询问普通手机卡/物联网卡类型，尚待答复。运营商卡状态、物联网卡设备绑定/业务开通、覆盖等还未核实，不据此断言欠费、天线坏或APN错误。重连不能绕过运营商的注册拒绝。

后续更正：Win32_SerialPort漏列了LUAT的USB端口，不能据仅列COM1推断未连接。SerialPort.GetPortNames及Get-PnpDevice -PresentOnly确认COM3/4/5/6都在且OK；本次COM4 STATUS实读0.7.0/manual/UNCONFIGURED。COM3在Open时报Access denied，没有发出AT查询，也未终止占用程序。0.7.1仍未刷写。

用户15:13:25的TLS连接在15:13:35.279返回TIMEOUT，随后8/16/32/60秒持续退避，至15:16:11仍TIMEOUT。程序持续运行，尚未取得本次TLS失败的确定根因，不能仅用此前注册日志代替当前注册证据。

## 验证与下载

152项Lua（12+15+19+28+23+33+22）和46项Python通过；Edge本地HTTP联调验证0.7.0/0.7.1手动模式兼容、开启/关闭回执、互锁及离线禁用。新增测试覆盖退避封顶后持续重试、链路恢复阈值/冷却、未注册等待、AT队列有界、IP丢失立即通知、时钟回绕和重连不恢复旧输出/命令。上述为模拟验证，未模拟写入生产设备状态。

完整下载包仍为build/firmware下8个Lua文件与1个CA，LuaTools项目`water-online-0.7.1`，保留原CORE。下载后确认版本0.7.1，并检查注册/IP/WSS及心跳；尚未实证本版能恢复这次运营商注册异常。

0.7.1兼容网页/后端已部署，备份`/apps/water-auto-exchange/backups/20260912T071450Z-3768177`。容器healthy，公网四个静态文件与本地一致，API和/sms健康200，既有凭据WSS probe通过；未向生产提交设备模拟状态或开水命令。

## 同类问题检索来源（2026-09-12）

- [原始Air724问题帖，2022-03-17](https://whycan.com/t_7805.html)：发帖人描述私有EMQX普通TCP可连，改为SSL和证书后连接失败。未取得与本项目相同版本及TIMEOUT的已解决复现，不能照搬帖子里的证书关闭操作。
- [合宙Air724UG历史AT固件记录](https://docs.openluat.com/air724ug/at/firmware/)：记录过DNS解析慢/失败及SSL加密套件兼容问题。这是旧AT版本的历史，不证明当前Lua V4035有相同缺陷。
- [合宙FAQ 2026-08-13](https://docs.openluat.com/faq/2026-08-13/)：有物联卡访问目标域名/IP受定向白名单限制的问答。用户卡类型仍未确认，CMIOT及10.x承载地址本身不能证明限制存在。
- [Air724UG HTTP官方说明](https://docs.openluat.com/air724ug/luatos/app/socket/http/)：说明CA及hostNameFlag域名上报参数；本项目已有CA校验与hostNameFlag=1，不能简单归因漏配SNI。

检查顺序建议：板端读取域名解析目标、对目标IP只建TCP不发业务密钥，再以保留域名校验的TLS连接对照。纯TCP也失败优先查数据出口/目标可达性；TCP成功而TLS失败再定位CA/时间/协议套件和握手期限。不要把PC假IP解析结果当作板端结果，也不要从综合TIMEOUT日志直接断言证书错误。
