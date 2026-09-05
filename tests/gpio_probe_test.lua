-- Lua 5.1 tests with stubbed hardware; no serial port or GPIO is accessed.
local root = (arg and arg[1]) or "."
package.path = root .. "/src/?.lua;" .. package.path
local real_print = print

local function equal(actual, expected, message)
    assert(actual == expected, (message or "unexpected value")
        .. ": expected " .. tostring(expected) .. ", got " .. tostring(actual))
end

local function fixture()
    for _, name in ipairs({ "gpio_probe", "sys", "pins" }) do package.loaded[name] = nil end
    local f = {
        now = 0, events = {}, messages = {}, logs = {}, active = {}, timers = {},
        attempts = {}, faults = {}, required = 0, next_id = 0, forbidden_accesses = 0
    }
    local function event(kind, a, b)
        if kind == "setup" or kind == "set" or kind == "close" then
            equal(a, 5, "only GPIO5 may be configured, written or closed")
        end
        f.events[#f.events + 1] = { kind = kind, a = a, b = b, time = f.now }
        f.attempts[kind] = (f.attempts[kind] or 0) + 1
        local fault = f.faults[kind]
        if fault and (not fault.at or fault.at == f.attempts[kind]) then
            if fault.mode == "throw" then error("injected_" .. kind) end
            return fault.mode
        end
        return "ok"
    end
    local function forbidden()
        f.forbidden_accesses = f.forbidden_accesses + 1
        error("GPIO5 diagnostics must not access pmd or uart")
    end
    _G.pmd = setmetatable({}, { __index = forbidden, __newindex = forbidden })
    _G.uart = setmetatable({}, { __index = forbidden, __newindex = forbidden })
    _G.print = function(line) f.logs[#f.logs + 1] = line end
    _G.pio = { pin = {
        setval = function(value, pin)
            assert(value == 0 or value == 1, "only explicit 0/1 writes are allowed")
            if event("set", pin, value) == "false" then return false end
            f.active[pin] = value
            -- Native functions may return nil on success.
        end
    } }
    package.preload.pins = function()
        f.required = f.required + 1
        return {
            setup = function(pin, value)
                equal(next(f.active), nil, "the preceding output must be released before setup")
                equal(value, 0, "setup must initially configure LOW")
                f.active[pin] = value -- Model partial setup before a reported failure.
                local result = event("setup", pin, value)
                if result == "false" then return false end
                if result == "nil" then return nil end
                return function(value)
                    assert(value ~= nil, "an output closure must never be read without an argument")
                    error("writes should use pio.pin.setval so its failure is observable")
                end
            end,
            close = function(pin)
                if event("close", pin) == "false" then return false end
                f.active[pin] = nil
            end
        }
    end
    package.preload.sys = function()
        f.required = f.required + 1
        return {
            timerStart = function(callback, duration)
                equal(duration, 5000, "every phase must hold for five seconds")
                -- Register before failure to exercise partial timer-start cleanup.
                f.next_id = f.next_id + 1
                local id = f.next_id
                f.timers[id] = { callback = callback, active = true, due = f.now + duration }
                f.latest_id = id
                local result = event("timer_start", id, duration)
                if result == "false" then return false end
                if result == "nil" then return nil end
                return id
            end,
            timerStop = function(id)
                if event("timer_stop", id) == "false" then return false end
                for key, timer in pairs(f.timers) do
                    if key == id or timer.callback == id then timer.active = false end
                end
            end
        }
    end
    function f.emit(line)
        f.messages[#f.messages + 1] = { line = line, time = f.now }
        if f.emit_fault and line:find(f.emit_fault, 1, true) then error("injected_emit") end
        if f.on_emit then f.on_emit(line) end
    end
    function f.fire()
        local timer = assert(f.timers[f.latest_id], "missing timer")
        assert(timer.active, "cannot fire a canceled timer")
        timer.active = false
        f.now = timer.due
        timer.callback()
    end
    function f.finish()
        local guard = 0
        while f.probe.status().state == "RUNNING" do
            guard = guard + 1
            assert(guard <= 2, "single pass must finish after two timers")
            f.fire()
        end
    end
    function f.events_of(kind)
        local result = {}
        for _, item in ipairs(f.events) do
            if item.kind == kind then result[#result + 1] = item end
        end
        return result
    end
    f.probe = require "gpio_probe"
    return f
end

local function quiescent(f)
    equal(next(f.active), nil, "GPIO5 must be released")
    equal(f.forbidden_accesses, 0, "pmd and uart must remain untouched")
    local before = #f.events
    for _, timer in pairs(f.timers) do timer.callback() end
    equal(#f.events, before, "stale callbacks must perform no I/O")
end

local tests = {}
local function test(name, callback) tests[#tests + 1] = { name, callback } end

test("require, status and an initial STOP perform no hardware I/O", function()
    local f = fixture()
    equal(#f.events, 0)
    equal(f.required, 0)
    local status = f.probe.status()
    equal(status.state, "IDLE")
    equal(status.gpio, nil)
    equal(status.level, nil)
    equal(status.index, 0)
    equal(status.total, 1)
    equal(status.hold_ms, 5000)
    equal(status.continuous, false)
    equal(status.cycle, 0)
    equal(status.cycle_ms, 10000)
    equal(status.candidates, "5")
    equal(f.probe.start(f.emit, "true"), false)
    status.state, status.total, status.candidates = "RUNNING", 99, "other"
    equal(f.probe.status().state, "IDLE")
    equal(f.probe.status().total, 1)
    equal(f.probe.status().candidates, "5")
    assert(f.probe.stop())
    equal(#f.events, 0)
    equal(f.required, 0)
    quiescent(f)
end)

test("single pass drives GPIO5 HIGH immediately, LOW at five seconds and releases at ten", function()
    local f = fixture()
    assert(f.probe.start(f.emit))
    equal(f.probe.status().gpio, 5)
    equal(f.probe.status().level, 1)
    equal(f.active[5], 1)
    f.fire()
    equal(f.now, 5000)
    equal(f.probe.status().level, 0)
    equal(f.active[5], 0)
    f.fire()
    equal(f.now, 10000)
    equal(f.probe.status().state, "DONE")
    equal(f.probe.status().cycle, 1)
    equal(f.probe.status().gpio, nil)
    local setups, writes, closes = f.events_of("setup"), f.events_of("set"), f.events_of("close")
    equal(#setups, 1)
    equal(setups[1].b, 0)
    equal(#writes, 3)
    for i, expected in ipairs({ { 1, 0 }, { 0, 5000 }, { 0, 10000 } }) do
        equal(writes[i].a, 5)
        equal(writes[i].b, expected[1])
        equal(writes[i].time, expected[2])
    end
    equal(#closes, 1)
    equal(closes[1].time, 10000)
    equal(#f.events_of("timer_start"), 2)
    equal(f.messages[1].line, "PROBE state=RUNNING event=START total=1 hold_ms=5000 cycle_ms=10000 domain=V_GLOBAL_1V8 continuous=0")
    equal(f.messages[2].line, "PROBE gpio=5 physical=49 level=1 phase=HIGH index=1/1 hold_ms=5000 domain=V_GLOBAL_1V8")
    equal(f.messages[2].time, 0)
    equal(f.messages[3].line, "PROBE gpio=5 physical=49 level=0 phase=LOW index=1/1 hold_ms=5000 domain=V_GLOBAL_1V8")
    equal(f.messages[3].time, 5000)
    equal(f.messages[4].line, "PROBE state=DONE event=END total=1 duration_ms=10000")
    equal(#f.logs, #f.messages)
    for i, message in ipairs(f.messages) do equal(f.logs[i], message.line) end
    quiescent(f)
end)

test("continuous mode repeats only GPIO5 every ten seconds without an extra holding phase", function()
    local f = fixture()
    assert(f.probe.start(f.emit, true))
    for n = 1, 6 do
        f.fire()
        local status = f.probe.status()
        equal(f.now, n * 5000)
        equal(status.state, "RUNNING")
        equal(status.continuous, true)
        equal(status.gpio, 5)
        equal(status.cycle, math.floor(n / 2) + 1)
        equal(status.level, n % 2 == 0 and 1 or 0)
        equal(f.active[5], status.level)
        equal(f.timers[f.latest_id].due, f.now + 5000)
    end
    equal(#f.events_of("setup"), 4)
    equal(#f.events_of("close"), 3)
    assert(f.probe.stop())
    quiescent(f)
end)

test("STOP during either level writes LOW, closes GPIO5 and invalidates callbacks across restart", function()
    for _, ticks in ipairs({ 0, 1 }) do
        local f = fixture()
        assert(f.probe.start(f.emit, true))
        if ticks == 1 then f.fire() end
        local id, callback = f.latest_id, f.timers[f.latest_id].callback
        assert(f.probe.stop())
        equal(f.probe.status().state, "STOPPED")
        equal(f.timers[id].active, false)
        local writes = f.events_of("set")
        equal(writes[#writes].b, 0)
        equal(writes[#writes].time, f.now, "STOP must write LOW without waiting for a timer")
        quiescent(f)
        assert(f.probe.start(f.emit))
        equal(f.probe.status().continuous, false)
        equal(f.probe.status().cycle, 1)
        local count = #f.events
        callback()
        equal(#f.events, count, "the old loop cannot alter the new pass")
        assert(f.probe.stop())
        quiescent(f)
    end
end)

test("duplicate START does not replace the emitter or interrupt the active cycle", function()
    local f = fixture()
    assert(f.probe.start(f.emit, true))
    local count, id = #f.events, f.latest_id
    local ok, reason = f.probe.start(function() error("wrong emitter") end, false)
    equal(ok, false)
    equal(reason, "already_running")
    equal(#f.events, count)
    equal(f.latest_id, id)
    equal(f.probe.status().continuous, true)
    f.fire()
    equal(f.probe.status().level, 0)
    assert(f.probe.stop())
    quiescent(f)
end)

test("DONE and STOPPED require another explicit START before any further output", function()
    local f = fixture()
    assert(f.probe.start(f.emit))
    f.finish()
    quiescent(f)
    assert(f.probe.start(f.emit, true))
    equal(f.probe.status().cycle, 1)
    assert(f.probe.stop())
    quiescent(f)
    assert(f.probe.start(f.emit))
    equal(f.probe.status().continuous, false)
    f.finish()
    quiescent(f)
end)

test("partial setup failures attempt LOW and close even when setup returns no closure", function()
    for _, mode in ipairs({ "nil", "false", "throw" }) do
        local f = fixture()
        f.faults.setup = { mode = mode }
        equal(f.probe.start(f.emit), false)
        equal(f.probe.status().state, "ERROR")
        equal(#f.events_of("set"), 1)
        equal(f.events_of("set")[1].b, 0)
        equal(#f.events_of("close"), 1)
        equal(#f.events_of("timer_start"), 0)
        quiescent(f)
        equal(f.probe.start(f.emit), false, "ERROR requires a successful STOP")
        f.faults = {}
        assert(f.probe.stop())
    end
end)

test("setval failures during HIGH, LOW or release stop the pass and clean up", function()
    for _, mode in ipairs({ "false", "throw" }) do
        for _, at in ipairs({ 1, 2, 3 }) do
            local f = fixture()
            f.faults.set = { at = at, mode = mode }
            local ok = f.probe.start(f.emit)
            if at == 1 then equal(ok, false)
            else assert(ok); f.finish() end
            equal(f.probe.status().state, "ERROR")
            equal(#f.events_of("setup"), 1)
            assert(f.messages[#f.messages].line:find("PROBE state=ERROR", 1, true))
            quiescent(f)
        end
    end
end)

test("failed timer creation cancels a partly registered callback and releases GPIO5", function()
    for _, mode in ipairs({ "nil", "false", "throw" }) do
        for _, at in ipairs({ 1, 2 }) do
            local f = fixture()
            f.faults.timer_start = { at = at, mode = mode }
            local ok = f.probe.start(f.emit)
            if at == 1 then equal(ok, false)
            else assert(ok); f.fire() end
            equal(f.probe.status().state, "ERROR")
            equal(f.timers[f.latest_id].active, false)
            quiescent(f)
        end
    end
end)

test("STOP attempts timer, LOW and close independently and permits failed-close retry", function()
    for _, mode in ipairs({ "false", "throw" }) do
        local f = fixture()
        assert(f.probe.start(f.emit, true))
        f.faults.timer_stop = { mode = mode }
        f.faults.set = { mode = mode }
        f.faults.close = { mode = mode }
        equal(f.probe.stop(), false)
        equal(f.probe.status().state, "ERROR")
        equal(f.probe.status().gpio, 5)
        equal(#f.events_of("timer_stop"), 1)
        equal(#f.events_of("set"), 2)
        equal(#f.events_of("close"), 1)
        local count = #f.events
        for _, timer in pairs(f.timers) do timer.callback() end
        equal(#f.events, count)
        equal(f.probe.start(f.emit), false)
        f.faults = {}
        assert(f.probe.stop())
        quiescent(f)
    end
end)

test("close failure at a continuous boundary prevents another HIGH cycle", function()
    local f = fixture()
    f.faults.close = { mode = "throw" }
    assert(f.probe.start(f.emit, true))
    f.finish()
    equal(f.probe.status().state, "ERROR")
    equal(f.probe.status().cycle, 1)
    equal(#f.events_of("setup"), 1)
    f.faults = {}
    assert(f.probe.stop())
    quiescent(f)
end)

test("progress-emitter exceptions stop and release both initial HIGH and timed LOW", function()
    for _, target in ipairs({ "phase=HIGH", "phase=LOW" }) do
        local f = fixture()
        f.emit_fault = target
        local ok = f.probe.start(f.emit, true)
        if target == "phase=HIGH" then equal(ok, false)
        else assert(ok); f.fire() end
        equal(f.probe.status().state, "ERROR")
        quiescent(f)
    end
end)

test("STOP from HIGH, LOW or cycle-boundary emitters cannot schedule more output", function()
    for _, target in ipairs({ "phase=HIGH", "phase=LOW", "event=NEXT_CYCLE" }) do
        local f = fixture()
        f.on_emit = function(line)
            if line:find(target, 1, true) then assert(f.probe.stop()) end
        end
        local ok = f.probe.start(f.emit, true)
        if target == "phase=HIGH" then equal(ok, false)
        else assert(ok); f.finish() end
        equal(f.probe.status().state, "STOPPED")
        equal(#f.events_of("setup"), 1)
        quiescent(f)
    end
end)

local failures = {}
for _, item in ipairs(tests) do
    local ok, err = xpcall(item[2], debug.traceback)
    _G.print = real_print
    if ok then real_print("PASS " .. item[1])
    else
        failures[#failures + 1] = item[1] .. ": " .. tostring(err)
        real_print("FAIL " .. failures[#failures])
    end
end
real_print(string.format("Result: %d/%d passed", #tests - #failures, #tests))
if #failures > 0 then error(table.concat(failures, "\n"), 0) end
