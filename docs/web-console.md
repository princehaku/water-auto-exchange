# Web 控制台与 4G 接入

设备通过 Air724UG 自带 4G 网络直接访问 `https://bytegallop.com/water/`，无需电脑常开。网页提供管理密钥登录、在线状态、补水请求、软件输出、故障说明、START / FILL / STOP / RESET 和最近 60 条操作记录。当前为单设备控制台，不配置未经确认的定时换水或自动补水规则。

## 软件与实板边界

本轮板端源码为 **0.4.0**，板上最近历史实读仍为已 STOP 的 0.2.8。默认 GPIO 配置继续禁用。本轮未刷写、未启动泵，电脑未枚举到串口；网页可访问不能证明设备在线。接线、启停电平、液位反馈和真实水流仍需实测。

## 管理员登录

部署时在服务器 `/etc/water-console.env` 自动生成两个独立随机密钥，已有密钥会保留：`WATER_ADMIN_KEY` 用于网页登录，`WATER_DEVICE_KEY` 用于设备认证。该文件权限为 600，禁止提交 Git。管理会话有效期 8 小时，Cookie 使用 Secure / HttpOnly / SameSite=Strict；变更请求校验 Origin。登录入口每分钟最多 10 次尝试。

本轮提供的本地登录说明位于被 Git 忽略的 `build/console-access.private.txt`，设备配置位于 `build/device.private.json`。这些文件含凭据，请勿分享或提交；聊天记录和日志不打印密钥。

## 准备 4G 固件

1. 准备可联网的 SIM、天线和供电；板端输出仍保持禁用，先验证网络及状态上报。GPIO23 复用 SIM 在位检测的硬件约束继续有效，不能据此猜测泵映射。
2. 本轮已提供 `build/device.private.json`。后续使用自己的 JSON 文件，格式为 `{"url":"https://bytegallop.com/water","device_key":"填入设备密钥"}`。
3. 在仓库运行 `python tools/build-firmware.py --config build/device.private.json`。脚本复制源码到 `build/firmware/`，仅在该目录启用联网并填入设备密钥，源文件与 GPIO 配置保持原状。
4. 在 LuaTools 的 water-exchange 项目中，删除旧文件条目，再按 `build/firmware/flash-files.txt` 加入 **7 个 Lua 文件和 1 个 CA 证书**。全部路径均来自同一个生成目录。保留现有 CORE、默认 LuaTask V2.4.4 库及 USB trace。每条 require 独占一行；JSON 为 CORE 内置全局模块。
5. 用户亲自点“下载脚本”。下载后核对 `project=water_auto_exchange version=0.4.0`、`UNCONFIGURED`、零输出配置，并查看登录后的网页是否出现真实上报。网络启用后自动建立会话，约每 2 秒轮询一次；TLS/PDP 初始化时间取决于网络。
6. 只有确认输出启停、液位板隔离反馈接线与超时参数后，才修改 `src/water_config.lua`，重新生成整包、验证、提交并推送，再由用户刷写。不能直接在生成目录内做长期配置修改，重新生成会覆盖该目录文件。

下载清单：`main.lua`、`water_config.lua`、`water_cycle.lua`、`water_control.lua`、`water_usb.lua`、`water_network.lua`、`water_network_config.lua`、`water-ca.crt`。

TLS 强制提供 CA 文件并开启 SNI。CA 来源与指纹见 [证书说明](../certs/README.md)。当前证书链已在服务器侧验证；Air724UG 实板 TLS 握手仍待验证。禁止通过删除 CA 配置来绕过证书校验。网络依赖按 [合宙 HTTP API](https://docs.openluat.com/air724ug/luatos/app/socket/http/) 和本机 V2.4.4 `http.lua` / `socket4G.lua` 核对。

## 操作和故障语义

- 上报超过 10 秒未更新，网页显示离线，服务器拒绝所有新控制命令；不缓存离线启动请求。
- START 需要待机或 DONE、输入稳定、无补水请求且输出记录确定；FILL 需要补水请求有效。板端在执行时再次检查所有条件。
- 命令有效期 8 秒，交付一次。网页提交仅代表入队；设备回执决定最终结果。回执丢失会显示“结果待核实”，禁止自动重发 START/FILL。
- STOP 优先领取并取消仍在排队的命令。已交付动作仍须等待实际 STOP 回执；不能把点击按钮当作已经断电。
- 4G 请求的本地总截止为 5 秒；请求失败或超过截止会尝试停止由远程命令启动的周期。另有 10 秒通信看门狗。USB 独立启动的本地周期保留其原有超时保护。
- 重连建立新会话，服务端取消旧会话未完成命令；旧会话最近仍有通信时最多等待 15 秒再允许替换。断电/服务重启不恢复泵运行。
- USB STOP 会作废在途远程响应并切换会话；过期 HTTP 回调和重复命令 ID 不启动输出。
- HTTP 库每个子过程各有超时，因此程序另外设置总截止；一次只保留一个请求。若库异常永久不回调，网络控制保持停机，需排查日志后重启，不持续创建新协程。
- RESET 仍由板端检查故障与稳定输入；清除成功后保持待机。
- 网络 STOP 写入失败会打印 `stop_unconfirmed`，硬件适配器保留输出不确定/故障信息。软件无法替代独立硬件断流。

## USB 调试网关（可选）

独立 4G 是当前主接入方式。USB 工具用于本地调试，支持 0.3.0/0.4.0 协议；不要与板端 4G 同时连接同一控制台，也不要与 LuaTools/其他串口工具争用用户口。

```powershell
python -m pip install -r tools/requirements-gateway.txt
python tools/water-gateway.py --port COM4 --config build/device.private.json
```

先重新枚举并确认用户串口。网关启动、断网或退出时尝试 STOP；仅在识别出正确项目和支持版本后发送控制命令。当前 0.4.0 纯 USB 主机命令使用 `tools/water-command.ps1`。

## 验证

```powershell
python -m unittest discover -s tests -p test_web.py
# 以下测试工具可安装到 .venv，避免改变全局环境。
python -m pip install lupa playwright
python tools/test-lua.py
python tests/web_browser_test.py
```

浏览器测试使用本机 Edge 和隔离的模拟设备状态，不连接真实串口。检查登录、鉴权、状态刷新、命令回执、停止、移动端和转义。模拟测试不构成实机换水验证。
