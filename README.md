# Air724UG 自动换水

> 2026-09-12：当前源码 **0.7.2**，提供补水/冲水手动开关，不要求水位传感器。对照短信项目后将建连等待由10秒调整为60秒，加入耗时日志与受限频率的TCP诊断；CA校验和活动10秒失联停止保留。160项Lua、47项后端及本地浏览器联调通过；完整9项下载包已准备。本轮未刷写，最新实板证据为0.7.1。见[网络恢复与项目对照](docs/network-recovery.md)。

`water_auto_exchange 0.7.2` 默认采用手动模式：FILL开启补水，DRAIN开启冲水（排水），STOP关闭。两路互锁，单次默认最多120秒；自动液位模式须显式配置。本地USB控制不需要SIM，4G远程控制需要可用数据连接。

上电待机，立即打印状态，之后每 5 秒打印一次。USB 支持 `STATUS`、`START`、`FILL`、`DRAIN`、`STOP`、`RESET`。已实现输入防抖、进排水互锁、阶段超时、故障锁存及可选超高水位输入。当前由手动命令开启/关闭对应输出，尚未设置定时计划。

**0.7.2已生成、尚未刷入。** 最新板端日志为0.7.1/UNCONFIGURED。默认输出映射仍未填写且禁用；须确认输出接线及有效电平。手动模式不要求液位反馈。

## 配置与使用

完整流程、接线条件、故障解释见 [自动换水说明](docs/water-control.md)。

- [water_config.lua](src/water_config.lua)：填写补水、排水输出和液位反馈输入；默认全部未映射。
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
| `STOP` | 尝试关断进排水；已锁存的故障继续保留 |
| `RESET` | 原因解除、输入稳定后清除故障，回到待机 |

软件输出记录不能代替电压或流量反馈。`outputs_known=0` 表示未初始化或输出写入结果不确定。液位板的一路继电器也不能报告连续水深，不能仅凭无补水请求断定水已到高位。

## 下载

在 LuaTools `water-online-0.7.2` 项目中保留 `LuatOS-Air_V4035_RDA8910_TTS_NOLVGL_FLOAT` CORE 和默认 LuaTask V2.4.4 库。项目配置已改为下载包清单中的 8 个 Lua 文件和 1 个 CA 证书；若界面仍显示旧清单，重新载入项目核对后，由用户点击“下载脚本”：

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

下载后通过 `STATUS` 确认 `project=water_auto_exchange version=0.7.2`。默认应为 `state=UNCONFIGURED reason=mapping_not_confirmed`。旧 `motor-command.ps1` 和 `gpio-probe.ps1` 用于历史诊断，不操作新版。

## 本地验证

Lua 5.1模拟160项、Python后端47项和本地Edge浏览器联调通过。官方LuaFLOAT语法及下载包检查通过。测试未控制实板GPIO，本次实板网络恢复仍待刷入后确认。

## 硬件依据与记录

板卡为工科物联 `GK21-SPTM_rev0.3`，模组 `Air724UG-NFM`，使用旧版 LuatOS-Air / Lua 5.1 FLOAT API。运行态曾枚举 COM3/4/5/6，其中 COM4 为应用 USB 端口；端口可能随连接变化。

| GPIO / 模块物理脚 | 用户已报告的观察 | 仍需确认 |
| --- | --- | --- |
| 5 / 49 | HIGH 时出纸端约 6.1V | LOW 的响应及完整启停映射 |
| 12 / 53 | HIGH 阶段对应板上灯 | LOW 亮灭 |
| 23 / 8 | DO2 HIGH/LOW 均约 12V，STOP 后近 0V | LOW 与释放的关断效果；GPIO23 还复用 SIM 在位检测 |

上述记录尚不足以填入补水/排水泵配置。液位板反馈必须是已确认隔离且不带外来电压的触点，或匹配 1.8V 的隔离接口；不能把探针或 12V 直接接 GPIO。具体约束见自动换水说明。

历史诊断源码与测试保留，不在新版启动路径中。快速接续见 [项目记忆](MEMORY.md)，完整记录见 [AGENTS.md](AGENTS.md)、[连接与诊断历史](docs/diagnostic-history.md)、[板级控制分析](docs/board-control-analysis.md)。

## 网站入口

项目入口：https://bytegallop.com/water/ 。现已实现带鉴权的控制台、状态/故障展示、命令与回执记录，以及 Air724UG 4G 独立接入。设备需刷入并验证 0.5.1 后上线。部署脚本、Nginx 片段及操作说明见 [网站部署](docs/deployment.md)。本地仓库位于 `E:\water-auto-exchange`。

## 4G 与 Web

完整操作见 [Web 与 4G 接入](docs/web-console.md)。板端独立联网，USB 网关作为调试工具保留。设备使用 WSS 长连接，空闲和活动期间均每 1 秒心跳，状态变化立即上报。后端使用 Python、wsproto 和 SQLite，通过 Docker 启动，部署目录统一为 `/apps/water-auto-exchange`，绑定服务器 127.0.0.1:8790；Nginx 代理 /water/api/。设备密钥与管理密钥分开保存。默认 GPIO 配置仍禁用，定时计划仍待用户明确规则。
