# Web 控制台与 4G 接入

设备通过 Air724UG 自带 4G 网络建立 WSS 长连接，网页入口为 `https://bytegallop.com/water/`，无需电脑常开。网页提供管理密钥登录、在线状态、补水请求、软件输出、故障说明、START / FILL / STOP / RESET 和最近 60 条操作记录。当前为单设备控制台，不配置未经确认的定时换水或自动补水规则。

## 软件与实板边界

本轮板端源码为 **0.5.1**，板上最近历史实读仍为已 STOP 的 0.2.8。默认 GPIO 配置继续禁用。本轮未刷写、未启动泵，电脑未枚举到串口；网页可访问不能证明设备在线。接线、启停电平、液位反馈和真实水流仍需实测。

## 管理员登录

服务器凭据集中保存在 `/apps/water-auto-exchange/config/water.env`，Docker 迁移保留已有两个独立密钥：`WATER_ADMIN_KEY` 用于网页登录，`WATER_DEVICE_KEY` 用于设备认证。该文件权限为 600，禁止提交 Git。管理会话有效期 8 小时，Cookie 使用 Secure / HttpOnly / SameSite=Strict；变更请求校验 Origin。登录入口每分钟最多 10 次尝试。

本轮提供的本地登录说明位于被 Git 忽略的 `build/console-access.private.txt`，设备配置位于 `build/device.private.json`。这些文件含凭据，请勿分享或提交；聊天记录和日志不打印密钥。

## 准备 4G 固件

1. 准备可联网的 SIM、天线和供电；板端输出仍保持禁用，先验证网络及状态上报。GPIO23 复用 SIM 在位检测的硬件约束继续有效，不能据此猜测泵映射。
2. 本轮已提供 `build/device.private.json`。后续使用自己的 JSON 文件，格式为 `{"url":"https://bytegallop.com/water","device_key":"填入设备密钥"}`。
3. 在仓库运行 `python tools/build-firmware.py --config build/device.private.json`。脚本复制源码到 `build/firmware/`，仅在该目录启用联网并填入设备密钥，源文件与 GPIO 配置保持原状。
4. 在 LuaTools 的 water-exchange 项目中，删除旧文件条目，再按 `build/firmware/flash-files.txt` 加入 **8 个 Lua 文件和 1 个 CA 证书**。全部路径均来自同一个生成目录。保留现有 CORE、默认 LuaTask V2.4.4 库及 USB trace。每条 require 独占一行；JSON 为 CORE 内置全局模块。
5. 用户亲自点“下载脚本”。下载后核对 `project=water_auto_exchange version=0.5.1`、`UNCONFIGURED`、零输出配置，并查看登录后的网页是否出现真实上报。网络启用后自动建立 WSS 长连接，空闲每 1 秒应用心跳，状态变化立即上报；TLS/PDP 初始化时间取决于网络。
6. 只有确认输出启停、液位板隔离反馈接线与超时参数后，才修改 `src/water_config.lua`，重新生成整包、验证、提交并推送，再由用户刷写。不能直接在生成目录内做长期配置修改，重新生成会覆盖该目录文件。

下载清单：`main.lua`、`water_config.lua`、`water_cycle.lua`、`water_control.lua`、`water_usb.lua`、`water_network.lua`、`water_network_config.lua`、`water_ws_transport.lua`、`water-ca.crt`。

TLS 强制提供 CA 文件并开启 SNI。CA 来源与指纹见 [证书说明](../certs/README.md)。当前证书链已在服务器侧验证；Air724UG 实板 TLS 握手仍待验证。禁止通过删除 CA 配置来绕过证书校验。网络接口按本机 V2.4.4 `sys.lua` / `socket4G.lua` 核对：connect/send 超时单位为秒，recv 为毫秒。

## WSS 通信与操作语义

设备连接 `wss://bytegallop.com/water/api/device/ws`。服务端主动推送命令，设备空闲每 1 秒发送小型应用心跳；状态变化立即上报。换水期间每 1 秒发送小心跳，10 秒收不到服务器有效回复会尝试停止远程启动的周期。网页自身的 HTTP 刷新发生在浏览器与服务器之间，不触发设备轮询。

- 状态不变时不重复上传完整状态，不自动回退到 HTTP 轮询。断线立即标记离线，半开连接空闲最多 75 秒判离线；活动状态最多 10 秒。
- 握手后先认证，设备密钥只在 TLS 加密消息中传送，不放 URL；认证前不读取设备状态、不交付命令。诊断 `probe` 只检查鉴权，不注册设备或更改线上记录。
- 命令有效期仍为 8 秒。服务器推送 offer，设备发送 claim 并记录本地单调时钟；服务器检查剩余有效期后返回 execute。设备再检查 claim 往返耗时与剩余有效期，避免延迟缓存的旧命令启动输出。命令交付、执行与设备回执分别记录，回执丢失显示“结果待核实”，不自动重发 START/FILL。
- START/FILL/RESET 仍由原控制器检查液位、防抖、互锁、超时和故障。STOP 优先，取消仍排队的命令；设备收到 STOP offer 会清除尚未执行的其他 claim。
- USB STOP 会作废在途远程响应并关闭当前连接；新连接创建新会话，旧命令不能恢复泵。远程 STOP 保留连接以便立即回传结果。
- `water_ws_transport.lua` 的单一任务持有 socket；connect/send/recv/close 直接在该任务中执行，不放进外层 pcall。业务调用 send 只入队，不直接等待底层 SOCKET_SEND。
- 收发支持 TCP 分包、粘包、126 字节边界、WebSocket 分片及 Ping/Pong。每条消息上限 8192 字节，发送队列上限 8 条；异常输入、队列失败及超时会断线并尝试关闭远程输出。应用/传输层不打印认证消息，认证固定前缀避免底层 socket 调试预览包含密钥。
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
