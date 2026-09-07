# Docker 部署

## 当前部署（2026-09-08）

所有本项目的服务代码、网页、配置、数据库、Docker 运行目录和备份集中在 `/apps/water-auto-exchange`。API 使用真实 Docker Engine 29.8.0 运行，容器名 `water-console`，Python 3.12，UID/GID 为 10001。原 systemd 应用服务已停用并归档。

| 内容 | 路径 |
| --- | --- |
| 应用源码 / 镜像构建 | `server/`、`deploy/Dockerfile` |
| 网页 | `www/water/` |
| 私有配置 | `config/water.env`，root 600 |
| 数据库 | `data/water.db`，容器挂载到 `/data` |
| Nginx 项目配置 | `config/nginx-water.conf` |
| Docker 二进制、引擎数据与服务定义 | `runtime/` |
| 备份 | `backups/`，目录权限 700 |
| 旧部署归档 | `backups/legacy-systemd/` |

共享 Nginx 继续负责主站 TLS 和静态文件服务，通过 `/etc/nginx/snippets/water-auto-exchange.conf` 的符号链接加载项目配置。API 仅监听宿主 `127.0.0.1:8790`，容器使用 host 网络；WSS 地址保持 `wss://bytegallop.com/water/api/device/ws`。

本机 `/usr/bin/docker` 实际调用 Podman，已有其他业务依赖它。本项目通过 `deploy/docker.sh` 选择专用 Docker 二进制及 `/run/water-docker/docker.sock`。独立 daemon 禁用 bridge 和防火墙修改，保留现有 Podman 服务。`water-docker.service` 仅负责启动 Docker 引擎，应用由 Docker 的 `unless-stopped` 策略启动。容器根文件系统只读、无额外 capabilities，日志自动轮换；数据通过宿主目录持久化。

## 更新与运维

本地执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\water-auto-exchange\deploy\deploy.ps1
```

脚本上传到 `/apps/water-auto-exchange`。先构建新镜像，再停止旧写入进程，备份 SQLite 主文件和 WAL，保留凭据，替换容器并验证健康，最后检查及重载 Nginx。失败时恢复上一个容器镜像和网页/Nginx 配置。构建上下文只包含后端代码及 Dockerfile，不包含凭据、数据和备份。脚本针对已有站点进行迁移与重复部署。

首次安装专用 Docker 时，从 Docker 官方 HTTPS 地址下载固定版本。若服务器访问失败，可从可信本机下载同一地址的安装包，上传并解压到 `runtime/bin/` 后重试。官方安装依据：https://docs.docker.com/engine/install/binaries/ 。静态安装的版本更新需后续显式维护。

服务器上执行：

```bash
cd /apps/water-auto-exchange
bash deploy/docker.sh ps
bash deploy/docker.sh logs --tail 50 water-console
bash deploy/docker.sh restart water-console
curl -f http://127.0.0.1:8790/water/api/health
```

修改 `config/water.env` 后，重新运行 `bash deploy/install.sh` 重建容器以加载环境变量；单纯 restart 不会重新读取 env 文件。不要执行会输出完整容器环境变量的 inspect，也不要输出私有配置内容。

## 回滚与验证

每次部署保存 `backups/<时间戳>/data/`、Nginx 配置及已有网页；旧镜像保留在 Docker 中。回滚时先停止容器，选择该次旧镜像并沿用 install.sh 中的挂载和安全参数。仅在确认需要恢复历史数据库时恢复对应 data 快照；恢复前另备份当前数据。禁止直接覆盖运行中的 SQLite 文件。

首次迁移前的代码、数据、私有配置和 systemd 单元统一归档在 `backups/legacy-systemd/`。旧路径已迁出；恢复历史 systemd 部署需按归档单元中的路径复原相关文件。

Python 3.12 容器内 39 项测试通过，重复部署及容器重启通过。已验证容器健康、公网 WSS 101、鉴权 probe、错误密钥拒绝、Web 登录/离线/退出、手机布局与静态资源一致；凭据保留、/sms 健康通过。未发送设备命令、未刷写板端，网站健康仍不能证明实板联网和关泵。

## 历史部署记录

以下保留迁移前记录，路径和 systemd 命令不再作为现行运维入口。

# 网站入口部署

## 当前范围

公网入口为 https://bytegallop.com/water/，`/water` 使用 308 跳转到带斜杠路径并保留查询参数。
当前上线内容是 Web 控制台和 Python/SQLite API，支持设备通过 4G 独立接入。`health.json` 与 `/water/api/health` 仅说明网站/API 可用；设备在线状态来自登录后的 `/water/api/status`。完整流程见 [Web 与 4G 接入](web-console.md)。

板端源码为 0.5.1，最近历史实读板端为已 STOP 的 0.2.8。本次部署不刷写板端、不改变 GPIO 配置。

## 环境与路径

| 项目 | 当前配置 |
| --- | --- |
| 本地仓库 | `E:\water-auto-exchange` |
| Git 远端 / 分支 | `git@github.com:princehaku/water-auto-exchange.git` / `main` |
| 服务器公网 IP | `47.97.255.190` |
| SSH | `root`，端口 `7878`，使用本机已有 SSH 密钥认证 |
| 服务器部署资料 | `/root/apps/water-auto-exchange/` |
| 网站文件 | `/var/www/water-auto-exchange/water/` |
| 现有站点配置 | `/etc/nginx/conf.d/bytegallop.conf` |
| 本项目 Nginx 片段 | `/etc/nginx/snippets/water-auto-exchange.conf` |
| 备份 | `/root/apps/water-auto-exchange/backups/<UTC时间戳>-<进程号>/` |

本机代理可能将域名解析为 `198.18.x.x` Fake-IP；部署脚本使用当前核对的公网 IP。默认 22 端口会关闭连接，应使用 7878。IP、端口发生变化时，通过脚本参数更新。私钥、密码、Token、证书私钥和设备身份数据均不得加入项目或日志。

Nginx 在现有 HTTPS server 内 include 本项目片段，沿用站点 TLS 证书。`location ^~ /water/` 避免站点通用静态资源正则抢先匹配；未找到文件返回 404，避免返回主站 HTML。所有资源地址应使用相对路径或 `/water/` 前缀。
页面由 Nginx 直接提供；API 由 `water-console.service` 运行于 127.0.0.1:8790，不对外开放该端口，无新增容器。`/sms/` 继续使用现有 8787 服务。

## 更新与验证

在 Windows PowerShell 中：

```powershell
Set-Location E:\water-auto-exchange
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\deploy.ps1
curl.exe -I https://bytegallop.com/water
curl.exe -f https://bytegallop.com/water/health.json
curl.exe -f https://bytegallop.com/sms/api/health
```

`deploy/deploy.ps1` 打包网站、后端代码、systemd 单元、Nginx 片段及部署资料，上传后执行 `deploy/install.sh`。安装脚本先备份当前配置、页面、后端代码与服务单元；已有数据库另存 SQL 快照，然后校验唯一插入位置，执行 `nginx -t` 后平滑 reload；安装失败会尝试还原本次文件并重新校验、reload。重复执行不会重复插入 include。此脚本针对当前站点配置，不适用于任意 Nginx 站点。

完成验证后，按用户持续要求用中文标准提交信息 commit 并 push；不提交 `build/`、日志、工具包和凭据。

## 回滚

在服务器选择对应备份目录，先检查其内容：

```bash
ls -lt /root/apps/water-auto-exchange/backups/
```

备份中的 `bytegallop.conf` 为更新前站点配置；若同时含 `nginx-water.conf`、`index.html`、`health.json`，应一起恢复到上表对应位置，再执行 `nginx -t && systemctl reload nginx`。首次安装的备份没有旧的项目片段和页面，恢复原站点配置即可撤销入口；可在核对路径后清理新项目文件。

站点配置若已被其他任务更新，先比较差异并仅撤销 water include，避免用旧备份覆盖后续变更。

## 验收记录

2026-09-06：已上线。公网验证 `/water?test=route` 返回 308 并保留查询参数；`/water/` 与 `/water/health.json` 返回 200，响应内容与仓库文件逐字节一致；不存在的 `/water/missing.js` 返回 404。主站返回 200，`/sms/api/health` 为 healthy；Nginx 配置检查通过、服务 active。浏览器确认标题和项目入口说明正常。

首次上线备份：`/root/apps/water-auto-exchange/backups/20260906T155828Z-1444166/`。


## 1.0 控制台服务配置（2026-09-07）

- 应用：`/opt/water-console/app.py`；非特权用户 `waterconsole`；systemd 单元 `/etc/systemd/system/water-console.service`。
- 数据：`/var/lib/water-console/water.db`（SQLite WAL）；设备当前在线状态仅保存在内存，服务重启后等待新上报。排队/已交付命令在重启时标为结果待核实，不重放。
- 凭据：`/etc/water-console.env`，root 600。首次部署随机生成两个独立密钥；后续部署保留。用环境变量 WATER_ORIGIN 设置网页登录来源（当前 https://bytegallop.com）。更换密钥后重启服务并更新设备私有包；不要输出密钥到日志。
- 服务运维：`systemctl status water-console`、`systemctl restart water-console`。健康：`curl -f http://127.0.0.1:8790/water/api/health`。进程用户只可写自己的数据目录。
- 备份包含 `web/`、`app.py`、`water-console.service`、`bytegallop.conf`、`nginx-water.conf`，已有数据库额外包含 `water-db.sql`；首次从静态站升级可能没有旧后端文件。
- 回滚时恢复上述已存在文件，再 `systemctl daemon-reload`、重启原服务、`nginx -t`、reload。首次升级回滚应停用新服务并恢复原静态站。SQL 数据快照仅用于必要的数据恢复，常规代码回滚保留当前操作记录。原先静态站备份格式仍按历史回滚说明处理。
- 部署失败会自动尝试恢复页面、后端代码、systemd 配置、站点配置；首次新建凭据和数据库会保留，便于重试。

上线后检查 `/water` 308、四个静态资源、API health 200、未登录 status 401、随机路径 404，并回归主站和 `/sms/api/health`。真实设备未接入时应显示离线，禁止使用模拟状态污染线上设备记录。

## 控制台上线验收

2026-09-07（北京时间）：已部署并实测公网 HTTPS 登录/退出、设备离线与控制禁用、空操作记录及桌面/手机布局；四个静态文件与本地逐字节一致。API health 200、匿名 status 401、缺失资源 404、/water 308 保留查询参数，主站及 /sms 健康正常，Nginx 检查通过、water-console 服务 active。未向线上设备发送测试命令。部署备份：`/root/apps/water-auto-exchange/backups/20260906T182029Z-1468822/`。


## WSS 升级（2026-09-07）

新增 `/water/api/device/ws` 精确路由，Nginx 转发 HTTP/1.1 Upgrade，关闭缓冲，读超时 90 秒。API 版本 1.1.0，设备主连接为 WSS；旧 HTTP 接口仅用于手动 USB 调试网关兼容。

新增 `/opt/water-console/ws_endpoint.py`；固定依赖见 `server/requirements.txt`，安装到独立 `/opt/water-console/vendor`，通过 systemd PYTHONPATH 加载。宿主 Python 3.6 对应 wsproto 1.0.0 / h11 0.12.0 / dataclasses 0.8，安装使用 HTTPS 软件源，不改变 /sms 的依赖。

部署备份和回滚现在包含 ws_endpoint.py。公网验证 101 升级、已鉴权 probe 和错误密钥拒绝；probe 不登记设备、不添加命令。管理登录密码配置在升级中保留。

WSS 最终部署备份：`/root/apps/water-auto-exchange/backups/20260906T185244Z-1474880/`；重复部署保留凭据、依赖和数据，部署后再次验证公网鉴权 probe 及 /sms 健康。
