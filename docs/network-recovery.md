# 0.7.2 连接等待、网络恢复与诊断

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
