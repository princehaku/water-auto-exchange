-- Build tools replace this file only inside ignored build/firmware/.
-- No tokens belong in the tracked source tree.
return {
    enabled = false,
    url = "https://bytegallop.com/water",
    device_key = "",
    ca_cert = "water-ca.crt",
    poll_ms = 2000,
    request_timeout_ms = 5000,
    offline_stop_ms = 10000
}
