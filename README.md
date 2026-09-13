# Air724UG 自动换水

> 2026-09-13：生产实板保留 **0.8.2**。网页和服务端支持持续水位估算、按目标水位分轮补排水，以及补水3分钟/排水5分钟到时正常停止，无需刷板。目标作业单轮最多补水170秒/排水290秒，确认关闭后间隔2秒再续下一轮；断联或停止后不会自动续开。仓库固件源码和生成包均回滚至已投产的0.8.2，以后确有需要再修改板端。

`water_auto_exchange 0.8.2` 默认采用手动模式：FILL开启补水，DRAIN开启冲水（排水），STOP关闭。两路可同时开启；服务端从开启授权开始独立计时，补水180秒、排水300秒，重复ON和另一条开关不延期。任一路达到软上限时服务端要求全部关断并回到待机，可以直接再次开启，无需复位；未确认关断则终止会话，让固件失联保护生效。USB本地控制及自动液位模式仍保留板端180/300秒保护，避免离线调试无限开启。

上电待机，立即打印状态，之后每 5 秒打印一次。USB 支持 `STATUS`、`START`、`FILL`、`DRAIN`、`FILL_OFF`、`DRAIN_OFF`、`STOP`、`RESET`。已实现输入防抖、独立开关计时、阶段超时、故障锁存及可选超高水位输入。当前由手动命令开启/关闭对应输出，尚未设置定时计划。

**生产实板为0.8.2，本轮Web功能无需更新固件。** 补水DO2/GPIO23、排水出纸口/GPIO5，关断仍为写LOW后pins.close。WSS远程手动控制必须与支持软上限的服务端完成能力确认；失联保护由控制器独立计时，不被网络收发等待延长，重连不重开水。手动模式不要求传感器。

## 配置与使用

完整流程、接线条件、故障解释见 [自动换水说明](docs/water-control.md)。

- [water_config.lua](src/water_config.lua)：补水GPIO23、排水GPIO5，均采用off_mode="release"；液位输入默认不启用。
- [water_cycle.lua](src/water_cycle.lua)：不依赖硬件的换水状态机。
- [water_control.lua](src/water_control.lua)：旧版 LuatOS-Air GPIO、采样、定时和故障清理。
- [water_usb.lua](src/water_usb.lua)、[main.lua](src/main.lua)：USB 命令、启动与每 5 秒状态日志。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\water-command.ps1 -Port COM4 -Command STATUS
```

| 命令 | 行为 |
| --- | --- |
| `START` | 手动模式拒绝；自动液位模式可启动完整换水 |
| `FILL` | 手动模式开启补水，STOP关闭或超时停止 |
| `DRAIN` | 手动模式开启冲水（排水），STOP关闭或超时停止 |
| `FILL_OFF` | 只关闭补水，放水保持原状态 |
| `DRAIN_OFF` | 只关闭放水，补水保持原状态 |
| `STOP` | 尝试关断进排水；已锁存的故障继续保留 |
| `RESET` | 原因解除、输入稳定后清除故障，回到待机 |

软件输出记录不能代替电压或流量反馈。`outputs_known=0` 表示未初始化或输出写入结果不确定。液位板的一路继电器也不能报告连续水深，不能仅凭无补水请求断定水已到高位。

## 下载

已投入生产的0.8.2继续使用，更新网页与服务端即可获得本轮功能。源码与生成包已恢复至已发布的0.8.2，以下下载说明供恢复或重新准备同版本时使用。

在 LuaTools `water-online-0.8.2` 项目中保留 `LuatOS-Air_V4035_RDA8910_TTS_NOLVGL_FLOAT` CORE 和默认 LuaTask V2.4.4 库。项目配置已改为下载包清单中的 8 个 Lua 文件和 1 个 CA 证书；若界面仍显示旧清单，重新载入项目核对后，由用户点击“下载脚本”：

```text
build/firmware/main.lua
build/firmware/water_config.lua
build/firmware/water_cycle.lua
build/firmware/water_control.lua
build/firmware/water_usb.lua
build/firmware/water_network.lua
build/firmware/water_network_config.lua
build/firmware/water_ws_transport.lua
build/firmware/water-ca.crt
```

4G 上板使用同一生成目录中的整包文件，操作步骤见 [Web 与 4G 接入](docs/web-console.md)，避免把已含密钥的配置提交到 src。

下载后通过 `STATUS` 确认 `project=water_auto_exchange version=0.8.2`。默认应为 `state=IDLE reason=ready ready=1 outputs_known=1 control_mode=manual`、两路输出为0（字段顺序可不同）。旧 `motor-command.ps1` 和 `gpio-probe.ps1` 用于历史诊断，不操作新版。

## 本地验证

本地验证覆盖独立5秒失联关断、网络发送等待、有效心跳续期、服务端180/300秒软上限、重复ON不延期、超时全关后直接重开、真实故障保留锁存。网页按设备能力展示保护倒计时，水位与水量估算独立显示。Web多轮作业及0.8.2心跳估算使用本地模拟设备协议测试，不替代真实水流验收。

## 硬件依据与记录

板卡为工科物联 `GK21-SPTM_rev0.3`，模组 `Air724UG-NFM`，使用旧版 LuatOS-Air / Lua 5.1 FLOAT API。运行态曾枚举 COM3/4/5/6，其中 COM4 为应用 USB 端口；端口可能随连接变化。

| GPIO / 模块物理脚 | 用户已报告的观察 | 仍需确认 |
| --- | --- | --- |
| 5 / 49 | HIGH 时出纸端约 6.1V；用户补充切换GPIO后无电压 | 配置为排水；关断用LOW后release，新版实物操作待验证 |
| 12 / 53 | HIGH 阶段对应板上灯 | 0.8.2工作常亮，在线待机随心跳短闪，离线网络闪烁；保持生产固件行为 |
| 23 / 8 | DO2 HIGH/LOW 均约 12V，STOP/切换后近 0V | 配置为补水；关断用LOW后release，不据此声称单独LOW有效 |

两路已按用户确认的用途和完整关断流程配置；运行就绪不等于实际水流验证。液位板反馈必须是已确认隔离且不带外来电压的触点，或匹配 1.8V 的隔离接口；不能把探针或 12V 直接接 GPIO。具体约束见自动换水说明。

历史诊断源码与测试保留，不在新版启动路径中。快速接续见 [项目记忆](MEMORY.md)，完整记录见 [AGENTS.md](AGENTS.md)、[连接与诊断历史](docs/diagnostic-history.md)、[板级控制分析](docs/board-control-analysis.md)。

## 网站入口

项目入口：https://bytegallop.com/water/ 。现已实现带鉴权的控制台、状态/故障展示、命令与回执记录，以及 Air724UG 4G 独立接入。生产保持0.8.2，后续优先通过网页和服务端演进。部署脚本、Nginx 片段及操作说明见 [网站部署](docs/deployment.md)。本地仓库位于 `E:\water-auto-exchange`。

## 4G 与 Web

完整操作见 [Web 与 4G 接入](docs/web-console.md)。板端独立联网，USB 网关作为调试工具保留。设备使用 WSS 长连接，空闲每30秒、补水/冲水期间每1秒心跳，状态变化立即上报，流量估算每5分钟上报。网页每2秒向服务器自动刷新，不追加设备请求。后端使用 Python、wsproto 和 SQLite，通过 Docker 启动，部署目录统一为 `/apps/water-auto-exchange`，绑定服务器 127.0.0.1:8790；Nginx 代理 /water/api/。设备密钥与管理密钥分开保存。GPIO23补水和GPIO5排水已配置，定时计划仍待用户明确规则。
