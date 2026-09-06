local root = (arg and arg[1]) or "."
package.path = root .. "/src/?.lua;" .. package.path
local network = require "water_network"
local count = 0
local function test(name, fn)
    fn()
    count = count + 1
    print("PASS " .. name)
end
local function fixture()
    local f = {tick = 0, requests = {}, calls = {}, decoded = {}, state = "IDLE"}
    f.config = {enabled = true, url = "https://example.test/water", device_key = string.rep("k", 32),
        ca_cert = "water-ca.crt", poll_ms = 2000, request_timeout_ms = 5000, offline_stop_ms = 10000}
    f.controller = {
        status = function() return {state = f.state} end,
        start = function() f.calls[#f.calls+1] = "START"; f.state = "DRAINING"; return true, "started" end,
        fill = function() f.calls[#f.calls+1] = "FILL"; f.state = "FILLING"; return true, "fill_started" end,
        stop = function() f.calls[#f.calls+1] = "STOP"; f.state = "IDLE"; return true, "stopped" end,
        reset = function() f.calls[#f.calls+1] = "RESET"; return false, "not_faulted" end
    }
    f.deps = {
        tick = function() return f.tick end,
        sys = {timerLoopStart = function(fn) f.step = fn; return f.timer_fail and nil or 1 end},
        json = {encode = function(value) f.payload = value; return "{}" end,
            decode = function() if f.decode_fail then error("invalid_json") end; return f.decoded end},
        usb = {format_status = function() return "project=water_auto_exchange version=0.4.0 state=IDLE" end},
        read_cert = function() return f.no_cert and "" or "-----BEGIN CERTIFICATE-----" end,
        http = {request = function(method, url, cert, head, body, timeout, callback)
            assert(method == "POST" and cert.caCert == "water-ca.crt" and cert.hostNameFlag == 1)
            assert(head.Authorization == "Bearer " .. f.config.device_key)
            f.requests[#f.requests+1] = {url = url, cb = callback, payload = f.payload}
        end}
    }
    function f.start() return network.start(f.controller, f.config, f.deps) end
    function f.advance(ms) f.tick = (f.tick + ms * 16) % 4294967296; f.step() end
    function f.reply(value, code, result, index)
        f.decoded = value
        f.requests[index or #f.requests].cb(result == nil and true or result, code or "200", {}, "{}")
    end
    function f.connect()
        assert(f.start())
        assert(f.requests[1].url:find("/hello", 1, true))
        f.reply({gateway = string.rep("a", 32)})
        f.advance(2000)
    end
    function f.command(command, ttl, id)
        f.reply({command = {command = command, id = id or string.rep("b",32)}, ttl_ms = ttl or 8000})
    end
    return f
end

test("disabled network has no I/O", function()
    assert(network.start({}, {enabled=false}) == false)
end)
test("plaintext URL and empty key rejected", function()
    local f = fixture(); f.config.url = "http://example.test/water"; assert(not f.start()); assert(#f.requests == 0)
    f = fixture(); f.config.device_key = ""; assert(not f.start())
end)
test("missing CA prevents network startup", function()
    local f = fixture(); f.no_cert = true; assert(not f.start()); assert(#f.requests == 0)
end)
test("handshake then authenticated telemetry", function()
    local f = fixture(); f.connect(); assert(f.requests[2].payload.gateway == string.rep("a",32))
    assert(f.requests[2].payload.status.version == "0.4.0"); assert(#f.calls == 0)
end)
test("remote start and acknowledgment", function()
    local f = fixture(); f.connect(); f.command("START"); assert(f.calls[1] == "START")
    f.advance(2000); assert(f.requests[3].payload.ack.status == "succeeded")
end)
test("remote STOP preserves session for prompt ack", function()
    local f = fixture(); f.connect(); f.command("STOP"); f.advance(2000)
    assert(f.requests[3].url:find("/poll",1,true)); assert(f.requests[3].payload.ack.result == "OK STOP stopped")
end)
test("duplicate callback cannot start twice", function()
    local f = fixture(); f.connect(); f.command("START"); f.command("START"); assert(#f.calls == 1)
end)
test("duplicate command ID is not replayed", function()
    local f = fixture(); f.connect(); f.command("START"); f.advance(2000); f.command("START"); assert(#f.calls == 1)
end)
test("expired command does not change output", function()
    local f = fixture(); f.connect(); f.advance(1000); f.command("START", 500); assert(#f.calls == 0)
    f.advance(1000); assert(f.requests[3].payload.ack.status == "rejected")
end)
test("unknown command does not change output", function()
    local f = fixture(); f.connect(); f.command("PROBE LOOP"); assert(#f.calls == 0)
end)
test("USB STOP invalidates pending remote response", function()
    local f = fixture(); f.connect(); f.controller.stop(); f.command("START")
    assert(#f.calls == 1 and f.calls[1] == "STOP")
    f.advance(2000); assert(f.requests[3].url:find("/hello",1,true))
end)
test("failed request stops remotely owned cycle", function()
    local f = fixture(); f.connect(); f.command("START"); f.advance(2000); f.reply({}, "503")
    assert(f.calls[2] == "STOP")
end)
test("deadline stops cycle before late callback", function()
    local f = fixture(); f.connect(); f.command("START"); f.advance(2000); f.advance(5000)
    assert(f.calls[2] == "STOP"); f.command("FILL"); assert(#f.calls == 2)
end)
test("stuck request never creates overlapping HTTP tasks", function()
    local f = fixture(); assert(f.start()); f.advance(30000); f.advance(30000); assert(#f.requests == 1)
end)
test("invalid JSON stops remote cycle", function()
    local f = fixture(); f.connect(); f.command("START"); f.advance(2000); f.decode_fail = true; f.reply({})
    assert(f.calls[2] == "STOP")
end)
test("controller failure stops and reports uncertainty", function()
    local f = fixture(); f.controller.start = function() error("write failed") end
    f.connect(); f.command("START"); assert(f.calls[1] == "STOP")
    f.advance(2000); assert(f.requests[3].payload.ack.status == "uncertain")
end)
test("clock rollover allows subsequent polling", function()
    local f = fixture(); f.tick = 4294960000; f.connect(); f.reply({}); f.advance(2000); assert(#f.requests == 3)
end)
test("clock discontinuity stops and disables networking", function()
    local f = fixture(); f.connect(); f.command("START"); f.tick = f.tick - 1; f.step()
    assert(f.calls[2] == "STOP"); f.advance(2000); assert(#f.requests == 2)
end)
test("local standalone cycle is unaffected by remote outage", function()
    local f = fixture(); f.connect(); f.controller.start(); f.reply({},"503"); assert(#f.calls == 1)
end)
test("invalid timer prevents networking", function()
    local f = fixture(); f.deps.sys.timerLoopStart = function() return nil end
    assert(not f.start()); assert(#f.requests == 0)
end)
print("water_network: " .. count .. " tests passed")
