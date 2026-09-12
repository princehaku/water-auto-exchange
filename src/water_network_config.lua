-- build-firmware.py enables WSS only in the ignored firmware package.
return {
    enabled = false,
    url = "wss://bytegallop.com/water/api/device/ws",
    device_key = "",
    -- Same optional certificate table as sms-forward's WSS client.
    -- nil uses the socket library defaults: TLS without server verification/SNI.
    -- To verify the server, set {caCert="water-ca.crt", hostNameFlag=1, insist=0}.
    long_connection_cert = nil,
    -- Initial DNS/TCP/TLS connection budget; independent of active-output watchdog.
    tls_connect_timeout_ms = 60000,
    heartbeat_ms = 1000,
    active_heartbeat_ms = 1000,
    offline_stop_ms = 10000,
    idle_timeout_ms = 75000
}
