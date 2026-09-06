-- LuatOS-Air V2.4.4 HTTPS polling. Timers and callbacks never wait/yield.
local M = {}
local ACTIVE = { DRAINING = true, SETTLING = true, FILLING = true }
local ALLOWED = { START = "start", FILL = "fill", STOP = "stop", RESET = "reset" }

function M.start(controller, config, deps)
    if type(config) ~= "table" or config.enabled ~= true then return false, "network_disabled" end
    if type(config.url) ~= "string" or not config.url:match("^https://[%w%.%-]+/[%w/_%-]+$")
        or type(config.device_key) ~= "string" or #config.device_key < 32
        or not config.device_key:match("^[%w_-]+$")
        or type(config.ca_cert) ~= "string" or not config.ca_cert:match("^[%w._-]+$") then
        return false, "network_config_invalid"
    end
    for _, key in ipairs({"poll_ms", "request_timeout_ms", "offline_stop_ms"}) do
        if type(config[key]) ~= "number" or config[key] % 1 ~= 0 or config[key] < 500 or config[key] > 30000 then
            return false, "network_timing_invalid"
        end
    end
    if config.offline_stop_ms > 10000 or config.request_timeout_ms >= config.offline_stop_ms then
        return false, "network_deadline_invalid"
    end
    deps = deps or {}
    local sys = deps.sys or require "sys"
    local http = deps.http or require "http"
    local json = deps.json or _G.json
    assert(type(json) == "table" and type(json.encode) == "function" and type(json.decode) == "function", "json_unavailable")
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
    local session, inflight, ack, last_command
    local enabled, owned, epoch, next_poll, last_ok = true, false, 0, 0, nil
    local raw_stop = controller.stop
    local function stop_outputs()
        local ok, stopped, reason = pcall(raw_stop)
        if not ok or stopped ~= true then
            print("WATER NET stop_unconfirmed", tostring(ok and reason or "exception"))
        end
    end
    local function lost(reason)
        if owned then stop_outputs() end
        owned, last_ok, session = false, nil, nil
        print("WATER NET", reason)
    end
    -- USB STOP cancels an in-flight remote response and the previous session.
    controller.stop = function()
        epoch = epoch + 1
        session, last_ok, owned = nil, nil, false
        return raw_stop()
    end
    local function telemetry()
        local fields = {}
        for key, value in usb.format_status(controller.status()):gmatch("([%w_]+)=([^%s]+)") do
            fields[key] = value
        end
        return fields
    end
    local function process_response(request, result, code, body)
        if inflight ~= request then return end
        inflight = nil
        if not enabled or request.epoch ~= epoch then return end
        local time = now()
        if request.expired or time - request.started >= config.request_timeout_ms then
            lost("response_expired")
            return
        end
        if result ~= true or tostring(code) ~= "200" or type(body) ~= "string" or #body > 8192 then
            lost("request_failed")
            return
        end
        local decoded = json.decode(body)
        if type(decoded) ~= "table" then lost("response_invalid"); return end
        if request.hello then
            if type(decoded.gateway) ~= "string" or #decoded.gateway ~= 32 or not decoded.gateway:match("^[a-f0-9]+$") then
                lost("session_invalid"); return
            end
            session = decoded.gateway
            last_command = nil
            return
        end
        ack, last_ok = nil, time
        if type(decoded.command) ~= "table" then return end
        local item, ttl = decoded.command, decoded.ttl_ms
        if type(item.id) ~= "string" or #item.id ~= 32 or not item.id:match("^[a-f0-9]+$") then
            lost("command_invalid"); return
        end
        if item.id == last_command then return end
        last_command = item.id
        ack = {id = item.id, status = "rejected", result = "invalid_or_expired_command"}
        if not ALLOWED[item.command] or type(ttl) ~= "number" or ttl ~= ttl
            or ttl <= time - request.started or ttl > 8000 then return end
        -- Calls are synchronous and use the same controller as USB.
        local action = item.command == "STOP" and raw_stop or controller[ALLOWED[item.command]]
        local called, ok, reason = pcall(action)
        if item.command == "STOP" then owned = false end
        if not called then
            ack.status, ack.result = "uncertain", "controller_exception_no_retry"
            stop_outputs()
            owned = false
        else
            ack.status = ok == true and "succeeded" or "rejected"
            ack.result = (ok == true and "OK " or "ERROR ") .. item.command .. " " .. tostring(reason):sub(1, 160)
            if ok == true and (item.command == "START" or item.command == "FILL") then owned = true end
        end
    end
    local function step()
        if not enabled then return end
        local time = now()
        if owned then
            if not ACTIVE[controller.status().state] then owned = false
            elseif not last_ok or time - last_ok >= config.offline_stop_ms then lost("offline_stop") end
        end
        if inflight then
            if time - inflight.started >= config.request_timeout_ms and not inflight.expired then
                inflight.expired = true
                lost("request_deadline")
            end
            -- Keep a single HTTP request until its callback releases the socket.
            return
        end
        if time < next_poll then return end
        next_poll = time + config.poll_ms
        local request = {started = time, epoch = epoch, hello = session == nil}
        local body = request.hello and {protocol = "water-v1"} or {gateway = session, status = telemetry(), ack = ack}
        local encoded = json.encode(body)
        assert(type(encoded) == "string", "network_encode_failed")
        inflight = request
        http.request("POST", config.url .. (request.hello and "/api/device/hello" or "/api/device/poll"),
            {caCert = config.ca_cert, hostNameFlag = 1},
            {["Content-Type"] = "application/json", Authorization = "Bearer " .. config.device_key},
            encoded, config.request_timeout_ms, function(result, code, _, response)
                local ok = pcall(process_response, request, result, code, response)
                if not ok then
                    if inflight == request then inflight = nil end
                    lost("callback_error")
                end
            end)
    end
    local function safe_step()
        local ok = pcall(step)
        if not ok then
            enabled = false
            epoch = epoch + 1
            lost("network_internal_error")
        end
    end
    local ok, timer = pcall(sys.timerLoopStart, safe_step, 500)
    if not ok or not timer or timer == 0 then
        enabled = false
        controller.stop = raw_stop
        return false, "network_timer_failed"
    end
    safe_step()
    return true, "network_started"
end

return M
