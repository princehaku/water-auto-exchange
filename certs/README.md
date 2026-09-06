# 设备 HTTPS 信任证书

`water-ca.crt` 为公开的 AAA Certificate Services 根 CA，服务于当前 bytegallop.com 证书链：站点证书 → WoTrus DV Server CA → USERTrust RSA Certification Authority → AAA Certificate Services。

2026-09-07 通过现有 SSH 服务器执行 OpenSSL `s_client -verify_return_error -servername bytegallop.com -showcerts` 获取公开证书链，核对 `Verify return code: 0 (ok)` 后提取根 CA。证书无私钥、无设备凭据。PEM 文件 SHA-256：

`a5ddabd1602ae1c66ce11ad078e734cc473dcb8e9f573037832d8536ae3de90b`

证书及 LuaTask V2.4.4 的 `caCert` + `hostNameFlag=1` 配置需要一起打包。更换站点证书签发链时重新核对信任链，不能删除 CA 参数跳过校验。当前站点证书有效期截至 2026-12-29；续签是否更换根 CA 应以新链为准。

本地 OpenSSL 链验证通过不等于 Air724UG 的 TLS 握手已经通过，实板校验仍需用户刷写后进行。

DER 证书 SHA-256（不受 PEM 换行格式影响）：`d7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4`。
