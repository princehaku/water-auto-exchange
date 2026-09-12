> 当前源码0.7.4，默认手动开关，不要求传感器或水流验证。后面的液位流程仅用于未来automatic模式。

# 手动开关控制（0.7.4）

当前默认 `water_config.mode="manual"`，输出配置已启用。补水接顶部DO2，由GPIO23控制；排水接出纸电机口，由GPIO5控制。FILL打开补水、DRAIN打开冲水，STOP关闭。没有水位传感器也可以使用，need_fill引脚不会被初始化或读取，状态报告为unknown。不能在一路开启时直接开启另一路，先STOP关闭即可切换。

两路开启均为先`pins.setup(gpio, 0)`，再`pio.pin.setval(1, gpio)`；关闭采用`off_mode="release"`：先`pio.pin.setval(0, gpio)`，再`pins.close(gpio)`，与用户实测旧扫描切换GPIO后无电压的完整流程一致。`off_level=0`只是释放前的写入，不声称单独LOW已经验证关断。上电先对两路执行完整关闭，不自动开水；每次重新开启都重新建立输出模式。接口历史测量约DO2 12V、出纸口6.1V，不代表模组GPIO本身电压或已确认负载额定值。

写LOW失败时仍尝试close；任何一步失败则保留输出未知/故障，禁止开启另一路。STOP失败后即使重试清理成功，也须重启以恢复控制轮询。旧配置未指定off_mode或显式hold时，仍保持原来的持续OFF电平方式。0.7.4尚待下载，当前测试为模拟GPIO与本地HTTP/浏览器联调，不能替代新版本实板验证。

手动模式保留单次120秒超时，可按实际用途调整timing中的fill_timeout_ms/drain_timeout_ms；超时会关闭并锁存FAULT。可选overflow启用时仍受监测；未启用时不需要任何输入引脚。远程活动连接失联会尝试停止输出。RESET只复位，不重新打开。

网页开关和设备回报同步，按同一开关第二次会发送STOP。此阶段只验收软件开关和命令逻辑，不要求实际水流验证。当前生成包是0.7.4，下载清单仍为8Lua+CA，见[Web与4G接入](web-console.md)。

## 自动液位控制参考（需显式配置mode=automatic）

当前应用为 `water_auto_exchange 0.6.0`，使用既有 Air724UG V4035 FLOAT / LuaTask V2.4.4。本地USB控制无需SIM或网络，网页控制需4G联网；通过命令启动一轮，重启后待机，不恢复未完成的换水。还未加入定时计划或持续自动补水。

## 液位板的信号

根据用户提供的 12V 液位板接线图，A 是最低公共探针，B 是下限，C 是上限。水位低于 B 后继电器请求补水，到 C 后解除请求。B 与 C 之间保持之前的状态，因此只有一个带记忆的 `need_fill` 信号：

- `true`：补水请求有效，持续到水面触及 C。
- `false`：补水请求无效，持续到水面降至 B 以下。

这个信号不能报告水深、百分比，也不能在开机时单凭 `false` 证明水已在 C。开始换水前应确认探针位置及液位板的实际工作状态；`START` 只检查信号稳定且没有补水请求。

## 一轮流程

```mermaid
stateDiagram-v2
    IDLE --> DRAINING: START 且无补水请求
    DRAINING --> SETTLING: START模式，补水请求有效并通过防抖
    IDLE --> DRAINING: DRAIN 且无补水请求
    DRAINING --> DONE: DRAIN模式，补水请求有效并通过防抖
    SETTLING --> FILLING: 进排水均关，等待 1 秒
    FILLING --> DONE: 补水请求解除并通过防抖
    IDLE --> FILLING: FILL 且有补水请求
    DONE --> DRAINING: 再次 START
    DRAINING --> FAULT: 超时或异常
    FILLING --> FAULT: 超时或异常
    FAULT --> IDLE: 排除故障后 RESET
```

`DONE` 保持关闭，不开始下一轮。任意运行阶段 `STOP` 都尝试关断两个输出；故障已经锁存时仍保留 `FAULT`，须显式 `RESET`。复位只回到待机，不重新启动泵。额外超高输入在任何状态触发都会锁存故障。

读取液位每 100ms 一次，正常液位转换防抖 500ms，排水转补水间隔 1000ms。排水、补水超时各为 120000ms；这些是初始参数，必须按实际容积和流量调整。软件的停机响应依赖 Lua 事件循环正常运行，不能代替独立硬件限位。

## 接线与配置

修改 `src/water_config.lua`。默认 `enabled=false`、`mapping_confirmed=false`、`wiring_confirmed=false`，全部 GPIO 和有效电平均为空，此时 `UNCONFIGURED`，程序不配置输出，也不会扫描引脚。

| 配置 | 实际含义 |
| --- | --- |
| `outputs.fill` | 补水泵或进水阀的控制输出：GPIO、实测开启和关闭电平 |
| `outputs.drain` | 排水泵或排水阀的控制输出：GPIO、实测开启和关闭电平 |
| `inputs.need_fill` | 液位板继电器隔离反馈：GPIO、请求补水时的电平、上下拉 |
| `inputs.overflow` | 可选额外超高输入，默认禁用；填写 GPIO、触发电平和上下拉 |
| `timing` | 防抖、切换等待、排水及补水超时，单位 ms |

启用前应先确认下面这些实际连接，然后才把三个确认/使能开关设为 `true`：

1. 补水和排水各自能被对应输出独立关断；验证上电、运行、STOP 的实际表现。液位板的自动补水接线必须受补水许可约束，不能绕过控制器自行启动泵，否则排水时会同时补水。
2. 液位板供电与 Air724 输入接口分清。卖家图的 12V 是液位板供电，图中继电器另一侧接了市电泵。不能把该泵侧带电触点、12V 或 A/B/C 电极直接接 Air724 的 1.8V GPIO。只使用确认隔离、没有外来电压的干接点，或匹配 1.8V 的隔离反馈电路。
3. 干接点使用 COM 接逻辑地、NO 接上拉输入时，通常闭合读 0；仍须实测“请求补水”对应的触点状态后填写 `active_level`。程序未预填该极性。
4. 实测低于 B、到 C 及 B/C 之间的保持行为。电极、水质和控制板供电必须能可靠检测。单路普通触点无法可靠区分断线、控制板失电与正常的某一状态；超时不能覆盖全部溢流风险。

当前适配器只接纳固定 1.8V 域且已有资料支持的 GPIO：`5,9,10,11,13,14,15,17,18,19,22,23`（GPIO12保留给网络灯）。这是可配置集合，不代表这些脚在定制板上空闲。输入、输出必须各用不同 GPIO。未自动处理 LDO、物理 UART 或 SIM 复用；选脚前须核对现有外围，尤其 GPIO23 的 SIM 在位检测和 GPIO13 的启动条件。

已有测量不能直接填成泵映射：GPIO5 HIGH 时出纸口约 6.1V，LOW 未确认；GPIO12 对应板灯；GPIO23 的 DO2 在 HIGH/LOW 阶段均约 12V，而 STOP 后约 0V，尚未分清写 LOW 与释放引脚的效果。新程序使用明确的 `off_level` 保持关断，不以释放 GPIO 作为关断方式。

可选 `overflow` 是软件额外输入，不等于独立硬件断水。若启用，超高触发不等待正常防抖，最迟在下一次有效轮询/命令采样时关断；解除后需稳定 500ms 才允许 RESET。失电关闭的阀、独立硬件断流方案须另按实际设备确定。

## USB 使用与状态

使用新工具，COM 编号以当前枚举为准：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\water-command.ps1 -Port COM4 -Command STATUS
```

| 命令 | 行为 |
| --- | --- |
| `STATUS` | 查询状态，不启动输出 |
| `START` | 手动启动一次“排水→补水”；有补水请求时拒绝，先处理初始水位 |
| `FILL` | 有补水请求时，只补水到 C；适合首次加水 |
| `DRAIN` | 单独冲水：排到B低位后停止，不自动补水；已请求补水时拒绝 |
| `STOP` | 取消当前运行并尝试关断两个输出，继续监测输入 |
| `RESET` | 故障原因解除且输入重新稳定后，清除锁存并待机 |

将示例命令最后的 `STATUS` 替换为所需命令即可。主机先核对项目及版本，避免把 `START` 发给旧电机测试程序。不使用旧 `motor-command.ps1` / `gpio-probe.ps1` 操作新版。

开机立即打印 `WATER STATUS`，之后每 5 秒一次；状态变化也打印。USB 命令响应带 `OK`/`ERROR`，USB 控制口不可用时不初始化输出。`ready=1` 表示配置、轮询和输入稳定条件具备，不表示水一定到 C；正在运行时重复 START 仍返回 `busy`。

`fill`、`drain` 是软件输出记录，不是板端电压/水流反馈；写入失败时可能保留最后的开启请求。`outputs_known=0` 表示输出尚未初始化或写入结果不确定，此时不能据 `fill=0` 推断硬件已断电。故障原因会保留原始原因，并附加关断失败信息。

`drain_timeout`、`fill_timeout`、`overflow`、传感器读取错误等会锁存为 `FAULT`。关断失败时两个输出都会独立尝试关闭，不能仅凭 STOP 应答代替电压确认。轮询定时器失效后禁止重新 START/FILL/DRAIN，修复问题后重启应用；普通传感器故障则可恢复稳定后 RESET。

## 下载与验证边界

LuaTools选择water-online-0.7.1项目，使用build/firmware/flash-files.txt中的完整8个Lua文件和1个CA证书，保留既有CORE、默认库及USB trace。准备与验证步骤见[Web与4G接入](web-console.md)。旧water-exchange的5文件清单不包含网络功能，不能用于当前联网版本。用户已授权助手刷写；执行工具不可用时不能宣称已下载。

本地测试覆盖控制逻辑、故障、单独冲水不自动补水及端到端命令交付。所有GPIO测试都是模拟，最近实板日志为0.7.0/manual/UNCONFIGURED，0.7.1尚未刷入；实际泵、探针测试不在本阶段范围内。
