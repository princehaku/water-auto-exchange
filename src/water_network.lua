-- WSS application protocol; socket I/O is queued to water_ws_transport's task.
local M = {}
local ACTIVE = { DRAINING = true, SETTLING = true, FILLING = true }
local ALLOWED = { START = "start", FILL = "fill", DRAIN = "drain", STOP = "stop", RESET = "reset" }

function M.start(controller, config, deps)
    if type(config) ~= "table" or config.enabled ~= true then return false, "network_disabled" end
    if type(config.url) ~= "string" or not config.url:match("^wss://[%w%.%-]+/[%w/_%-]+$")
        or type(config.device_key) ~= "string" or #config.device_key < 32
        or not config.device_key:match("^[%w_-]+$") then
        return false, "network_config_invalid"
    end
    local cert_options = config.long_connection_cert
    if cert_options ~= nil then
        if type(cert_options) ~= "table" then return false, "network_cert_config_invalid" end
        for _, field in ipairs({"caCert", "clientCert", "clientKey"}) do
            local name = cert_options[field]
            if name ~= nil and (type(name) ~= "string" or not name:match("^[%w._-]+$")) then
                return false, "network_cert_config_invalid"
            end
        end
        for _, field in ipairs({"insist", "hostNameFlag"}) do
            if cert_options[field] ~= nil and cert_options[field] ~= 0 and cert_options[field] ~= 1 then
                return false, "network_cert_config_invalid"
            end
        end
        if cert_options.clientPassword ~= nil and type(cert_options.clientPassword) ~= "string" then
            return false, "network_cert_config_invalid"
        end
    end
    local connect_ms = config.tls_connect_timeout_ms
    if connect_ms ~= nil and (type(connect_ms) ~= "number" or connect_ms % 1000 ~= 0
        or connect_ms < 15000 or connect_ms > 120000) then
        return false, "network_connect_timeout_invalid"
    end
    local send_ms = config.send_timeout_ms
    if send_ms ~= nil and (type(send_ms) ~= "number" or send_ms % 1000 ~= 0
        or send_ms < 5000 or send_ms > 60000) then
        return false, "network_send_timeout_invalid"
    end
    local auth_ms = config.auth_timeout_ms == nil and 30000 or config.auth_timeout_ms
    if type(auth_ms) ~= "number" or auth_ms % 1000 ~= 0 or auth_ms < 5000 or auth_ms > 60000 then
        return false, "network_auth_timeout_invalid"
    end
    for _, key in ipairs({"heartbeat_ms", "active_heartbeat_ms", "offline_stop_ms", "idle_timeout_ms"}) do
        if type(config[key]) ~= "number" or config[key] % 1 ~= 0 or config[key] < 500 or config[key] > 75000 then
            return false, "network_timing_invalid"
        end
    end
    if config.offline_stop_ms > 10000 or config.active_heartbeat_ms >= config.offline_stop_ms
        or config.heartbeat_ms >= config.idle_timeout_ms or config.heartbeat_ms > 30000 then
        return false, "network_deadline_invalid"
    end
    deps = deps or {}
    local sys = deps.sys or require "sys"
    local transport = deps.transport or require "water_ws_transport"
    local json = deps.json or _G.json
    local usb = deps.usb or require "water_usb"
    local tick = deps.tick or rtos.tick
    local read_cert = deps.read_cert or function(name)
        local file = io.open("/lua/" .. name, "rb")
        if not file then return nil end
        local value = file:read("*a")
        file:close()
        return value
    end
    if cert_options and cert_options.caCert then
        local cert_ok, cert = pcall(read_cert, cert_options.caCert)
        if not cert_ok or type(cert) ~= "string" or not cert:find("-----BEGIN CERTIFICATE-----", 1, true) then
            return false, "network_ca_missing"
        end
    end
    local previous, elapsed = nil, 0
    local function now()
        local raw = tick()
        assert(type(raw) == "number" and raw == raw and math.abs(raw) < math.huge, "network_clock_invalid")
        local current = raw % 4294967296
        if previous ~= nil then
            local delta = (current - previous) % 4294967296
            assert(delta < 2147483648, "network_clock_discontinuity")
            -- Air724UG ticks are 5 ms, not the legacy 2G library timebase.
            elapsed = elapsed + delta * 5
        end
        previous = current
        return elapsed
    end
    local client, ready, opened, owned, enabled = nil, false, false, false, true
    local last_ok, last_ping, ping_seq, pong_seq, signature = 0, 0, 0, 0, nil
    local pings, report_steps = {}, 0
    local meter_id, traffic_total, traffic_seq, traffic_sent = nil, 0, 0, 0
    local previous_session
    local traffic_bytes, traffic_seconds, traffic_at = 0, 60, now()
    local pending, seen, seen_count = {}, {}, 0
    local raw_stop = controller.stop
    local function stop_outputs()
        local ok, stopped, reason = pcall(raw_stop)
        if not ok or stopped ~= true then print("WATER WS stop_unconfirmed", tostring(ok and reason or "exception")) end
    end
    local function lost(reason)
        if owned then stop_outputs() end
        ready, opened, owned, pending, pings = false, false, false, {}, {}
        if client then client:close(not enabled) end
        print("WATER WS", reason)
    end
    controller.stop = function()
        ready, opened, owned, pending, pings = false, false, false, {}, {}
        if client then client:close() end
        return raw_stop()
    end
    local function telemetry()
        local fields, text = {}, usb.format_status(controller.status())
        for key, value in text:gmatch("([%w_]+)=([^%s]+)") do fields[key] = value end
        return fields, text
    end
    local function send(value)
        local encoded = json.encode(value)
        assert(type(encoded) == "string", "encode_failed")
        if client:send(encoded, value.type) ~= true then lost("send_failed"); return false end
        return true
    end
    local function message(body)
        if not enabled or not opened then return end
        if type(body) ~= "string" or #body > 8192 then lost("invalid_message"); return end
        local value = json.decode(body)
        if type(value) ~= "table" then lost("invalid_json"); return end
        local time = now()
        if not ready then
            if value.type ~= "ready" or type(value.session) ~= "string" or #value.session ~= 32 then lost("auth_failed"); return end
            ready, last_ok, last_ping = true, time, time
            -- Keep one server-issued meter id across reconnects, until reboot.
            meter_id = meter_id or value.session
            previous_session = value.session
            print("WATER WS online")
            return
        end
        if value.type == "pong" then
            local sent_at = pings[value.seq]
            local active = ACTIVE[telemetry().state] == true
            local limit = active and config.offline_stop_ms or config.idle_timeout_ms
            -- A cellular reply can arrive after the next ping was queued.
            -- Accept fresh outstanding replies once, never an old-session pong.
            if sent_at and value.seq > pong_seq and time - sent_at < limit then
                last_ok, pong_seq = time, value.seq
                for seq in pairs(pings) do if seq <= pong_seq then pings[seq] = nil end end
            end
        elseif value.type == "received" then last_ok = time
        elseif value.type == "offer" then
            if type(value.id) ~= "string" or #value.id ~= 32 or not value.id:match("^[a-f0-9]+$") or not ALLOWED[value.command] then
                lost("invalid_offer"); return
            end
            if seen[value.id] or pending[value.id] then return end
            if value.command == "STOP" then pending = {} end
            pending[value.id] = {started = time, command = value.command}
            send({type = "claim", id = value.id})
        elseif value.type == "expired" then pending[value.id] = nil
        elseif value.type == "execute" then
            local item = pending[value.id]
            if not item or seen[value.id] then return end
            pending[value.id], seen[value.id], seen_count = nil, true, seen_count + 1
            local ack = {id = value.id, status = "rejected", result = "invalid_or_expired_command"}
            if value.command == item.command and type(value.ttl_ms) == "number"
                and value.ttl_ms > time - item.started and value.ttl_ms <= 8000 and time - item.started < 5000 then
                local action = value.command == "STOP" and raw_stop or controller[ALLOWED[value.command]]
                local called, ok, reason = pcall(action)
                if value.command == "STOP" then owned = false end
                if not called then
                    ack.status, ack.result = "uncertain", "controller_exception_no_retry"
                    stop_outputs(); owned = false
                else
                    ack.status = ok == true and "succeeded" or "rejected"
                    ack.result = (ok == true and "OK " or "ERROR ") .. value.command .. " " .. tostring(reason):sub(1,160)
                    if ok == true and (value.command == "START" or value.command == "FILL" or value.command == "DRAIN") then owned = true end
                end
            end
            last_ok = time
            local status, text = telemetry()
            if send({type = "ack", ack = ack, status = status}) then signature = text end
        else lost("invalid_type") end
    end
    client = transport.new(config, {
        open = function()
            if not enabled then client:close(); return end
            ready, opened, pending, seen, seen_count, pings = false, true, {}, {}, 0, {}
            traffic_sent = 0
            pong_seq = ping_seq
            last_ok, last_ping = now(), now()
            local status, text = telemetry()
            signature = text
            -- Fixed prefix keeps credentials outside socket4G's 30-byte debug preview.
            local auth = '{"type":"auth","protocol":"water-ws-v1","key":' .. json.encode(config.device_key)
                .. ',"status":' .. json.encode(status)
                .. (previous_session and ',"previous_session":' .. json.encode(previous_session) or '') .. '}'
            if client:send(auth, "auth") ~= true then lost("auth_send_failed") end
        end,
        message = function(body)
            local ok = pcall(message, body)
            if not ok then lost("callback_error") end
        end,
        close = function() lost("disconnected") end
    })
    local function step()
        if not enabled then return end
        if client.failed and client:failed() then enabled = false; lost("transport_task_failed"); return end
        if not opened then return end
        local time = now()
        local status, text = telemetry()
        local active = ACTIVE[status.state] == true
        if owned and not active then owned = false end
        local limit = not ready and auth_ms or (active and config.offline_stop_ms or config.idle_timeout_ms)
        if time - last_ok >= limit then lost(not ready and "auth_timeout" or "heartbeat_timeout"); return end
        if not ready then return end
        if traffic_seq > traffic_sent then
            if send({type="traffic", meter=meter_id, total_bytes=traffic_total,
                interval_bytes=traffic_bytes, interval_seconds=traffic_seconds}) then traffic_sent=traffic_seq end
            if not ready then return end
        end
        if text ~= signature then if send({type = "status", status = status}) then signature = text end end
        local interval = active and config.active_heartbeat_ms or config.heartbeat_ms
        if time - last_ping >= interval then
            ping_seq, last_ping = ping_seq + 1, time
            pings[ping_seq] = time
            send({type = "ping", seq = ping_seq})
        end
        for seq, sent_at in pairs(pings) do
            if time - sent_at >= limit then pings[seq] = nil end
        end
        report_steps = report_steps + 1
        if report_steps >= 10 then
            report_steps = 0
            print("WATER WS heartbeat seq=" .. ping_seq .. " ack=" .. pong_seq
                .. " age_ms=" .. math.floor(time - last_ok)
                .. (client.stats and client:stats() or ""))
        end
        for id, item in pairs(pending) do if time - item.started >= 5000 then pending[id] = nil end end
        if seen_count >= 2048 and not active then lost("session_refresh") end
    end
    local function safe_step()
        local ok = pcall(step)
        if not ok then enabled = false; lost("network_internal_error") end
    end
    local ok, timer = pcall(sys.timerLoopStart, safe_step, 500)
    if not ok or not timer or timer == 0 then
        enabled = false; controller.stop = raw_stop
        return false, "network_timer_failed"
    end
    if config.traffic_enabled == true then
        local accounting_ok = pcall(function()
            local accounting = deps.socket or require "socket"
            assert(type(accounting.setIpStatis) == "function")
            sys.subscribe("LIB_IP_STATIS_RPT", function(bytes)
                if type(bytes) ~= "number" or bytes % 1 ~= 0 or bytes < 0
                    or traffic_total + bytes > 9007199254740991 then return end
                local clock_ok, time = pcall(now)
                if not clock_ok then return end
                traffic_total, traffic_bytes = traffic_total + bytes, bytes
                traffic_seconds = math.max(1, math.floor((time - traffic_at) / 1000))
                traffic_at, traffic_seq = time, traffic_seq + 1
            end)
            accounting.setIpStatis(60)
        end)
        print("WATER NET traffic=" .. (accounting_ok and "estimated interval_s=60" or "unavailable"))
    end
    client:start()
    return true, "network_started"
end
return M
