# 2026-09-12 实板联网联调

## 上午接续：0.5.3已上板，0.6.0独立按钮已实现

- 用户10:13提供的0.5.2日志显示IP_READY之后TLS连接10秒TIMEOUT，尚未到WSS认证/心跳阶段，不能以旧心跳换算解释该次失败。
- 10:20:41现有trace记录0.5.3启动；10:22左右通过COM4实读STATUS确认版本和UNCONFIGURED。CPIN:READY、CEREG:2、CSQ8–10，之后持续waiting_pdp至本次观察结束；新版稳定WSS连接尚未验证。仅凭这些信息不能认定欠费、APN错误或天线损坏。
- 电脑以公开CA验证bytegallop.com的TLS1.2通过（一次约0.08秒），公网API200；生产状态为离线及旧0.5.2历史信息、一条此前STOP记录。本轮未发泵控制命令。COM3打开时报拒绝访问，未发送只读AT查询。
- 本轮没有computer-use所需node_repl/sky执行入口，未操作桌面。用户已保留助手刷写授权；最新0.6.0未刷写，不能声称实板联调完成。
- 独立按钮FILL补到高位、DRAIN排到低位后结束，完整START保留。138Lua、42Python和本地Edge无头HTTP联调通过；浏览器使用隔离模拟设备，桌面/手机截图在build/browser，不能作为真实在线证据。官方LuaFLOAT、PS语法、生成包与凭据及九文件清单核对通过。
- Web和后端已部署，备份 `/apps/water-auto-exchange/backups/20260912T022704Z-3677505`，容器healthy；公网四个静态文件一致、API及/sms200、凭据WSS probe通过。0.6.0本地项目water-online-0.6.0指向build/firmware。默认GPIO映射仍禁用，未验证真实水流。

下方为夜间联调记录，其中“0.5.3未上板”的历史快照已由上方COM4新证据更新。

本次用户授权助手自行使用computer-use下载和测试。项目是工科物联GK21.5PTM rev0.3、Air724UG-NFM，保留V4035 TTS NOLVGL FLOAT CORE。GPIO12作为已确认网络灯；泵和液位映射仍未配置。

## 已取得的实板证据

1. 修正LuaTools项目选择后，01:26:12下载完整0.5.2联网包成功。此前src项目中的网络配置是disabled、密钥为空，解释了版本更新后仍等待连接的问题。
2. 01:26:24.144进入HTTP升级，01:26:24.485升级成功，01:26:25.784打印 `WATER WS online`。真实4G、TLS和设备认证通过。
3. COM4返回 `project=water_auto_exchange version=0.5.2 state=UNCONFIGURED reason=mapping_not_confirmed ready=0 fill=0 drain=0 outputs_known=0`。服务器认证后的status也收到相同设备状态，没有注入模拟设备。
4. 向实际在线的未配置设备提交一次远程STOP，返回 `status=succeeded result=OK STOP stopped`。第一次检查设备正离线，未提交；第二次提交得到回执。未发START/FILL，未启动泵。

原始证据留在被忽略的 `tools/vendor/luatools/log/tools_20260912.txt` 和 `trace_2026-09-12_002549.txt`。日志可能包含设备标识，不提交原始文件。

## 稳定性缺陷和修正

01:27:41.414断开，01:27:45.219重新online；01:29:00.928再次断开。服务器last_seen只在认证后刷新，未看到每秒应用心跳。该结果不满足稳定连接要求。

[Air724官方rtos文档](https://doc.openluat.com/wiki/21?wiki_page_id=2247)将tick单位定义为5ms。本次通过同站公开API `/api/site/text?id=21927` 读取到正文。旧代码依据跨平台patch.lua使用tick差值/16，使1秒间隔成为约80秒，超过服务端75秒空闲期限。控制器防抖/超时、HTTP握手及重连退避也使用了错误换算。

0.5.3统一以tick差值乘5得到毫秒；保留异常时钟停机逻辑。同时允许较早但未过期、未确认的pong，避免移动网络延迟超过一个心跳间隔时误丢回执。重复、过期、旧会话或未发送序号不能续期。约每5秒打印心跳seq、ack和最后有效回执年龄，不打印认证内容。

## 本地验证

| 检查 | 结果 |
| --- | --- |
| Lua 5.1 | 130项通过：诊断12、电机15、启动18、适配器24、状态机17、网络31、WSS传输13 |
| Python API/WS/网关 | 39项通过 |
| 真实时基边界模拟 | 200 ticks/秒心跳；2000 ticks/10秒HTTP截止；12000 ticks/60秒稳定退避；控制器20 ticks/100ms防抖和200 ticks/1秒超时 |
| 心跳回执 | 延迟回执、重复、旧会话和错误序号检查通过 |
| 包与语法 | 8 Lua+1 CA，凭据与私有配置一致，源码一致、LuaFLOAT语法及PowerShell解析通过 |

这些是本地模拟和打包检查，不能等同0.5.3已在硬件上生效。CORE长期回绕行为也未经过实板长期观察。

服务器已部署0.5.3兼容，备份为 `/apps/water-auto-exchange/backups/20260911T175713Z-3513927`。容器healthy、公网water API及/sms返回200、既有设备凭据WSS probe通过；容器内调用纯格式校验确认接受0.5.3，未写入模拟遥测。

## 尚未完成

computer-use在检查Chrome网页时报告无法可靠确定当前URL，自动停止了本轮界面操作。之后没有再发UI输入。0.5.3生成包与 `water-online-0.5.3` 项目已准备，**尚未刷入**，实板仍为会间歇重连的0.5.2。

恢复工具后需刷新LuaTools项目列表，选择 `water-online-0.5.3`，下载脚本并核对实际版本、打包配置。随后观察seq/ack和服务器last_seen持续增长，至少跨过原75秒断线窗口，验证断线重连及USB/远程STOP。泵输出与液位联调仍取决于实际接线和有效电平确认。
