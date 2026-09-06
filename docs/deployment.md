# 网站入口部署

## 当前范围

公网入口为 https://bytegallop.com/water/，`/water` 使用 308 跳转到带斜杠路径并保留查询参数。
当前上线内容是 `deploy/www/` 的独立静态项目入口；仓库尚无 Web 后端、网络设备 API 或远程泵控制。`health.json` 仅说明网站入口可用，不能用来判断设备在线或实际换水状态。

板端源码仍为 0.3.0，最近历史实读板端为已 STOP 的 0.2.8。本次部署不刷写板端、不改变 GPIO 配置。

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
当前静态入口由现有 Nginx 直接提供，无新增端口或容器。`/sms/` 继续使用现有 8787 服务。

## 更新与验证

在 Windows PowerShell 中：

```powershell
Set-Location E:\water-auto-exchange
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy\deploy.ps1
curl.exe -I https://bytegallop.com/water
curl.exe -f https://bytegallop.com/water/health.json
curl.exe -f https://bytegallop.com/sms/api/health
```

`deploy/deploy.ps1` 打包网站、Nginx 片段及部署资料，上传后执行 `deploy/install.sh`。安装脚本先备份当前配置和页面，校验唯一插入位置，执行 `nginx -t` 后平滑 reload；安装失败会尝试还原本次文件并重新校验、reload。重复执行不会重复插入 include。此脚本针对当前站点配置，不适用于任意 Nginx 站点。

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
