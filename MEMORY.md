# 项目记忆

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
- Git：分支 `main`，远程 `git@github.com:princehaku/water-auto-exchange.git`。用户本次明确要求先更新 MEMORY/AGENTS 再提交推送；这不是以后每次修改自动推送的授权。
- `.gitignore` 排除 `logs/`、`build/`、`tools/vendor/` 及固件二进制。此次同步代码、测试和文档，不把工具包、驱动、日志或临时照片加入仓库；其他机器按 README 重建本地 LuaTools 项目。

详细使用见 [自动换水说明](docs/water-control.md)，硬件依据见 [板级分析](docs/board-control-analysis.md)，原始诊断过程见 [诊断历史](docs/diagnostic-history.md)。
