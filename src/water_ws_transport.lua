-- Bounded WSS client for LuatOS-Air. All yielding socket calls run in ONE task.
local M = {}
local LIMIT = 8192

local function xor(a, b)
    local value, place = 0, 1
    for _ = 1, 8 do
        if a % 2 ~= b % 2 then value = value + place end
        a, b, place = math.floor(a / 2), math.floor(b / 2), place * 2
    end
    return value
end

function M.frame(data, opcode, mask)
    assert(type(data) == "string" and #data <= LIMIT and #mask == 4, "invalid_frame")
    local header = string.char(128 + (opcode or 1))
    if #data < 126 then header = header .. string.char(128 + #data)
    else header = header .. string.char(254, math.floor(#data / 256), #data % 256) end
    local parts = {header, mask}
    for i = 1, #data do parts[#parts + 1] = string.char(xor(data:byte(i), mask:byte((i - 1) % 4 + 1))) end
    return table.concat(parts)
end

function M.parser()
    local buffer, fragments, fragment_size, fragment_opcode = "", {}, 0, nil
    return function(chunk)
        buffer = buffer .. chunk
        local events = {}
        while #buffer >= 2 do
            local a, b = buffer:byte(1, 2)
            local fin, opcode = a >= 128, a % 16
            assert(math.floor(a / 16) % 8 == 0 and b < 128, "invalid_server_frame")
            local size, offset = b, 2
            if size == 127 then error("frame_too_large") end
            if size == 126 then
                if #buffer < 4 then break end
                size, offset = buffer:byte(3) * 256 + buffer:byte(4), 4
                assert(size >= 126, "nonminimal_length")
            end
            assert(size <= LIMIT and (opcode < 8 or (fin and size <= 125)), "frame_too_large")
            if #buffer < offset + size then break end
            local payload = buffer:sub(offset + 1, offset + size)
            buffer = buffer:sub(offset + size + 1)
            if opcode == 8 or opcode == 9 or opcode == 10 then
                assert(opcode ~= 8 or #payload ~= 1, "invalid_close")
                events[#events + 1] = {opcode = opcode, data = payload}
            else
                assert(opcode == 0 or opcode == 1 or opcode == 2, "unsupported_opcode")
                if opcode == 0 then assert(fragment_opcode, "unexpected_continuation")
                else assert(not fragment_opcode, "unfinished_message"); fragment_opcode = opcode end
                fragment_size = fragment_size + #payload
                assert(fragment_size <= LIMIT, "message_too_large")
                fragments[#fragments + 1] = payload
                if fin then
                    events[#events + 1] = {opcode = fragment_opcode, data = table.concat(fragments)}
                    fragments, fragment_size, fragment_opcode = {}, 0, nil
                end
            end
        end
        assert(#buffer <= LIMIT + 4, "buffer_too_large")
        return events
    end
end

local function unhex(value)
    return (value:gsub("..", function(pair) return string.char(tonumber(pair, 16)) end))
end

function M.new(config, callbacks, deps)
    deps = deps or {}
    local sys = deps.sys or require "sys"
    local socket = deps.socket or require "socket"
    local link = deps.link or require "link"
    local net = deps.net or require "net"
    local ril = deps.ril or require "ril"
    local crypto = deps.crypto or _G.crypto
    local client = {queue = {}, connected = false, cancelled = false}
    local event, serial = "WATER_WS_WAKE", 0
    local last_recovery, last_diagnostic, diagnostic_pending, last_tcp_probe
    local connect_ms = config.tls_connect_timeout_ms or 60000
    local function certificate_options()
        if config.long_connection_cert == nil then return nil end
        -- socket4G rewrites certificate filenames; keep config reusable on retry.
        local cert = {}
        for name, value in pairs(config.long_connection_cert) do cert[name] = value end
        return cert
    end
    local function age(tick)
        return ((rtos.tick() - tick) % 4294967296) * 5
    end
    local function diagnose()
        if diagnostic_pending or (last_diagnostic and age(last_diagnostic) < 60000) then return end
        last_diagnostic, diagnostic_pending = rtos.tick(), true
        -- Query only; preserve the library's URC handlers and operator/APN selection.
        -- The final callback bounds the queue even when the AT channel stalls.
        for _, command in ipairs({"AT+CPIN?", "AT+CREG?", "AT+CGREG?", "AT+CEREG?"}) do
            ril.request(command)
        end
        ril.request("AT+CSQ", nil, function(_, ok, _, intermediate)
            diagnostic_pending = false
            local csq = ok and type(intermediate) == "string" and intermediate:match("%+CSQ:%s*(%d+)")
            print("WATER NET diagnostic csq=" .. tostring(csq or "unknown") .. "; see CPIN/CREG/CGREG/CEREG above")
        end)
    end
    local function recover(reason)
        if client.stopped or (last_recovery and age(last_recovery) < 300000) then return false end
        if net.getState() ~= "REGISTERED" then return false end
        last_recovery = rtos.tick()
        print("WATER WS pdp_recover reason=" .. reason .. " cooldown_ms=300000")
        -- V2.4.4 shut invalidates local IP readiness and re-queries the LTE bearer.
        -- It does not force a radio restart or bypass network registration denial.
        link.shut()
        return true
    end
    local function random_bytes(length)
        serial = serial + 1
        local value = config.device_key .. ":" .. tostring(os.time()) .. ":" .. tostring(rtos.tick()) .. ":" .. serial
        return unhex(crypto.sha1(value, #value)):sub(1, length)
    end
    local function notify(name, ...)
        if callbacks[name] then
            local ok = pcall(callbacks[name], ...)
            if not ok then client.cancelled = true end
        end
    end
    function client:send(value)
        if not self.connected or self.cancelled or #self.queue >= 8 or type(value) ~= "string" or #value > LIMIT then return false end
        self.queue[#self.queue + 1] = value
        sys.publish(event)
        return true
    end
    function client:close(permanent)
        if permanent then self.stopped = true end
        self.cancelled = true
        self.queue = {}
        sys.publish(event)
    end
    local function probe_tcp(host)
        if client.stopped or not socket.isReady() or net.getState() ~= "REGISTERED"
            or (last_tcp_probe and age(last_tcp_probe) < 300000) then return end
        last_tcp_probe = rtos.tick()
        -- Same owner task, after the old TLS socket and application session close.
        -- No send/recv, credentials, application fallback or changes to TLS trust.
        local probe = socket.tcp()
        if not probe then print("WATER NET tcp_probe socket_create_failed"); return end
        local started = rtos.tick()
        print("WATER NET tcp_probe host=" .. host .. " port=443 timeout_ms=15000")
        local ok = probe:connect(host, 443, 15)
        print("WATER NET tcp_probe result=" .. (ok and "reachable" or "failed")
            .. " elapsed_ms=" .. age(started) .. " scope=dns_tcp_only")
        probe:close()
    end
    local function run_connection(io)
        local host, path = config.url:match("^wss://([%w%.%-]+)(/.*)$")
        local connect_started = rtos.tick()
        local cert = config.long_connection_cert
        print("WATER WS connecting_tls host=" .. host .. " timeout_ms=" .. connect_ms
            .. " ca=" .. (cert and cert.caCert and "enabled" or "disabled")
            .. " sni=" .. (cert and cert.hostNameFlag == 1 and "enabled" or "disabled"))
        -- socket4G connect/send use SECONDS; recv uses milliseconds. In particular,
        -- do not pass the old websocket library's millisecond value through here.
        if not io:connect(host, 443, connect_ms / 1000) then
            print("WATER WS tls_connect_failed elapsed_ms=" .. age(connect_started)
                .. " registration=" .. tostring(net.getState()) .. " pdp_ready=" .. tostring(socket.isReady()))
            return "tls_connect_failed"
        end
        if client.cancelled then return end
        print("WATER WS tls_connected elapsed_ms=" .. age(connect_started))
        print("WATER WS upgrading_http")
        local key = crypto.base64_encode(random_bytes(16), 16)
        local request = "GET " .. path .. " HTTP/1.1\r\nHost: " .. host .. "\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: " .. key .. "\r\n\r\n"
        if not io:send(request, 5) then print("WATER WS upgrade_send_failed"); return end
        local response, boundary = "", nil
        -- recv is milliseconds; connect/send use seconds in socket4G V2.4.4.
        local started = rtos.tick() % 4294967296
        repeat
            local ok, chunk = io:recv(1000, event)
            if ok then response = response .. chunk
            elseif chunk ~= "timeout" and chunk ~= event then print("WATER WS upgrade_receive_failed"); return end
            if #response > 16384 or client.cancelled then return end
            boundary = response:find("\r\n\r\n", 1, true)
            if ((rtos.tick() - started) % 4294967296) * 5 >= 10000 then print("WATER WS upgrade_timeout"); return end
        until boundary
        local headers = response:sub(1, boundary + 3)
        local accept_source = key .. "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        local expected = crypto.base64_encode(unhex(crypto.sha1(accept_source, #accept_source)), 20)
        local fields = {}
        for name, value in headers:gmatch("\r\n([^:]+):%s*([^\r\n]+)") do fields[name:lower()] = value end
        if not headers:match("^HTTP/1%.1 101 ") or fields["sec-websocket-accept"] ~= expected
            or (fields.upgrade or ""):lower() ~= "websocket"
            or not (fields.connection or ""):lower():find("upgrade",1,true) then
            print("WATER WS upgrade_rejected", headers:match("^HTTP/1%.1 (%d%d%d)") or "invalid")
            return
        end
        print("WATER WS upgraded; authenticating")
        client.connected = true
        client.connected_at = rtos.tick()
        notify("open")
        local parse = M.parser()
        local pending = response:sub(boundary + 4)
        while not client.cancelled do
            if #pending > 0 then
                local ok, events = pcall(parse, pending)
                if not ok then return end
                for _, item in ipairs(events) do
                    if item.opcode == 8 then return
                    elseif item.opcode == 9 then
                        if not io:send(M.frame(item.data, 10, random_bytes(4)), 5) then return end
                    elseif item.opcode == 1 or item.opcode == 2 then notify("message", item.data) end
                    if client.cancelled then return end
                end
            end
            while #client.queue > 0 and not client.cancelled do
                local value = table.remove(client.queue, 1)
                if not io:send(M.frame(value, 1, random_bytes(4)), 5) then return end
            end
            local ok, chunk = io:recv(1000, event)
            pending = ok and chunk or ""
            if not ok and chunk ~= "timeout" and chunk ~= event then return end
        end
    end
    function client:start()
        sys.subscribe("IP_ERROR_IND", function()
            if client.stopped then return end
            client:close()
            -- Stop a remotely owned output even if connect/recv has not returned.
            if client.connected then
                client.stable_before_loss = age(client.connected_at) >= 60000
                client.connected = false
                notify("close")
            end
        end)
        self.task = sys.taskInit(function()
            local backoff, failures = 1000, 0
            while not client.stopped do
                client.cancelled = false
                local waiting, waiting_since = 0, rtos.tick()
                while not socket.isReady() and not client.stopped do
                    if waiting % 5 == 0 then
                        print("WATER WS waiting_pdp registration=" .. tostring(net.getState()))
                        diagnose()
                    end
                    if age(waiting_since) >= 120000 and recover("pdp_wait_timeout") then
                        waiting_since = rtos.tick()
                    end
                    waiting = waiting + 1
                    sys.wait(1000)
                end
                if client.stopped then return end
                client.cancelled = false
                client.stable_before_loss = false
                local io = socket.tcp(true, certificate_options())
                local failure
                if io then
                    -- Never put these yielding operations inside pcall/xpcall.
                    failure = run_connection(io)
                else print("WATER WS socket_create_failed") end
                local stable = client.stable_before_loss or (client.connected and age(client.connected_at) >= 60000)
                local cancelled = client.cancelled
                client.connected, client.queue = false, {}
                -- Invalidate the session and stop outputs before yielding in close/recovery.
                notify("close")
                if io then io:close() end
                if client.stopped then return end
                if stable then backoff, failures = 1000, 0
                elseif not cancelled then failures = math.min(failures + 1, 6) end
                diagnose()
                if not cancelled and failures >= 3 and failure == "tls_connect_failed" then
                    probe_tcp(config.url:match("^wss://([%w%.%-]+)"))
                end
                if client.stopped then return end
                if failures >= 6 and recover("consecutive_failures") then failures = 0 end
                print("WATER WS retry_ms=" .. backoff .. " failures=" .. failures)
                sys.wait(backoff)
                backoff = math.min(backoff * 2, 60000)
            end
        end)
    end
    function client:failed()
        return type(self.task) == "thread" and coroutine.status(self.task) == "dead" and not self.stopped
    end
    return client
end

return M
