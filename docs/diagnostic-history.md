# 连接与 GPIO 诊断历史（0.1.0–0.2.8）

以下是切换到 0.3.0 自动换水应用前的原 README，保留当时的上下文与记录。其中“当前”“最新”和开机自动扫描均指历史诊断版本；现行应用与命令请见仓库 README 和 water-control.md，不据此恢复旧扫描。

# Air724UG 自动换水项目

实际用途已明确为鱼缸/水箱类水位监测、自动排旧水和补新水。用户随后补充已有12V三探针液位控制板：图示A为公共探针，低于B自动补水、到C停止；实际探针、泵/阀及接线尚未确定。该板可承担正常补水上下限，自动换水还须排补水互锁，并讨论独立超高保护。下述3秒通断与GPIO扫描是板级诊断记录，尚未实现实际自动换水业务。

2026-09-06 11:07:32.916/11:07:37.927，用户贴出 GPIO23（模块物理8脚）的 HIGH/LOW 两阶段日志，并澄清 DO2 两针之间两个阶段都约 12V。记录为本次未观察到 GPIO23 使 DO2 通断；不能认定 GPIO23 控制 DO2，也不能证明它完全无关。GPIO13/22 的测量结果未提供。12V 是 DO2 板端电压，不是 GPIO23 的 1.8V 逻辑电压。

2026-09-06 11:09:33 左右 COM4 实读 `version=0.2.8 probe_state=RUNNING probe_gpio=23 probe_level=1 probe_continuous=1 probe_cycle=5 probe_total=3 probe_cycle_ms=30000 probe_candidates=13,22,23`，随后收到 `OK STOP stopped`。已确认 0.2.8 上板，本次扫描已停止；用户随后确认：不重启、STOP 成功后，DO2 两针之间已接近 0V。结合 STOP 前实际正在 GPIO23 HIGH 阶段，GPIO23 记为 DO2 控制强候选；但先前 HIGH/LOW 均约12V，STOP 同时包含写 LOW 和释放 GPIO，尚不能区分低电平关断与释放后关断，不能填写确定的高/低有效电平。 STOP 写 LOW 后释放 GPIO；近0V来自用户实测，不是仅由STOP应答推断。重启仍会自动扫描。

历史验证：2026-09-06 10:08 左右 COM4 实际读取 `0.2.7`、连续扫描第 2 轮、GPIO1 LOW、20 候选及 200000 ms 周期，候选顺序与本地和 LuaTools 临时打包脚本一致。随后收到 `OK STOP stopped`。电机业务映射仍未完整确认，原定通电 3 秒/断电 3 秒尚未启用。

前一组测量反馈：用户贴出 09:58:35.574 至 10:01:50.686 的 40 条 HIGH/LOW 阶段日志，并说“这些可排除”。日志覆盖 GPIO15、14、19、18、9、10、11、17、20、21、27、28、24、25、26、0、1、2、3、4，顺序匹配 0.2.6（未附版本字段）。按 DO2 测量未见响应记录，先排除这组重复单脚扫描；0.2.7 仅调整同一集合的顺序，无需为增加候选再次刷写。DO2 是固定 0 V 还是固定非零电压仍待用户回答，接口供电及控制拓扑尚未确认。

已记录 GPIO5（模块物理 49 脚）为出纸控制的强候选：用户贴出的 00:21:51.634 日志为 `gpio=5 physical=49 level=1 phase=HIGH index=1/13`，并报告此时出纸端万用表读数约 6.1 V。6.1 V 是用户测得的板端电压，GPIO5 自身属于 1.8 V 域；低电平时出纸端是否降至 0 V 尚未确认，因此不能直接启用电机映射。

用户最新选择跳过 RD2D 近照辨认，继续换 GPIO 测试 DO2。本地 `0.2.8` 已准备 GPIO13→22→23（模块物理脚 43→7→8），均为固定 V_GLOBAL_1V8；每脚 HIGH/LOW 各 5 秒，一轮 30 秒，开机自动持续循环并打印每个阶段。GPIO5/12 和先前 20 候选全部排除，不默认拉高 GPIO5；本轮不操作 LDO、物理 UART、AT 或 SIM 配置。主机期望 `version=0.2.8 probe_total=3 probe_cycle_ms=30000 probe_candidates=13,22,23`，默认采集 45 秒，可设范围 35–600 秒。检查全部通过：电机/USB/启动 18/18、GPIO 诊断 12/12、官方 LuaFLOAT 全部 5 个源码语法、PowerShell 解析、实际模块与主机元数据及 LuaTools 项目文件列表核对通过。现已实读核对上板并 STOP，无需重复下载。RD2D 继续保留为未确认候选，当前不再以近照为前置步骤。

本轮资料复核区分了启动条件与运行时复用：GPIO13 的上电外拉高校准限制不禁止 Lua 运行后输出；GPIO22 的 CP TX、GPIO23 的 SIM detect 均可复用 GPIO，不需要额外 UART/SIM/LDO 动作。SIM GPIO29–31 的无 SIM 等待条件不能套用到 GPIO23。完整记录见 [电机控制推断](docs/board-control-analysis.md)。

### 0.2.7 历史顺序与电压域

此前按 Top View 截图优先模块上边，`0.2.7` 顺序为 GPIO9、10、11、1、4、17、15、14、19、18、20、21、27、28、24、25、26、0、2、3。最先五脚对应物理脚 52、54、55、58、57，第六 GPIO17/物理50 位于邻近右上侧；位置优先级并未确认 DO2 布线。该集合现已暂停复扫。

每脚 HIGH/LOW 各 5 秒，一轮 200 秒，开机自动循环并打印阶段。电压域依次为固定 1.8 V（0–30 秒）、VLCD 档位 2/约 1.828 V（30–50 秒）、固定 1.8 V（50–120 秒）、VMMC 档位 13/约 3.054 V（120–170 秒）、VLCD 档位 2（170–200 秒）；VLCD 分两段开启并关闭。域结束或 `STOP` 时先释放 GPIO，再关闭本次开启的 LDO；第 100 秒进入 GPIO20 前关闭物理 UART2。主机默认采集 215 秒，核对 `0.2.7`、20 候选、200000 ms 及完整候选串。`0.2.7` 本地电机/USB/启动测试 18/18、诊断测试 16/16、全部 5 个源码文件的官方 LuaFLOAT 语法、PowerShell 解析及主机/模块元数据一致性检查均通过；现已核对上板并 STOP，无需再次刷入同组扫描。继续保持外接负载断开。测量依据和版本记录见 [电机控制推断](docs/board-control-analysis.md)。

`0.2.6` 保留为历史准备版本：电机/USB/启动测试 18/18、诊断测试 16/16、LuaFLOAT 语法、PowerShell 解析及元数据一致性检查通过，未验证上板；随后按新截图调整优先顺序为 `0.2.7`。

## 已记录的板级功能

| GPIO | 模块物理脚 | 用户观察 |
| --- | --- | --- |
| 5 | 49 | HIGH 时出纸端测得约 6.1 V；LOW 时的电压待确认 |
| 12 | 53 | 用户确认对应板载灯，提供的日志为 HIGH 阶段；低电平亮灭状态尚未单独确认 |
| 23 | 8 | DO2在HIGH/LOW均约12V，STOP后近0V；控制强候选，有效电平待核实 |

GPIO12 记录来自 2026-09-06 00:31:47.904 的 `gpio=12 physical=53 level=1 phase=HIGH index=5/13`。GPIO5 和 GPIO12 的低电平响应仍待确认，两者均不加入本次 DO2 扫描。

## 已验证的板子信息（2026-09-05—2026-09-06）

| 项目 | 实测结果 |
| --- | --- |
| 模组型号 | Air724UG |
| 固件 | `LuatOS-Air_V4035_RDA8910_TTS_NOLVGL_FLOAT` |
| 开发方式 | 当前运行旧版 LuatOS-Air 固件 |
| USB 控制串口 | COM4，`LUAT USB Device 1 AT`，接口 `MI_03`；当前由测试程序接收自定义命令 |
| 运行态串口 | COM3、COM4、COM5、COM6；其中 COM3：Modem（`MI_02`），COM5：AP Diag（`MI_04`） |
| 下载态串口 | COM7：`SPRD U2S Diag`，仅在下载模式使用 |
| USB 标识 | `VID_1782&PID_4E00` |
| Windows 驱动 | `unisoc_iot.inf`、`sprd_rda.inf`、`unisoc_iot_npi.inf` 均安装成功，退出码均为 0 |
| 首次基础查询 | 烧录测试程序前，`AT`、`ATI`、`AT+CGMI`、`AT+CGMM`、`AT+CGMR` 全部返回 OK |
| 当前程序 | `gk21_motor_test`，最近实测板上 `0.2.8` 已核对三脚/30秒元数据并成功 `STOP`；本地检查通过；电机业务配置保持 `enabled=0`、`mapping_confirmed=0` |

首次连接日志：`logs/air724-probe-20260905-202230-172.log`，该次仅安装电脑端驱动并读取模块信息。后续烧录及通信验证见 `logs/motor-flash-verification-20260905.json`。更换 USB 插口或切换运行/下载模式后，COM 编号可能变化，使用前重新检测。

## 板卡照片记录

根据用户提供的正反面照片，正面标有“工科物联”，PCB 型号丝印为 `GK21-SPTM_rev0.3`，模组标签为 `Air724UG-NFM`。

可见的接口及标记包括：

- USB 插座及 `VBUS`、`DM`、`DP`、`GND` 标记。
- SIM 卡座、天线座、`KEY1` 按键。
- 电源输入、输出及 `+12V`、`+3.9V` 标记。
- 出纸电机、仓门电机、人体红外、NFC、LED 接口、键盘、纸巾仓机芯信号。
- `RX`、`TX`、`GND` 标记，以及背面多处 `TP` 测试点。

这些是照片中可见的丝印，尚未核对各接口的实际针序、GPIO 映射及驱动能力。后续控制外设前，通过原理图或连通性测量确认。

## 出纸电机测试需求

目标：重复通电 3 秒、断电 3 秒（周期 6 秒）。电机测试代码已完成并烧录：`src/main.lua`、`src/motor_config.lua`、`src/motor_cycle.lua`、`src/usb_control.lua`；`0.2.0` 新增 `src/gpio_probe.lua`，用于独立的受限 GPIO 诊断，`0.2.1` 增加连续模式。`0.2.0` 代码通过官方 Lua 5.1 FLOAT 语法检查，`tests/motor_test.lua` 16/16、`tests/gpio_probe_test.lua` 12/12 通过。板上 USB 命令通信已验证；电机业务映射仍禁用，尚未实际驱动电机。受限 GPIO 诊断的进展单独记录如下。

用户提出的 `TP18`、`TP29` 最初仅为按位置猜测，不能作为 GPIO 编号使用。此前按完全断电、拔掉电机插头的步骤安排连通测量，以核对测试点与电机端子的关系；实际驱动芯片输入到模组焊盘的映射仍待确认。

用户随后报告测量 `TP18`、`TP29` 时万用表蜂鸣（曾口误为 TP17，已更正），并明确当时未断电，屏幕可能显示约 `0.8 MΩ`。具体档位和两支表笔的连接仍不清楚；带电电阻/通断测量不能作为接线判断依据。已要求断开 USB 和 12V 电源，再确认万用表档位及表笔插孔后重测。GPIO 映射保持未确认。

对照照片及器件文档后，优先验证“出纸采用单路开关，仓门采用 RZ7886 双向驱动”的假设；尚未确认。照片依据、原厂文档链接及现有程序的适用限制见 [电机控制推断](docs/board-control-analysis.md)。

最新用户报告：TP18/TP29 对应出纸电机负端，TP19 对应正端，TP20 对应仓门端子，TP21 对应“中间第三根没有标的”。已记录在上述分析文档；此次断电/拔电机条件和完整四针顺序待确认，不能据此配置 GPIO。

随后用户报告出纸负端与电源输入负极蜂鸣，支持共地这一候选关系；TP19 正极的开关或供电使能线路仍需追踪。该次蜂鸣的具体阻值和测量条件尚未明确，不能据此启用电机业务 GPIO。

### 旧诊断程序记录（0.2.4，已运行并停止）

该版本电机/USB/启动测试 18/18、诊断测试 19/19、官方 LuaFLOAT 语法检查及 PowerShell 解析均通过。以下保留 13 脚版本的历史行为；其后的 `0.2.5` 仅 GPIO5 高/低各 5 秒，已在当天上午实测运行并停止，本地检查结果为 18/18、13/13、LuaFLOAT 语法及 PowerShell 解析通过。后续 `0.2.7` 已如文首所述核对上板并停止。

- 上电自动按 GPIO5→9→10→11→12→17→20→21→0→1→2→3→4 连续诊断，每脚 LOW/HIGH/LOW 各 5 秒，一轮 195 秒。前 8 脚为固定 1.8 V 域，GPIO0–4 前开启 VLCD 档位 2（约 1.828 V），组末释放 GPIO 后关闭；进入 GPIO20 前关闭物理 UART2，不操作 USB 控制口。
- USB AT 口在测试程序启动后用于接收 `STATUS`、`START`、`STOP`、`PROBE`、`PROBE LOOP`（以换行结尾）。
- `STATUS` 返回项目、版本、电机状态、3000/3000 ms 周期、诊断状态 `probe_state`、当前 GPIO/电平、连续模式/轮次，以及 `probe_total`、`probe_cycle_ms`、`probe_candidates`。
- 未在 `motor_config.lua` 中确认映射及使能前，`START` 返回 `mapping_not_confirmed`，不配置输出。
- 映射确认后，`START` 才能开始 3 秒开/3 秒停；电机业务不会因自动诊断而启用。
- `PROBE` 显式启动一轮诊断，`PROBE LOOP` 显式启动连续诊断；`STOP` 取消诊断定时器、释放当前 GPIO，并关闭本次开启的 VLCD。停止只影响本次运行，下次上电仍会自动开始连续诊断。
- `tools/gpio-probe.ps1 -Port COM4 -Continuous` 遇到已运行的自动扫描时附着采集日志，不重复发送启动命令；核对 `0.2.4` 和完整 13 脚候选串，默认采集 210 秒以确认进入下一轮。

已通过以下电脑端命令验证，两次退出码均为 0：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\motor-command.ps1 -Port COM4 -Command STATUS
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\motor-command.ps1 -Port COM4 -Command STOP
```

首次 `0.1.0` 烧录后的 `STATUS` 实测响应（历史记录）：

```text
OK STATUS project=gk21_motor_test version=0.1.0 state=STANDBY enabled=0 mapping_confirmed=0 ready=0 reason=mapping_not_confirmed on_ms=3000 off_ms=3000
```

当时 `STOP` 先返回上述待机状态，再返回 `OK STOP stopped`。该次验证了程序运行和 USB 命令通信，未验证电机接线或 GPIO 映射，也未启动电机。后续 `0.2.0`、`0.2.1` 均先通过 COM4 `STATUS` 确认版本和待机状态，再启动诊断。

测试程序占用 USB 虚拟 AT 口时，原生 AT 查询脚本不再适用；先使用 `STATUS` 确认程序。运行命令工具时要让 Luatools 释放同一串口。

### 受限 GPIO 诊断（0.2.0 / 0.2.1）

用户已明确确认所有电机、人体红外、NFC、LED、键盘等外接负载均已拔掉，仅保留电源和 USB；万用表使用直流电压档（DC V）。这次诊断用于观察板端电压随候选 GPIO 电平的变化，不代表这些 GPIO 已确认为电机控制脚。

一轮按下表顺序运行，每脚依次 LOW 5 秒、HIGH 5 秒、LOW 5 秒，共约 75 秒。每 5 秒输出日志，标明 GPIO、模块物理脚号和电平。

| 顺序 | GPIO | 模块物理脚 |
| --- | --- | --- |
| 1 | GPIO27 | 25 |
| 2 | GPIO28 | 26 |
| 3 | GPIO24 | 27 |
| 4 | GPIO25 | 29 |
| 5 | GPIO26 | 30 |

诊断启用 VMMC 档位 13，名义电压约 3.054 V。完成或停止时释放当前 GPIO，并关闭本次诊断启用的 VMMC。`PROBE` 只运行一轮；`0.2.1` 的 `PROBE LOOP` 显式启动连续重复。`START` 仍专用于原来的 3 秒电机循环，当前因映射未确认而拒绝启动。

电脑端启动及日志采集：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\gpio-probe.ps1 -Port COM4
```

连续模式使用 `-Continuous`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\gpio-probe.ps1 -Port COM4 -Continuous
```

日志保存为 `logs/gpio-probe-YYYYMMDD-HHMMSS.log`。连续模式的主机工具验证进入第二轮后可退出，板端仍会运行；需要停止时，先让占用 COM4 的工具释放串口，再使用 `tools/motor-command.ps1 -Port COM4 -Command STOP`。

首轮实证记录：2026-09-05 23:20:45.307 启动，按 GPIO27、28、24、25、26 顺序输出 15 条 LOW/HIGH/LOW 阶段日志；23:22:00.346 返回 `PROBE state=DONE event=END total=5 duration_ms=75000`；23:22:01.344 的 `STATUS` 为 `probe_state=DONE probe_gpio=none`。主机最后发送 `STOP`，23:22:01.405 收到 `OK STOP stopped`，工具退出码为 0。日志为 `logs/gpio-probe-20260905-232045.log`。该轮已停止，没有自动重复；该日志仅证明预定序列完成，不能确认板端电压或电机 GPIO 映射。

用户随后明确要求持续运行。`logs/gpio-probe-20260905-234025.log` 记录：23:40:25.944，`STATUS` 实际识别 `version=0.2.1`；23:40:25.995 启动连续模式；23:41:41.046 出现 `event=NEXT_CYCLE cycle=2`。23:41:56.002 主机工具退出时，`STATUS` 仍为 `probe_state=RUNNING probe_continuous=1 probe_cycle=2`，板端继续运行。

后续通过 `tools/motor-command.ps1` 查询到 `probe_state=RUNNING probe_gpio=25 probe_level=0 probe_continuous=1 probe_cycle=11`，随后发送 `STOP` 并收到成功应答。用户反馈 GPIO24、25、26、27、28 测试时均未观察到电压变化；这一结果只覆盖本次逐脚 LOW/HIGH/LOW 的测量条件，不能据此排除组合控制、额外使能或其它供电条件。

关于此前约 4 分钟后才看到扫描日志：23:36:45 已有 CME 日志，23:40:25.995 主机启动诊断时 Lua trace 同时出现。这个间隔来自旧版等待主机命令；`0.2.3` 已改为上电自动扫描。

### 烧录记录（2026-09-05）

使用 `tools/vendor/luatools/Luatools_v3.exe`（v3.4.9）的图形界面，在 `water-exchange` 项目中选择同版本底包 `tools/vendor/CORE_V4035/LuatOS-Air_V4035_RDA8910_TTS_NOLVGL_FLOAT.pac`，添加 `src/` 下的应用脚本，并使用默认旧版 LuaTask V2.4.4 库。首次 `0.1.0` 包含 4 个应用脚本，`0.2.0` 增加 `gpio_probe.lua`。项目配置保存在 `tools/vendor/luatools/project/water-exchange.ini`；用于本地检查的脚本及配套库位于 `build/motor-test/`。

通过界面“下载脚本”烧录，保留原 V4035 底层。首次 `0.1.0` 下载前通过语法及依赖检查，生成脚本包大小为 24,257 字节，脚本区容量为 425,984 字节。烧录会替换原应用脚本；没有备份原应用脚本。

此前曾遇到模块重启超时和下载态 COM7 驱动不匹配。关闭重复的 LuaTools 实例、安装匹配的 `sprd_rda.inf` 和 `unisoc_iot_npi.inf` 后，COM7 识别为 `SPRD U2S Diag`，重新下载成功。21:12:49，CmdDloader 记录 `DownLoad Passed`；21:12:50，LuaTools 记录“下载成功”，界面显示绿色成功状态。随后在恢复的运行态 COM4 上完成上述通信验证。

验证摘要：`logs/motor-flash-verification-20260905.json`。工具原始日志：`tools/vendor/luatools/log/tools_20260905.txt`。

`0.2.0` 诊断固件于 23:16:55 获得 LuaTools“下载成功”，随后在 COM4 用 `STATUS` 确认版本及 `probe_state=IDLE`。准备期间出现的“缺 sys”提示实际源于 LuaTools 依赖解析：`gpio_probe.lua` 将 `sys, pins = require "sys", require "pins"` 写在同一行，被误解析为文件名 `sys", require "pins.lua`。将两个 `require` 拆成独立行后修复；默认库原本已包含 `sys.lua`，无需额外复制。修复后官方 LuaFLOAT 语法检查及上述 16/16、12/12 测试均通过。

23:58:23，用户手动下载标为 `0.2.2` 的脚本时，源文件更新尚未全部完成，`temp_script` 内仍为 GPIO27、28、24、25、26；当时虽报告 `version=0.2.2`，并未据此认定新组已烧录，诊断保持 `IDLE`。后来重新下载的 `0.2.3` 已核对打包序列为 GPIO15、14、19、18，且 `STATUS` 实测为 `probe_state=RUNNING probe_gpio=15 probe_level=1 probe_continuous=1 probe_cycle=3`，随后 `STOP` 成功。`tools/vendor/luatools/log/trace_2026-09-05_205909.txt` 对应记录为 2026-09-06 00:09:11.538 `NEXT_CYCLE cycle=3`、00:09:16.552 GPIO15 HIGH、00:09:18.471 `STOPPED event=STOP`。`0.2.3` 的电机/USB/启动测试 18/18、诊断测试 13/13、官方 LuaFLOAT 语法检查及 PowerShell 解析均通过；用户反馈第二组也未见电压变化。

## 连接与读取

1. 用支持数据传输的 USB 线连接板子的模组 USB 接口，并启动模组。
2. 若 Windows 出现缺驱动设备，安装合宙官方 Air724UG / 展锐 8910 USB 驱动。
3. 在项目目录执行设备检测：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\air724-probe.ps1
   ```

4. 当前测试程序运行时，使用 COM4 查询应用状态：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\motor-command.ps1 -Port COM4 -Command STATUS
   ```

`air724-probe.ps1` 不带参数时只列出端口。仅当 USB 端口提供原生 AT 功能、尚未被测试程序接管时，才使用 `-Port COM4` 查询模组基本信息：脚本以 115200 / 8N1 发送 `AT`、`ATI`、`AT+CGMI`、`AT+CGMM`、`AT+CGMR`，握手失败即停止，结果保存在 `logs/`。这些查询不刷写或复位模组。运行命令工具前关闭占用同一串口的调试工具。

当前板子的 AT 口是 `MI_03`（设备实例路径尾号 `0003`），已通过实测确认。不要将其他固件文档中的 `0006` 直接套用于这块板子，也不要将电脑自带的 COM1 当作板子的串口。

如果只有电源灯亮、Windows 中没有新增 USB 设备，先检查数据线、USB 接口和模组开机状态。如果设备已经出现但无 COM 口，再检查驱动。

## 开发方式

当前采用旧版 LuatOS-Air V4035、Lua 5.1 FLOAT 和 LuaTask V2.4.4，使用旧版 `pins`、`sys`、`uart.USB` 接口。AT 命令有应答并不代表当前是纯 AT 固件；不要直接使用新版 LuatOS-SoC 的 API 或固件覆盖当前环境。

## 官方资料

- [Air724UG 软件环境与 USB 驱动](https://docs.openluat.com/air724_soc/luatos/common/swenv/)
- [Air724UG AT 命令手册](https://docs.openluat.com/air724ug/at/app/at_command/)
- [Luatools 使用说明](https://docs.openluat.com/common/Luatools/)

官方驱动已下载并解压到 `tools/vendor/`，已通过 Windows PnPUtil 安装 `unisoc_iot.inf`、`sprd_rda.inf`、`unisoc_iot_npi.inf`，系统发布名分别为 `oem225.inf`、`oem229.inf`、`oem233.inf`，三项退出码均为 0。运行态和下载态使用不同的设备驱动，运行态端口正常不能证明下载态 COM7 驱动已匹配。安装程序和三个驱动目录文件（`.cat`）的数字签名均有效。安装日志保存在 `logs/driver-install.log`，结果摘要为 `logs/driver-install-status.json`。需要重新安装时，在管理员 PowerShell 中执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\install-air724-driver.ps1
```

## 首次检测记录（2026-09-05）

最初 Windows 仅检测到系统串口 `COM1`。更换连接后出现三个 `Unisoc Generic Serial` 设备，错误码 28（缺驱动）。安装匹配驱动后，COM3、COM4、COM5 状态均为正常，COM4 基础查询成功。
