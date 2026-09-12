# 0.7.1 网络恢复与注册诊断

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

本轮枚举仅见COM1，未对串口发送AT命令、未刷写0.7.1。最新板端版本证据来自用户日志和本机现有trace，而不是本轮新STATUS实读。

## 验证与下载

152项Lua（12+15+19+28+23+33+22）和46项Python通过；Edge本地HTTP联调验证0.7.0/0.7.1手动模式兼容、开启/关闭回执、互锁及离线禁用。新增测试覆盖退避封顶后持续重试、链路恢复阈值/冷却、未注册等待、AT队列有界、IP丢失立即通知、时钟回绕和重连不恢复旧输出/命令。上述为模拟验证，未模拟写入生产设备状态。

完整下载包仍为build/firmware下8个Lua文件与1个CA，LuaTools项目`water-online-0.7.1`，保留原CORE。下载后确认版本0.7.1，并检查注册/IP/WSS及心跳；尚未实证本版能恢复这次运营商注册异常。

0.7.1兼容网页/后端已部署，备份`/apps/water-auto-exchange/backups/20260912T071450Z-3768177`。容器healthy，公网四个静态文件与本地一致，API和/sms健康200，既有凭据WSS probe通过；未向生产提交设备模拟状态或开水命令。
