# 项目约定与本轮对话摘要

## 2026-09-12 下载项目纠正与最新板端状态

- 用户反馈下载后仍“等待设备连接”。真实 COM4 STATUS 已为 `water_auto_exchange 0.5.2 state=UNCONFIGURED`，替代下节0.3.0快照；GPIO映射仍禁用，未发送泵控制命令。
- LuaTools日志显示00:49:53下载成功，但现场界面当前项目仍为 `water-exchange`、文件来自 `src`。最近生成的 `_temp/script/temp_script/water_network_config.lua` 明确为 `enabled=false`、空设备密钥，该目录无CA。已证实该次更新了版本却未使用启用联网的生成包。LuaTools会剥离注释，其他文件字节差异本身不能作为混包证据。
- 用 computer-use 刷新项目列表并选中现有 `water-online-0.5.2`，现场核对 `build/firmware` 的8 Lua+1 CA共9项。独立检查启用标志、凭据与私有配置一致、CA与仓库公开证书一致、项目清单/文件存在/版本通过；无需重启工具才能刷新项目。
- 已通知用户现在可点“下载脚本”；助手未点击刷写。下载后还需核对最新打包配置和板端WATER NET/WS、服务端在线。此前trace在00:50:04才重新接入，仅见5秒WATER STATUS，没捕获开机消息；不能把没有早期日志当作已定位SIM或TLS故障。
- 本次只调整本地已忽略的工具项目选择并更新记录，应用代码和生成包未改，不重复运行上一轮124+39项测试或部署。尚无真实4G/WSS成功证据。

## 2026-09-12 实板接入排查：0.5.2

- 已 fast-forward 拉取远程 `7482969`。本次通过真实 COM4 的 STATUS 确认板上为 `water_auto_exchange 0.3.0 state=UNCONFIGURED`，替代此前“最近板上0.2.8”快照；0.3.0 没有 4G/WSS，网页等待设备连接不是电脑未识别 USB。运行态 COM3/4/5/6 均正常。
- 用户明确网络灯为 `netLed.setup(true, pio.P0_12)`，对应 GPIO12/物理53/SPI1_DIN。0.5.2 在联网启用时初始化该灯，适配器从允许集合排除 GPIO12，避免泵/传感器冲突。库默认闪烁表示注册/socket 状态，不等于服务器应用认证成功；不得外推其他 GPIO 映射。
- 新增无密钥的联网阶段日志，区分 waiting_pdp、connecting_tls、upgrading_http、认证与重试；关闭网络的源码明确打印 disabled。主机、服务端和网关支持 0.5.2，控制 GPIO 配置仍禁用。源码零控制输出与网络灯初始化分别记录。
- 已读服务器健康，公网 HTTPS/WSS 及既有设备密钥 probe 通过；登录后状态为离线、device=null、命令数0。未向生产服务填入模拟设备状态、未发泵命令，probe 不证明板端4G已通。
- 从既有服务器配置提取设备凭据到忽略的 `build/device.private.json`，不展示值。使用 tools/build-firmware.py 生成同目录 8 Lua + CA 共9项，不能继续用旧 src 项目或旧5文件清单。用户仍亲自下载；本次联调尚须下载后实读版本和联网状态。
- 本地已通过 124 项 Lua 5.1、39 项 Python API/WS/网关测试，以及生成包官方 LuaFLOAT 语法检查。旧 tools/vendor/python 的二进制不适配当前 Python3.10，测试依赖放忽略目录 build/debug-python（不要改全局解释器或提交依赖包）。
- 服务器已部署0.5.2兼容，备份 `/apps/water-auto-exchange/backups/20260911T163813Z-3487003`，容器healthy，公网API及/sms健康正常；生成包CA配合TLS1.2/域名校验成功。新LuaTools项目为 `water-online-0.5.2`，配置文件在工具project目录，指向生成包9项；用户关闭重开LuaTools后选该项目亲自下载，再核对真实版本/在线。此记录不表示下载已发生。

## 持续要求：每次修改后提交并推送

- 每次完成本仓库的代码、配置或文档修改并通过相应验证后，必须主动执行 `git commit` 和 `git push`，无需用户再次提醒。
- 提交信息使用中文标准格式，例如 `feat: 新增功能`、`fix: 修复问题`、`docs: 更新约定`。
- 提交前检查 Git 状态与暂存清单，确保本次新增文件和依赖全部纳入提交，保留用户其他改动，排除密钥、日志、工具包和生成产物。
- 推送后核对远程分支与本地提交一致，并在结果中说明提交号和推送结果；推送失败时明确报告原因，不能宣称已同步。

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


适用范围：整个 `water-auto-exchange` 仓库。整理时间：2026-09-06，覆盖 2026-09-05 至 09-06 的首次连接、硬件辨识、GPIO 诊断、测量反馈、0.3.0 自动换水实现与 Git 同步。后续用户的新指示及新的实测结果优先；更新结论时保留证据和未确认部分。

接续工作先读 [MEMORY.md](MEMORY.md) 的简明现状，再查本文件的约定与证据。0.3.0 源码和板上最近实读的 0.2.8 必须分开记录；下文标记为历史的扫描流程不能作为现行入口的操作指令。

## 当前任务与源码：0.3.0 自动换水

- 用户接受三探针液位板方案并明确“好，可行的，写代码吧”。现行入口已切换为 `PROJECT="water_auto_exchange"`、`VERSION="0.3.0"`，不再开机扫描 GPIO，也不加载旧电机循环。以下 0.2.x 相关“当前范围”“开机自动扫描”等均为历史条件，不能覆盖本节。
- 已实现单路迟滞继电器反馈控制：命令 START 排水，补水请求有效后关排水，等待 1 秒再补水，请求解除后全部关闭并 DONE；FILL 只补水到高位，用于首次加水。STOP 关输出，故障保持锁存；RESET 在输入恢复稳定后仅回待机。输入每 100ms 采样、防抖 500ms，排补各 120 秒初始超时。超高输入可选，默认禁用；不是独立硬件断流。
- 当前先手动命令启动一轮，无定时计划、无空闲时自动补水、无断点恢复。已向用户询问补水/排水设备规格与启动方式，尚无答复，不据此填写泵映射或定时规则。
- `src/water_config.lua` 默认 `enabled=false`、`mapping_confirmed=false`、`wiring_confirmed=false`，输出/输入 GPIO 和有效电平未填。该配置下 `UNCONFIGURED`、零 GPIO 配置。GPIO5/23 的现有观察不能填成确定的泵启停电平；液位板反馈 GPIO 也未确定。
- 现行适配器只允许已知固定 1.8V 域集合 `5,9,10,11,12,13,14,15,17,18,19,22,23` 中的不同脚，仍需核对板上已有外围与复用。关断保持实测 `off_level`，不释放脚作为关断。无自动 AT/SIM/LDO 或物理 UART 配置。
- 开机先建立 USB STOP 通道，成功后初始化已确认配置的输出为 OFF；立即打印状态并每 5 秒打印一次，不依赖 SIM/网络。USB 命令 `STATUS/START/FILL/STOP/RESET`，主机工具 `tools/water-command.ps1` 先核对项目和版本。`fill/drain` 是软件输出记录，`outputs_known=0` 表示未初始化或写入不确定，不代表实测电压。
- 纯状态机 `water_cycle` 用冒号方法；硬件适配器 `water_control.new(config,deps)` 返回无 self 的点调用方法。使用 V2.4.4 `rtos.tick()/16` 毫秒时基并处理 32 位回绕。读取/时钟异常清除稳定样本，恢复后须重新完整防抖才能 RESET。关断错误须附加显示，不能被原故障原因覆盖。
- LuaTools `tools/vendor/luatools/project/water-exchange.ini` 已换成 `main.lua/water_config.lua/water_cycle.lua/water_control.lua/water_usb.lua` 五文件，保留 CORE/default_lib。工具 vendor 目录被 Git 忽略，其他机器须按 README 重建项目清单。旧 motor/probe 源码及测试保留供历史查阅。
- **板上状态与本地版本区分：尚未下载 0.3.0；最近实读板上是已 STOP 的 0.2.8。** 旧版重启仍会扫描，不能据新源码宣称板上已待机。用户亲自点击 LuaTools“下载脚本”，不要替用户刷写；若界面文件清单未更新需先重新载入项目。
- 使用说明见 `docs/water-control.md`；原 README 完整保留在 `docs/diagnostic-history.md`。用户最新明确要求“push到远程，记得更新memory和agent再推送”，本次已授权整理记录、提交并推送；用户最新持续要求每次修改验证后都要 commit 和 push，使用中文标准提交信息。
- 0.3.0 本地检查已完成：Lua 5.1 测试 82/82（状态机17、硬件适配器24、USB/启动14、旧电机/USB15、旧诊断12），官方 LuaFLOAT 语法、PowerShell 解析、LuaTools 五文件/依赖/CORE/版本及 UTF-8 文档核对通过。未操作真实串口/输出，未刷写。模拟测试不能当作硬件联调结果。

## 实际应用目标：水位监测与自动换水

- 用户明确项目用于水位监测、自动换水，并确认是鱼缸/水箱类自动排旧水、补新水；最初说没有水位传感器，随后补充已有下述12V三探针液位控制板。具体容器、新水来源、泵/阀规格及实际探针尚未确定。
- 新图 `codex-clipboard-14732991-f585-4dc5-9960-1c50d6b1feea.png` 是用户所持液位板的卖家接线图。图示A为最低公共探针，B低位启动补水、C高位停止补水；配探针可承担正常上下限检测。只展示一组继电器转换触点，不能当作两个独立水位信号，更不能当连续水深测量。排水期间须抑制自动补水，否则水位低于B可能边排边补。正常C满水截止不等于独立超高保护；后者仍是建议，未采购/接线。
- 图上12V指控制板供电版本，图示泵侧另接市电；若要把触点作为Air724输入，必须先确认触点与电源隔离且不带泵侧电压，不能将A/B/C探针或12V输出直接接1.8V GPIO。卖家标注10A/2000W不是本项目已验证的泵负载额定值。该类探针依赖液体导电性，实际水质和电极材料需核对，不能把普通裸铜线示意直接作为长期鱼缸电极方案。
- 初期出纸电机接口的3秒通/3秒断是板级输出测试目标，不可直接作为实际自动换水控制逻辑。现阶段DO2/GPIO23仍仅为强候选，启停电平和负载能力未确认。
- 向用户建议两个正常控制检测点：排水下限、补水上限；无人值守再配独立超高水位保护，触发停止进水并报警。此为设计建议，尚未确认采购、接线或启用业务。连续水位传感器也可提供上下限，不能把“三个检测点”表述为一定需要三件独立主测量传感器。
- 若只需判断到位，可考虑干接点浮球开关；若需厘米/百分比，需另选连续水位测量方案。传感器接输入电路，不能直接接DO2输出；未确认接口电平前，不向Air724UG的1.8V GPIO接入12V传感器信号。
- 后续控制设计应包含排水/补水超时、异常停机及进排水互锁；独立超高保护若设计为硬件断流，须另核对驱动和额定参数，不因增加一个软件输入就声称已有独立硬件保护。

## 最新 DO2 测量与停止状态

2026-09-06 11:07:32.916/11:07:37.927，用户贴出 GPIO23（模块物理8脚）的 HIGH/LOW 两阶段日志，并澄清 DO2 两针之间两个阶段都约 12V。记录为本次未观察到 GPIO23 使 DO2 通断；不能认定 GPIO23 控制 DO2，也不能证明它完全无关。GPIO13/22 的测量结果未提供。12V 是 DO2 板端电压，不是 GPIO23 的 1.8V 逻辑电压。

2026-09-06 11:09:33 左右 COM4 实读 `version=0.2.8 probe_state=RUNNING probe_gpio=23 probe_level=1 probe_continuous=1 probe_cycle=5 probe_total=3 probe_cycle_ms=30000 probe_candidates=13,22,23`，随后收到 `OK STOP stopped`。已确认 0.2.8 上板，本次扫描已停止；用户随后确认：不重启、STOP 成功后，DO2 两针之间已接近 0V。结合 STOP 前实际正在 GPIO23 HIGH 阶段，GPIO23 记为 DO2 控制强候选；但先前 HIGH/LOW 均约12V，STOP 同时包含写 LOW 和释放 GPIO，尚不能区分低电平关断与释放后关断，不能填写确定的高/低有效电平。 STOP 写 LOW 后释放 GPIO；近0V来自用户实测，不是仅由STOP应答推断。重启仍会自动扫描。

## 持续用户约定与历史 0.2.8 诊断范围

- 使用中文，直接完成已明确的工作，给出简短进度和结果；不要反复询问已有答案的问题。
- **0.2.8 阶段的目标是核实 DO2 固定 12V 的表现；该三脚扫描已 STOP，用户实测 DO2 降至近0V；GPIO23 为强候选，有效电平仍未确认。** 当前已转入顶部所述 0.3.0 自动换水，未要求恢复扫描。用户已选择跳过 RD2D 近照辨认。此前 20 候选已按 09:58:35.574 至 10:01:50.686 的日志及“这些可排除”反馈暂停复扫；具体电压未明确，不得补写为已测 0 V。
- 这次暂排除的 GPIO 为 `0,1,2,3,4,9,10,11,14,15,17,18,19,20,21,24,25,26,27,28`。日志顺序匹配 0.2.6，但未附 VERSION 字段；0.2.7 只是调整同一集合的顺序，不要要求用户为“新增候选”再刷再扫。该结果不等于已经证明这些脚与 DO2 无电路关系，仍可能涉及供电、接口类型或共用使能。
- 用户曾提出“低、中、高”，随后明确改为“高低”；不实现高阻中档、PWM 中档或中间模拟电压。
- GPIO5 的出纸端约 6.1 V 和 GPIO12 的板载灯关系只保留记录；0.2.8 扫描排除了这两脚及先前 20 候选，仅测试 GPIO13/22/23。此为历史测试范围，不限定 0.3.0 的配置校验集合，也不授权默认让 GPIO5 保持高电平。
- **刷写由用户亲自操作。** 所有源码和检查完成后，明确告诉用户“现在可以点下载脚本”；不要自行点击刷写按钮。使用 LuaTools 的“下载脚本”，保留现有底层 CORE。
- 用户不希望等待主机命令才开始打印；0.3.0 开机立即打印状态，每 5 秒更新，保持待机。旧 0.2.x 诊断按当时要求开机自动运行、每次高低切换同步打印，STOP 后重启会重新扫描；该旧启动行为不适用于 0.3.0。
- 最初板级输出测试目标是出纸电机“通电 3 秒、断电 3 秒”，一直未启用；历史 5 秒高低循环是定位与测量工具。两者都不是现行自动换水的业务逻辑。

## 历史 0.2.8 源码与板上验证记录

本节保留 0.2.x 实施和验证过程。现行入口与测试总数以顶部 0.3.0 节为准；只有最近一次实读的板上固件仍为 0.2.8。

- 当时本地版本：`PROJECT="gk21_motor_test"`、`VERSION="0.2.8"`；代码和检查已完成，已核对上板并 STOP。当时检查全部通过：电机/USB/启动 18/18、GPIO 诊断 12/12、官方 LuaFLOAT 当时全部 5 个源码语法、PowerShell 解析、实际模块与主机元数据及 LuaTools 项目文件列表核对通过。
- 保留的 `src/gpio_probe.lua` 仅 GPIO13→22→23，对应模块物理脚 43→7→8；全部固定 V_GLOBAL_1V8，每脚 HIGH/LOW 各 5000 ms，每轮 30000 ms。0.2.8 入口曾开机自动无限循环并逐阶段打印；0.3.0 入口不加载它。该模块已移除旧 LDO/物理 UART 代码，不执行额外 AT/SIM 配置。
- 历史 0.2.7 板上记录：2026-09-06 10:08 左右 COM4 实读 `probe_state=RUNNING probe_gpio=1 probe_level=0 probe_continuous=1 probe_cycle=2 probe_total=20 probe_cycle_ms=200000 probe_candidates=9,10,11,1,4,17,15,14,19,18,20,21,27,28,24,25,26,0,2,3`，随后 `OK STOP stopped`；临时打包脚本版本和顺序也一致。
- 每脚初始化先设 LOW，再立即进入 HIGH 5000 ms、LOW 5000 ms；连续模式反复执行。停止时取消计时、尝试写 LOW 并释放 GPIO，旧回调不得继续输出。
- `src/motor_config.lua` 仍为 `enabled=false`、`mapping_confirmed=false`、`auto_start=false`，输入及有效电平未填写；业务周期仍是 3000/3000 ms。
- 保留的 `motor_cycle.lua` 是历史双输入驱动模板。若以后恢复出纸测试且确认是单输入控制，应改为单输入实现，不能为凑参数随意占用第二个 GPIO。
- 在旧 0.2.8 中，`motor_config.auto_start=false` 不代表诊断不开机运行：旧 `main.lua` 在 USB 初始化成功且未选电机自动启动时，自动执行同一命令处理路径的 `PROBE LOOP`。现行 `main.lua` 已替换这一逻辑。
- **0.2.5 已确认上板并停止。** 2026-09-06 09:40:47 再次枚举到 COM3/4/5/6，随后 COM4 读取 `version=0.2.5 probe_state=RUNNING probe_gpio=5 probe_level=0 probe_continuous=1 probe_cycle=73 probe_total=1 probe_cycle_ms=10000 probe_candidates=5`，随后 STOP 成功。此为旧连接记录；后续 0.2.7 已由上述实读确认并停止。
- 先前仅枚举到 COM1、COM4 不存在是旧快照，已被上述重新连接实证更新；后续仍应重新枚举端口，不能把 COM1 当作 Air724UG。
- 0.2.7、0.2.8 均已核对并停止，无需为旧记录重复刷入。0.2.8 的已核对字段为 `version=0.2.8 probe_total=3 probe_cycle_ms=30000 probe_candidates=13,22,23`，并有实际阶段日志；仅版本号不足以证明包内脚本一致。
- `STATUS` 中电机 `state=STANDBY`、`on_ms=3000` 与诊断 `probe_state=RUNNING`、5000 ms 阶段可以同时成立，不是定时错误。

## 板卡和功能测量记录

- 定制板：工科物联 `GK21-SPTM_rev0.3`；模组 `Air724UG-NFM`。不是官方开发板，模组手册不能代替定制 PCB 原理图。
- 可见接口：USB、SIM、KEY1、出纸电机、仓门电机、人体红外、NFC、LED、键盘、纸巾仓机芯信号、RX/TX/GND、电源输入/输出及顶部 DO2 两针口；丝印包含 +12V、+3.9V。当前按用户所说顶部 DO 口对应照片 DO2 理解，其控制 GPIO 未知。
- 用户报告“上面写了个 RD2D”，暂按 DO2 附近器件丝印理解，具体器件及脚数未确认。若为 SC-70-6/SOT-363 六脚，Si1551DL 是候选：Vishay doc 71255 Rev.D 标记 RD，另有批次/日期码，不能唯一识别 RD2D。它是 N+P 双 MOS，芯片 2/5 为栅极，不是 GPIO2/5；不能推定本板 GPIO 或两路控制。用户已选择跳过近照辨认继续 GPIO 测试，不再要求近照；保留候选和来源。
- 原先无法识别 USB，后来更换连接条件并安装匹配驱动后已成功读取、刷写和通信；不要从“换过线”推断具体故障原因已经确定。

| GPIO | 模块物理脚 | 本轮用户观察 | 尚未确认 |
| --- | --- | --- | --- |
| 5 | 49 | 2026-09-06 00:21:51.634 日志为 `level=1 phase=HIGH index=1/13`；用户报告出纸端约 **6.1 V** | LOW 时是否降至近 0 V；完整启停电平、驱动拓扑及是否影响其他输出 |
| 12 | 53 | 2026-09-06 00:31:47.904 日志为 `level=1 phase=HIGH index=5/13`；用户明确说“这个是板子上的灯” | LOW 时亮灭、具体 LED 标号 |
| 23 | 8 | 11:07:32/37 HIGH/LOW均约12V；11:09:33 STOP后用户实测近0V | DO2强候选；LOW与释放后的关断效果尚未区分 |

6.1 V 是用户测得的板端输出电压，**不是 GPIO5 自身的电压**；GPIO5、GPIO12 都属于固定 1.8 V 域。GPIO5 是出纸控制的强候选，GPIO12 已按用户观察记录为板载灯；不要把未测出的低电平响应写成已确认。

### TP、连通性和照片

- `TPxx` 是定制 PCB 的测试点编号，既不是 GPIO 编号，也不是模块物理脚号。必须分别标注这三类编号。
- 用户报告：TP18、TP29 对应出纸负端；TP19 对应出纸正端；TP20 对应仓门端子（具体端未定）；TP21 对应“中间第三根没有标的”。
- 用户曾口误 TP17，后更正为 TP18；最初“已测通”后来撤回为“没测”，不要把这些早期说法作为已验证连线。
- 曾在未断开电源时用电阻/蜂鸣档测量，显示可能约 0.8 MΩ，该次蜂鸣不构成可靠连通证据。
- 后续报告出纸负端与电源负极蜂鸣；TP19 到电源正极约 30 kΩ，交换表笔读数相同。先按用户报告保留；不据此确定固定负极、高边开关或电机使用 12 V。
- 测电阻/通断须断开 USB 和外部电源，并拔掉电机；带电观察输出使用 DC V 档。
- 用户在主动诊断前已确认电机及红外/NFC/LED/键盘等外接负载均拔掉，只留电源和 USB、万用表使用 DC V。延续该已确认条件，接线发生变化时按新事实处理。
- 照片支持“出纸可能是单向开关、仓门可能由 RZ7886 双向驱动”的假设，尚未追通完整线路。RZ7886 更靠近仓门接口，不能直接认定它控制出纸。
- 未找到可复核的该定制板公开原理图或源码；这不代表厂家没有内部资料。
- 当前近照中 SIM 在左、LTE 天线在右、电机接口在下，与手册 Top View 对齐；左边从上至下物理 1–17，下边左至右 18–34，右边下至上 35–51，上边右至左 52–68；LTE_ANT 为 46 脚。
- 最早照片与后续近照的模组个体标签不同，不要把不同板的正反面走线拼成同一网表。换层、器件遮挡和不清晰照片不能靠猜测补齐。

## 固件与诊断历史

| 版本 | 内容与结果 |
| --- | --- |
| 0.1.0 | 初始化 USB 命令与电机待机模板，2026-09-05 21:12:50 下载成功，STATUS/STOP 已验证；电机映射禁用 |
| 0.2.0 | GPIO27→28→24→25→26，LOW/HIGH/LOW 各 5 秒，75 秒单轮；23:20:45 开始，23:22:00 DONE，23:22:01 STOP |
| 0.2.1 | 同一 5 脚增加显式 `PROBE LOOP`；23:40:25 启动，已验证第二轮及后续第 11 轮，随后 STOP；用户报告这组未见输出电压变化 |
| 0.2.2 | 用户在文件更新完成前下载，main 版本已更新但包内仍是旧 GPIO 组，未据此认定新组上板；这是必须避免的混合打包问题 |
| 0.2.3 | 改为 GPIO15→14→19→18、固定 1.8 V，LOW/HIGH/LOW 各 5 秒、60 秒一轮；开机自动扫描；00:09:11 已到第 3 轮，00:09:18 STOP；用户报告也未见电压变化 |
| 0.2.4 | 扩大为 GPIO5→9→10→11→12→17→20→21→0→1→2→3→4，LOW/HIGH/LOW 各 5 秒，195 秒一轮；已核对实际候选，发现 GPIO5 的 6.1 V 响应及 GPIO12 板载灯关系；不能声称所有脚均完成电压测量 |
| 0.2.5 | 仅 GPIO5 HIGH/LOW 各 5 秒、10 秒循环、开机自动开始；当天上午 COM4 实测第 73 轮、候选 5，随后 STOP 成功 |
| 0.2.6 | DO2 的首个 20 候选准备版本，排除 GPIO5/12，高低各 5 秒、200 秒一轮；本地检查通过，未验证上板，随后按新截图调整顺序 |
| 0.2.7 | 历史实测版本，优先模块上边 9/10/11/1/4，随后 17 及其余候选；20 脚、200 秒自动循环，VLCD 分两段管理；全部本地检查通过，已核对上板并 STOP；用户反馈同组 20 脚未见 DO2 响应 |
| 0.2.8 | 最近实读板上版本，GPIO13→22→23、物理43→7→8，全固定 1.8 V，每脚高低各 5 秒、30 秒自动循环；检查通过、已核对上板并 STOP |
| 0.3.0 | 当前源码，单路迟滞液位反馈的手动一轮换水与单独补水、互锁/超时/故障处理；82/82 本地测试通过，默认 UNCONFIGURED，尚未刷写 |

历史 0.2.7 顺序为 `9,10,11,1,4,17,15,14,19,18,20,21,27,28,24,25,26,0,2,3`。前五脚位于模块上边：9/52、10/54、11/55、1/58、4/57，第六 17/50 在右上侧。旧电压域为固定 1.8 V（0–30 秒）、VLCD（30–50 秒）、固定 1.8 V（50–120 秒）、VMMC（120–170 秒）、VLCD（170–200 秒），第 100 秒 GPIO20 前关闭 UART2；域结束/STOP 后释放 GPIO 并关闭自启 LDO。这些均为历史行为，不是 0.2.8 的动作。

"未观察到电压变化"只说明当次测量端口和逐脚测试未触发响应，不能彻底排除组合控制、额外使能或供电条件。先前 20 候选已按 DO2 反馈暂停复扫，0.2.8 当时授权扫描范围仅 GPIO13/22/23；后续自动换水实现没有恢复扫描。

## 引脚与 API 注意事项

- GPIO23 与 USIM_CD 共用模块物理8脚，GPIO输出和SIM硬件在位检测不能同时占用。普通SIM通信使用独立CLK/DATA/RST/VDD，引脚复用为GPIO不等于必然无法读卡或联网；前提包括不启用该脚的硬件检测，且板上卡座检测触点没有与输出冲突的连接。当前src及V2.4.4默认库未发现主动启用CSDT/热插拔检测的代码，但未查询CORE当前或持久配置，不能宣称检测已关闭。此前“无需额外SIM配置”仅描述本次诊断未添加该动作，不能作为任何固件/接线下都不冲突的保证。DO2高低均12V、STOP后近0V也不能单凭现象归因于SIM复用冲突。

- 使用旧版 **LuatOS-Air / Lua 5.1 / LuaTask** API：`pins.setup`、`pio.pin`、`sys`、`pmd`；不要混入现代 LuatOS 的 `gpio.setup` 写法。
- `pins.setup(pin, 0/1)` 配置输出；`pins.setup(pin)` 配置输入。输出闭包无参数调用也可能切成输入，不能把它当成无副作用的读回。
- 输出显式使用 `pio.pin.setval(value, gpio)`，不借闭包读输出来判断电机身份。日志证明软件步骤，不能代替万用表测量；0.3.0 输入使用 `pio.pin.getval` 读取。
- Lua 中 `0` 为真，GPIO0 不能误当成无引脚；释放状态用 nil，写高低电平只用 0/1。
- 原生 setval/close/LDO 成功时可能无返回值，不要把 nil 一律当失败；显式 false、异常以及 setup/timer 的无效返回需按各 API 语义处理。
- GPIO14/15、18/19 都是固定 1.8 V 域：物理 32/31、38/37；GPIO5=49、GPIO12=53。不要套用其他模组的电压域。
- 历史组 GPIO24–28 使用 VMMC；GPIO0–4 使用 VLCD。`pmd.ldoset` 第一个参数是档位，不是伏数：档位 2 约 1.828 V，档位 13 约 3.054 V，不是 3.3 V。
- 历史 0.2.7 的 LDO/UART2 管理已从 0.2.8 删除；0.2.8 仅固定 1.8 V 三脚，不配置其他 GPIO、LDO、物理 UART、AT 或 SIM。0.3.0 同样不配置 LDO/物理 UART/AT/SIM，仅按已确认的配置访问顶部允许集合，不操作 USB 控制口复用；默认配置不访问 GPIO。
- 0.2.8 用户范围包含 GPIO13/22/23，并完成资料复核：13 的上电外拉高进入校准限制不禁止 Lua 运行后的输出；22 CP TX、23 SIM detect 可复用 GPIO。该次测试没有额外 AT/UART/SIM/LDO 动作。GPIO29–31 的无 SIM 等待 10 秒条件不能套用到 GPIO23；是否与实际板上外围或持久 SIM 配置冲突仍须核对。
- GPIO7（内部）、GPIO6/8/16（用途未明）及 SIM GPIO29/30/31 不在现行适配器允许集合中；USB_BOOT、复位、开关机、电源、地、模拟和天线物理脚不作 GPIO 扫描。
- RZ7886 原厂署名手册：BI=1、FI=2、GND=3、Vcc=4、FO=5/6、BO=7/8；FI/BI 的 10、01 为相反方向，00 高阻，11 低电平制动。该芯片真值表不能直接当作未知板级 GPIO 映射。

## 连接、刷写与日志排障

- 环境：Windows / PowerShell，工作目录 `E:\water-auto-exchange`；端口 115200、8N1，无握手，主机脚本 DTR/RTS 为 false。
- 已安装驱动：`unisoc_iot.inf`（运行态）、`sprd_rda.inf`（下载态）、`unisoc_iot_npi.inf`（NPI）。首次下载曾因 COM7 驱动不匹配失败，安装匹配驱动后恢复。
- 历史运行态 VID/PID=`1782:4E00`：COM3 Modem/MI02，COM4 USB 用户口/MI03，COM5 AP Diag/MI04，COM6 CP Diag/MI05；下载态 `0525:A4A7`，COM7 `SPRD U2S Diag`。COM 编号会改变，先枚举，不写死识别结论。
- COM4 被本应用接管后使用自定义命令，不是原生 AT 控制台。LuaTools 的“用户虚拟串口 COM4”提示不代表它已为用户打开命令终端；避免多个程序争用同一端口。
- 本地工具：`tools/vendor/luatools/Luatools_v3.exe`，已用版本 3.4.9；项目 `water-exchange`，配置 `tools/vendor/luatools/project/water-exchange.ini`。
- 底层保持 `LuatOS-Air_V4035_RDA8910_TTS_NOLVGL_FLOAT`，包在 `tools/vendor/CORE_V4035/`；默认库为 `tools/vendor/luatools/resource/8910_script/script_LuaTask_V2.4.4/lib/`。
- LuaTools 现行项目仅包含 `src/main.lua`、`src/water_config.lua`、`src/water_cycle.lua`、`src/water_control.lua`、`src/water_usb.lua` 五个入口及依赖文件，勾选默认扩展库及 USB trace。`src/` 还保留历史诊断文件，不要把“全部 src”当作当前下载清单。不要忽略依赖检查；没有备份原应用脚本，不得声称可以恢复原业务源码。
- **每条 require 独占一行。** “缺 sys”曾由 `sys, pins = require "sys", require "pins"` 被打包器误读为 `sys", require "pins.lua` 引起；拆行即可，默认库已有 sys.lua。
- 完成全部文件更新和检查后再通知刷写，最后更新版本号。下载后核对打包内容、STATUS 元数据及实际日志，防止仅 main 更新的混合包。
- `CME ERROR: 10` 是未检测到 SIM；本地 USB、GPIO、定时器和打印无需 SIM。`sys.init(0,0)` 不等待 SIM 或联网。
- 约 4 分钟后才出现 GPIO 日志的实证是 23:36:45 的 CME 提示与 23:40:25 主机发启动命令之间的间隔，GPIO 与 Lua trace 同时开始；不是 SIM 阻塞。旧诊断随后改为开机自动扫描；现行 0.3.0 改为开机立即打印状态、保持待机。
- `log.openTrace(true)` 不传 uartid，开启默认 trace；传 1/2 会涉及物理 UART，不要为排日志问题随意改变它们。`LOG_LEVEL` 不控制裸 print。
- 本轮没有确认 LuaTools 3.4.9 存在可用 HTTP 控制 API，不要再次要求用户打开猜测的菜单。界面状态偶有陈旧，应结合下载日志和应用 STATUS 验证，不盲目重复刷写。

## 文件职责、常用命令与检查

| 文件 | 用途 |
| --- | --- |
| `MEMORY.md` / `AGENTS.md` | 简明接续状态 / 完整约定、硬件证据与历史 |
| `src/main.lua` | 0.3.0 项目/版本、默认 trace、先 USB 后控制器初始化、立即及每 5 秒打印状态；不自动启动输出 |
| `src/water_config.lua` | 未确认时禁用的 GPIO/有效电平/隔离接线配置、采样与超时参数 |
| `src/water_cycle.lua` | 无硬件 I/O 的换水状态机、单独补水、互锁、防抖与故障锁存 |
| `src/water_control.lua` | 配置校验、GPIO 读写、单调时钟、定时采样、STOP 与异常关断 |
| `src/water_usb.lua` | STATUS、START、FILL、STOP、RESET 协议与状态格式化 |
| `tools/water-command.ps1` | 核对 `water_auto_exchange` / `0.3.0` 后发送应用命令；不发送 AT |
| `tests/water_cycle_test.lua` / `tests/water_control_test.lua` / `tests/water_boot_test.lua` | 现行状态机、硬件适配器、USB/启动的 Lua 5.1 模拟测试 |
| `src/gpio_probe.lua` / `src/usb_control.lua` | 历史 GPIO13/22/23 扫描与旧 USB 协议；不由 0.3.0 入口加载 |
| `src/motor_config.lua` / `src/motor_cycle.lua` | 保留的未启用电机配置与 3 秒双输入周期模板 |
| `tools/air724-probe.ps1` / `tools/install-air724-driver.ps1` | 端口识别、原生 AT 探测及驱动安装工具；应用 USB 用户口不作为 AT 口 |
| `tools/motor-command.ps1` / `tools/gpio-probe.ps1` | 旧电机/诊断工具，仅用于匹配的旧版固件；后者验证 0.2.8、3 候选、30000 ms 周期，默认采集 45 秒、范围 35–600 秒，Continuous 成功后保留板端运行 |
| `tests/motor_test.lua` / `tests/gpio_probe_test.lua` | 保留的旧电机/USB、GPIO 诊断回归测试；全部模拟测试均不等同实板电压测量 |

在仓库根目录运行。先枚举并确认 USB 用户口；下方 COM4 只是历史端口示例。用户刷入并核对 0.3.0 后使用现行工具：

```powershell
[IO.Ports.SerialPort]::GetPortNames()
& .\tools\water-command.ps1 -Port COM4 -Command STATUS
& .\tools\water-command.ps1 -Port COM4 -Command STOP
# 完成配置和实物验证后，由用户命令启动一轮换水或首次补水。
& .\tools\water-command.ps1 -Port COM4 -Command START
& .\tools\water-command.ps1 -Port COM4 -Command FILL
# 故障原因排除且输入重新稳定后复位；复位不会启动泵。
& .\tools\water-command.ps1 -Port COM4 -Command RESET
```

这些是各自独立的命令示例，不是应一次连续执行的脚本。默认未配置时 START/FILL 会被拒绝。最近实读板上仍是 0.2.8；未刷写前不要将其当作 0.3.0 运行。旧版查询/STOP 工具与诊断采集命令保留在 `docs/diagnostic-history.md`，历史 `PROBE LOOP` 会启动扫描，不能当作只读检查。

- 现行 0.3.0 上轮本地检查通过：状态机 17/17、适配器 24/24、USB/启动 14/14、旧电机/USB 15/15、旧诊断 12/12，合计 82/82；另有官方 LuaFLOAT 语法、PowerShell 解析及五文件下载清单/依赖/版本核对。尚无 0.3.0 实板联调结果。
- 0.2.8 检查全部通过：电机/USB/启动 18/18、GPIO 诊断 12/12、官方 LuaFLOAT 全部 5 个源码语法、PowerShell 解析、实际模块与主机元数据及 LuaTools 项目文件列表核对通过。已核对上板并 STOP。历史 0.2.7 检查为电机/USB/开机 **18/18**、诊断 **16/16**、5 个源码的 LuaFLOAT 语法、PowerShell 解析和主机/模块元数据一致性通过，并另有上板 STATUS/STOP 实证。0.2.6 历史为 **18/18**、**16/16** 及上述检查通过，未验证上板；0.2.5 历史为 **18/18**、**13/13**、语法/解析通过并另有上板实证。
- 测试通过 `tools/vendor/python` 中的 `lupa.lua51.LuaRuntime` 执行，对每个测试创建独立运行时，`arg[1]` 指向仓库根目录；保持 Lua 5.1 语义。
- 官方语法检查器：`tools/vendor/luatools/_temp/tools/luac_float.exe -p`，检查全部 `src/*.lua`。
- 0.3.0 有关输出的修改要检查未配置时零 I/O、进排水互锁、输入防抖、超时、STOP、故障锁存与 RESET、旧回调失效、时钟回绕/异常、部分初始化失败及关断失败报告。若修改历史 0.2.8 诊断模块，另检查精确周期、候选顺序、只访问 GPIO13/22/23、不访问 pmd/物理 uart；0.2.7 的电压域切换及 UART2 测试属于更早历史版本。纯记录修改不需要重跑硬件测试。
- 主机 Continuous 验证后退出不等于板端停止；以设备 STOP 应答和实际测量为准。主机采集出错时可能尝试 STOP，检查输出记录。

## 证据与资料索引

- 项目概览：[README.md](README.md)；简明接续：[MEMORY.md](MEMORY.md)；现行使用与配置：[docs/water-control.md](docs/water-control.md)；完整诊断历史：[docs/diagnostic-history.md](docs/diagnostic-history.md)；硬件假设、物理脚表与证据：[docs/board-control-analysis.md](docs/board-control-analysis.md)。
- 历史实证：`logs/motor-flash-verification-20260905.json`、`logs/gpio-probe-20260905-232045.log`、`logs/gpio-probe-20260905-234025.log`。
- LuaTools 原始日志：`tools/vendor/luatools/log/tools_20260905.txt`、`trace_2026-09-05_205909.txt`（使用 Windows 默认编码读取）；后者也包含 09-06 的记录。
- 当前板近照：`codex-clipboard-246008b2-4375-4529-9f1c-ab3892face8e.jpg`（正面）、`codex-clipboard-3466744b-5afe-4061-90f5-568e854af791.jpg`（背面）；最新模块 Top View 截图为 `codex-clipboard-18433a10-0a7a-4dbc-ad49-f289b761627a.png`，用于 0.2.7 的位置优先级。最早一组为 `bd4329da-a857-4df8-95da-ed60c800e85b`、`c442628a-0bdb-444f-a6db-fd453e93c882`；均曾位于 `C:/Users/haku/AppData/Local/Temp/`，临时文件可能不再存在。
- [合宙 GPIO 教程](https://docs.openluat.com/air724ug/luatos/app/driver/gpio/)、[日志教程](https://docs.openluat.com/air724ug/luatos/app/utils/log/)、[运行框架](https://docs.openluat.com/air724ug/luatos/app/service/framework/)。
- [Air724UG 硬件手册 V3.7](https://docs.openluat.com/air724ug/product/file/Air724UG_%E7%A1%AC%E4%BB%B6%E8%AE%BE%E8%AE%A1%E6%89%8B%E5%86%8C_V3.7.pdf)；本地曾保存 `C:/Users/haku/AppData/Local/Temp/air724ug-hardware-v37.pdf` 和 `air724ug-pinout-v37.png`。
- [RZ7886 原厂署名手册，立创镜像](https://atta.szlcsc.com/upload/public/pdf/source/20180613/C128852_BAAE7924E869DB4853C0D962680B5688.pdf)。

## Git 与后续接续

- 首次 Git 初始化按用户要求设置 `origin=git@github.com:princehaku/water-auto-exchange.git`，把分支从 master 改为 main，并成功 `git push -u origin main`；当时提交为 `9fa230d`（`feat: new`）。这是历史提交，不表示当前本地全部更改已同步。
- 2026-09-06 用户明确授权本次先更新 memory 和 agent，再提交推送现有项目成果。`MEMORY.md` 记录当前接续状态，`AGENTS.md` 保留完整规则和证据；提交成功与远程同步结果须据实际 Git 输出核对，不提前写成成功。
- `.gitignore` 排除 `logs/`、`build/`、`tools/vendor/` 及固件二进制；不要把本地驱动、工具大包、日志和设备身份数据顺手提交。
- 需要提交或推送时先读取最新 Git 状态，保留用户改动；按用户最新持续要求，每次修改验证后都要 commit 和 push，使用中文标准提交信息。
- 现行源码 0.3.0 已完成、默认 UNCONFIGURED、开机只打印状态；最近实测板上仍为 0.2.8，已核对 3 候选、30000 ms 并 STOP，旧固件重启仍会扫描。接续硬件联调先确认泵/阀规格、输出有效电平与液位板隔离反馈接线，再填写 `water_config.lua`；不能把 Git 推送或源码更新当作已刷写。
- GPIO23 列为 DO2 强候选；若继续实测，应单独区分 LOW 与释放后的关断效果，不能把两阶段均 12V 写成高低开关验证成功。用户已选择跳过 RD2D 近照辨认，不再要求近照，不复扫旧 20 脚，不默认拉高 GPIO5。GPIO5/12 的功能观察、低电平未确认状态及 RD2D 候选继续保留；历史电机 3 秒通断测试不得替代自动换水逻辑。

## 网站与部署约定（2026-09-06）

- 本地项目目录为 `E:\water-auto-exchange`；网站入口为 `https://bytegallop.com/water/`。
- 当前网站仅为独立静态项目页，尚无 Web 后端或网络设备控制。不得将网站可访问当作设备在线、0.3.0 已刷写或实际换水验证。
- 部署资料见 [docs/deployment.md](docs/deployment.md)，静态页及脚本在 `deploy/`；沿用现有 Nginx HTTPS 站点，与 `/sms/` 分开。修改站点配置须先备份、`nginx -t` 后 reload，并回归 `/sms/api/health`。
- 私钥、密码、Token、日志和工具包不得提交。后续修改验证完成后按用户持续要求 commit 并 push。
