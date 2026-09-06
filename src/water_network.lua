-- WSS application protocol; socket I/O is queued to water_ws_transport's task.
local M = {}
local ACTIVE = { DRAINING = true, SETTLING = true, FILLING = true }
local ALLOWED = { START = "start", FILL = "fill", STOP = "stop", RESET = "reset" }

function M.start(controller, config, deps)
    if type(config) ~= "table" or config.enabled ~= true then return false, "network_disabled" end
    if type(config.url) ~= "string" or not config.url:match("^wss://[%w%.%-]+/[%w/_%-]+$")
        or type(config.device_key) ~= "string" or #config.device_key < 32
        or not config.device_key:match("^[%w_-]+$")
        or type(config.ca_cert) ~= "string" or not config.ca_cert:match("^[%w._-]+$") then
        return false, "network_config_invalid"
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
    local cert_ok, cert = pcall(read_cert, config.ca_cert)
    if not cert_ok or type(cert) ~= "string" or not cert:find("-----BEGIN CERTIFICATE-----", 1, true) then
        return false, "network_ca_missing"
    end
    local previous, elapsed = nil, 0
    local function now()
        local raw = tick()
        assert(type(raw) == "number" and raw == raw and math.abs(raw) < math.huge, "network_clock_invalid")
        local current = raw % 4294967296
        if previous ~= nil then
            local delta = (current - previous) % 4294967296
            assert(delta < 2147483648, "network_clock_discontinuity")
            elapsed = elapsed + delta / 16
        end
        previous = current
        return elapsed
    end
    local client, ready, opened, owned, enabled = nil, false, false, false, true
    local last_ok, last_ping, ping_seq, signature = 0, 0, 0, nil
    local pending, seen, seen_count = {}, {}, 0
    local raw_stop = controller.stop
    local function stop_outputs()
        local ok, stopped, reason = pcall(raw_stop)
        if not ok or stopped ~= true then print("WATER WS stop_unconfirmed", tostring(ok and reason or "exception")) end
    end
    local function lost(reason)
        if owned then stop_outputs() end
        ready, opened, owned, pending = false, false, false, {}
        if client then client:close(not enabled) end
        print("WATER WS", reason)
    end
    controller.stop = function()
        ready, opened, owned, pending = false, false, false, {}
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
        if client:send(encoded) ~= true then lost("send_failed"); return false end
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
            print("WATER WS online")
            return
        end
        if value.type == "pong" then
            if value.seq == ping_seq then last_ok = time end
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
                    if ok == true and (value.command == "START" or value.command == "FILL") then owned = true end
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
            ready, opened, pending, seen, seen_count = false, true, {}, {}, 0
            last_ok, last_ping = now(), now()
            local status, text = telemetry()
            signature = text
            -- Fixed prefix keeps credentials outside socket4G's 30-byte debug preview.
            local auth = '{"type":"auth","protocol":"water-ws-v1","key":' .. json.encode(config.device_key)
                .. ',"status":' .. json.encode(status) .. '}'
            if client:send(auth) ~= true then lost("auth_send_failed") end
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
        local limit = (not ready or active) and config.offline_stop_ms or config.idle_timeout_ms
        if time - last_ok >= limit then lost("heartbeat_timeout"); return end
        if not ready then return end
        if text ~= signature then if send({type = "status", status = status}) then signature = text end end
        local interval = active and config.active_heartbeat_ms or config.heartbeat_ms
        if time - last_ping >= interval then
            ping_seq, last_ping = ping_seq + 1, time
            send({type = "ping", seq = ping_seq})
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
    client:start()
    return true, "network_started"
end
return M
