# Web 控制台与 4G 接入

## 2026-09-12 当前：手动开关（0.7.1）

按用户最新要求，先提供无需水位传感器的补水、冲水开关。点击开启，设备回报后显示已打开，再点同一开关关闭。关闭使用STOP，因两路互锁，正常状态最多一路开启；另一只开关须等当前关闭后才能打开。独立停止输出按钮保留。

默认water_config.mode为manual，手动FILL/DRAIN不检查水位，也不读取need_fill引脚。未配置输出GPIO时仍显示等待配置；以后只需确认两路输出与启停电平，无需先安装液位板。默认单次开启上限120秒，超时锁存故障；远程活动连接异常也会尝试停止，重新连接不会自动开水。

网页仅对0.7.0或0.7.1/manual启用手动开启，防止向旧自动固件发送意图不同的FILL；旧版STOP仍可用。STATUS新增control_mode，need_fill在manual模式保持unknown。API和固件支持旧automatic模式，但当前网页不显示自动换水入口。

已完成152项Lua、46项Python及本地Edge无头浏览器联调。此阶段按用户要求不做实际水流验证，也不把缺少泵或传感器作为软件交付前提。用户15:03日志已确认实板0.7.0/manual/UNCONFIGURED；0.7.1增强重连包已准备，本轮尚未刷入。

## 历史：补水和冲水独立操作（0.6.0）

控制区提供两个独立按钮：**补水**发送FILL，有补水请求时补到高位；**冲水**发送DRAIN，无补水请求时排到低位并停止，本次不自动补水。“完整换水”折叠入口仍发送START，执行排水→间隔→补水。停止输出随时优先，所有动作仍需设备在线、接线映射确认和稳定液位反馈；未配置时不启用按钮。

DRAIN要求板端0.6.0，旧版显示“更新设备后可用”，API及USB网关也拒绝新命令。该版本保留0.5.3的时基和心跳修正。138项Lua、42项Python及隔离Edge浏览器联调通过；浏览器回执使用模拟设备，不证明现场泵动作。

真实板端10:20:41启动0.5.3，COM4实读确认UNCONFIGURED；SIM为CPIN:READY，持续waiting_pdp，尚未完成新版稳定联网。电脑TLS1.2验证和公网API正常。0.6.0生成包已准备，未刷入；本轮computer-use缺少执行接口，未操作界面。最新详细状态见MEMORY/AGENTS，下方0.5.x为历史记录。

## 历史：0.5.3计时修正与夜间联调

真实板端0.5.2已通过4G/TLS/WSS认证，服务器收到了UNCONFIGURED状态；向真实板下发的一条STOP已回执 `OK STOP stopped`。原“等待设备连接”的直接原因是LuaTools选了src项目，联网关闭、设备密钥为空。改用生成包后可连接，但旧tick换算导致本应1秒的心跳拖到约80秒，先被服务器75秒超时断开。不能把短暂online描述成稳定运行。

0.5.3统一按Air724接口规定每tick为5ms计算心跳、命令有效期、控制器防抖/排补超时、HTTP升级超时及稳定连接退避。同时允许仍有效的较早心跳回执，避免4G回包晚于下一次ping时被误判；每约5秒打印seq、ack和回执年龄。依据：[官方rtos接口](https://doc.openluat.com/wiki/21?wiki_page_id=2247)。V2.4.4旧patch.lua中的 `/16` 不能作为本平台时基，旧记录以本节更正为准。

修正版已通过130项Lua、39项Python测试与LuaFLOAT语法检查。生成目录仍为 `build/firmware`，本机新项目 `water-online-0.5.3` 已写入工具project目录，需“刷新列表”后选择。用户本次已授权助手自行刷写测试，覆盖之前由用户点击的限制；computer-use在检查浏览器网址时停止了本轮界面操作，因此0.5.3尚未下载，仍缺修正版连续心跳及断线恢复的实板验证。详情见[联调记录](verification-20260912.md)。

## 2026-09-12 前次排查记录（0.5.2，以下版本快照属历史）

本机 COM4 已读到真实板端 `water_auto_exchange 0.3.0`、`UNCONFIGURED`；该版本没有 4G/WSS 功能，网页等待设备连接符合当前状态。公网 HTTPS/WSS 握手与现有设备密钥 probe 通过，服务器尚无设备上报，未发送控制命令。

0.5.2 增加本板网络指示灯 `netLed.setup(true, pio.P0_12)`，GPIO12/物理53脚专用于该灯，不能配置为泵或液位输入。网络启用时初始化灯，未启用时保持不配置 GPIO。灯表示移动网络/socket 状态；WSS 认证成功须以 `WATER WS online` 和网页真实上报为准。

本机已准备 LuaTools 项目 `water-online-0.5.2`，指向 `build/firmware/` 中的完整 9 项；关闭并重新打开 LuaTools 后选择它，再由用户点击“下载脚本”。服务器已部署该版本兼容，包内 CA 对当前站点的 TLS1.2/域名校验通过。

新版打印 `waiting_pdp`、`connecting_tls`、`upgrading_http`、`upgraded; authenticating` 及失败阶段；`WATER NET disabled` 表示下载了未启用网络的源码配置，应使用生成包。源码不含设备密钥。当前下载目标 **0.5.2**，清单仍为生成目录中的 8 个 Lua 文件和 1 个 CA，按下述准备步骤操作。控制 GPIO 保持禁用；除明确的网络灯外不配置输出。下方 0.5.1/0.2.8 为此前版本记录，不能替代本次 COM4 实读。

设备通过 Air724UG 自带 4G 网络建立 WSS 长连接，网页入口为 `https://bytegallop.com/water/`，无需电脑常开。网页提供管理密钥登录、在线状态、补水请求、软件输出、故障说明、START / FILL / DRAIN / STOP / RESET 和最近 60 条操作记录。当前为单设备控制台，不配置未经确认的定时换水或自动补水规则。

## 软件与实板边界

当前源码0.7.1、真实板端最近日志0.7.0，详情以上方最新记录为准。默认GPIO配置继续禁用，只有已确认的GPIO12网络灯启用。接线、启停电平、液位反馈和真实水流仍需实测；网络联调不能作为泵控制验证。

## 管理员登录

服务器凭据集中保存在 `/apps/water-auto-exchange/config/water.env`，Docker 迁移保留已有两个独立密钥：`WATER_ADMIN_KEY` 用于网页登录，`WATER_DEVICE_KEY` 用于设备认证。该文件权限为 600，禁止提交 Git。管理会话有效期 8 小时，Cookie 使用 Secure / HttpOnly / SameSite=Strict；变更请求校验 Origin。登录入口每分钟最多 10 次尝试。

本轮提供的本地登录说明位于被 Git 忽略的 `build/console-access.private.txt`，设备配置位于 `build/device.private.json`。这些文件含凭据，请勿分享或提交；聊天记录和日志不打印密钥。

## 准备 4G 固件

1. 准备可联网的 SIM、天线和供电；板端输出仍保持禁用，先验证网络及状态上报。GPIO23 复用 SIM 在位检测的硬件约束继续有效，不能据此猜测泵映射。
2. 本轮已提供 `build/device.private.json`。后续使用自己的 JSON 文件，格式为 `{"url":"https://bytegallop.com/water","device_key":"填入设备密钥"}`。
3. 在仓库运行 `python tools/build-firmware.py --config build/device.private.json`。脚本复制源码到 `build/firmware/`，仅在该目录启用联网并填入设备密钥，源文件与 GPIO 配置保持原状。
4. 本机LuaTools“刷新列表”后选 `water-online-0.7.1`。其他机器按 `build/firmware/flash-files.txt` 创建项目并加入 **8个Lua文件和1个CA证书**，全部来自同一个生成目录。保留现有CORE、默认LuaTask V2.4.4库及USB trace。每条require独占一行；JSON为CORE内置全局模块。
5. 按当前用户授权点击“下载脚本”。下载后核对 `project=water_auto_exchange version=0.7.1`、`UNCONFIGURED` 和生成配置，并查看服务器是否收到真实上报。持续观察至少跨过原75秒断线窗口，确认5秒日志中的心跳seq/ack继续增长、服务器last_seen持续刷新，再验证重连。仅版本号或一次online不足以通过联调。
6. 手动模式只需确认输出启停、输出接线和超时参数后修改 `src/water_config.lua`；不要求水位传感器。自动模式另需液位板隔离反馈接线，重新生成整包、验证、提交并推送，再由用户刷写。不能直接在生成目录内做长期配置修改，重新生成会覆盖该目录文件。

下载清单：`main.lua`、`water_config.lua`、`water_cycle.lua`、`water_control.lua`、`water_usb.lua`、`water_network.lua`、`water_network_config.lua`、`water_ws_transport.lua`、`water-ca.crt`。

TLS强制提供CA文件并开启SNI。CA来源与指纹见[证书说明](../certs/README.md)。0.5.2实板TLS与WSS认证已通过，0.5.3持续连接仍待验证。禁止通过删除CA配置绕过校验。网络接口按本机V2.4.4 `sys.lua` / `socket4G.lua` 核对：connect/send超时单位为秒，recv为毫秒；Air724的 `rtos.tick()` 每计数为5ms。

## WSS 通信与操作语义

设备连接 `wss://bytegallop.com/water/api/device/ws`。服务端主动推送命令，设备空闲每 1 秒发送小型应用心跳；状态变化立即上报。换水期间每 1 秒发送小心跳，10 秒收不到服务器有效回复会尝试停止远程启动的周期。网页自身的 HTTP 刷新发生在浏览器与服务器之间，不触发设备轮询。

- 状态不变时不重复上传完整状态，不自动回退到 HTTP 轮询。断线立即标记离线，半开连接空闲最多 75 秒判离线；活动状态最多 10 秒。
- 握手后先认证，设备密钥只在 TLS 加密消息中传送，不放 URL；认证前不读取设备状态、不交付命令。诊断 `probe` 只检查鉴权，不注册设备或更改线上记录。
- 命令有效期仍为 8 秒。服务器推送 offer，设备发送 claim 并记录本地单调时钟；服务器检查剩余有效期后返回 execute。设备再检查 claim 往返耗时与剩余有效期，避免延迟缓存的旧命令启动输出。命令交付、执行与设备回执分别记录，回执丢失显示“结果待核实”，不自动重发 START/FILL/DRAIN。
- START/FILL/DRAIN/RESET 仍由原控制器检查液位、防抖、互锁、超时和故障。STOP 优先，取消仍排队的命令；设备收到 STOP offer 会清除尚未执行的其他 claim。
- USB STOP 会作废在途远程响应并关闭当前连接；新连接创建新会话，旧命令不能恢复泵。远程 STOP 保留连接以便立即回传结果。
- `water_ws_transport.lua` 的单一任务持有 socket；connect/send/recv/close 直接在该任务中执行，不放进外层 pcall。业务调用 send 只入队，不直接等待底层 SOCKET_SEND。
- 收发支持 TCP 分包、粘包、126 字节边界、WebSocket 分片及 Ping/Pong。每条消息上限 8192 字节，发送队列上限 8 条；异常输入、队列失败及超时会断线并尝试关闭远程输出。应用/传输层不打印认证消息，认证固定前缀避免底层 socket 调试预览包含密钥。
- 0.7.1新增：已注册时连续6次连接失败，或等待IP超过120秒，调用当前LuaTask库的link.shut恢复链路就绪状态；每次至少间隔300秒。未注册时等待底层搜网，不循环切飞行模式。故障期间每60秒最多一批只读AT诊断，IP_ERROR立即作废远程会话并尝试停止其输出。详见[网络恢复与现场注册诊断](network-recovery.md)。
- 失败重连按 1、2、4 秒逐步延长，最多 60 秒；连接稳定达到 60 秒后重置退避。连接失败和身份拒绝不会每秒重新握手。不可恢复的任务异常或时钟异常停止网络控制，需排查后重启。
- 初期 TLS 握手依然有流量开销。长连接避免每 2 秒重复 TCP/TLS/HTTP 请求；实际运营商流量仍需实板测量，不据模拟测试填写节省百分比。
- TLS 必须携带 CA、SNI，并设置 `insist=0` 拒绝证书域名校验失败。板端实际 TLS 握手与长时间在线仍待用户刷写验证。
- 网络 STOP 写入失败会打印 `stop_unconfirmed`，硬件适配器继续显示输出不确定或故障；软件无法替代独立硬件断流。

## USB 调试网关（可选）

独立 4G 是当前主接入方式。USB 工具用于本地调试，支持 0.3.0/0.5.1 协议；不要与板端 4G 同时连接同一控制台，也不要与 LuaTools/其他串口工具争用用户口。

```powershell
python -m pip install -r tools/requirements-gateway.txt
python tools/water-gateway.py --port COM4 --config build/device.private.json
```

先重新枚举并确认用户串口。网关启动、断网或退出时尝试 STOP；仅在识别出正确项目和支持版本后发送控制命令。当前 0.5.1 纯 USB 主机命令使用 `tools/water-command.ps1`。

## 验证

```powershell
python -m pip install -r server/requirements.txt websocket-client
python -m unittest discover -s tests -p "test_web*.py"
# 以下测试工具可安装到 .venv，避免改变全局环境。
python -m pip install lupa playwright
python tools/test-lua.py
python tests/web_browser_test.py
```

浏览器测试使用本机 Edge 和隔离的模拟设备状态，不连接真实串口。检查登录、鉴权、状态刷新、命令回执、停止、移动端和转义。模拟测试不构成实机换水验证。
