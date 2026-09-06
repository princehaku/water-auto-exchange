-- build-firmware.py enables WSS only in the ignored firmware package.
return {
    enabled = false,
    url = "wss://bytegallop.com/water/api/device/ws",
    device_key = "",
    ca_cert = "water-ca.crt",
    heartbeat_ms = 30000,
    active_heartbeat_ms = 2000,
    offline_stop_ms = 10000,
    idle_timeout_ms = 75000
}
