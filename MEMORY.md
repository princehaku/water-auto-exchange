# 项目记忆

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
