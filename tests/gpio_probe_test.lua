-- Lua 5.1 tests with stubbed hardware; no serial port or GPIO is accessed.
local root = (arg and arg[1]) or "."
package.path = root .. "/src/?.lua;" .. package.path
local real_print = print
local expected_gpio = { 13, 22, 23 }
local expected_physical = { 43, 7, 8 }
local expected_candidates = "13,22,23"
local allowed = {}
for _, pin in ipairs(expected_gpio) do allowed[pin] = true end

local function equal(actual, expected, message)
    assert(actual == expected, (message or "unexpected value")
        .. ": expected " .. tostring(expected) .. ", got " .. tostring(actual))
end

local function fixture()
    for _, name in ipairs({ "gpio_probe", "sys", "pins" }) do package.loaded[name] = nil end
    local f = {
        now = 0, events = {}, messages = {}, logs = {}, active = {}, timers = {},
        attempts = {}, faults = {}, required = 0, next_id = 0,
        power_reads = 0, uart_reads = 0, unauthorized_gpio_accesses = 0
    }
    local function event(kind, a, b)
        if kind == "setup" or kind == "set" or kind == "close" then
            if not allowed[a] then
                f.unauthorized_gpio_accesses = f.unauthorized_gpio_accesses + 1
                error("GPIO outside the authorized DO2 candidates: " .. tostring(a))
            end
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
    -- Count before throwing so a pcall in the implementation cannot hide forbidden access.
    _G.pmd = setmetatable({}, { __index = function(_, key)
        f.power_reads = f.power_reads + 1
        error("power API is forbidden for this fixed-domain group: " .. tostring(key))
    end })
    _G.uart = setmetatable({}, { __index = function(_, key)
        f.uart_reads = f.uart_reads + 1
        error("UART API is forbidden in the probe: " .. tostring(key))
    end })
    _G.print = function(line) f.logs[#f.logs + 1] = line end
    _G.pio = { pin = {
        setval = function(value, pin)
            assert(value == 0 or value == 1, "only explicit 0/1 writes are allowed")
            if event("set", pin, value) == "false" then return false end
            f.active[pin] = value
        end
    } }
    package.preload.pins = function()
        f.required = f.required + 1
        return {
            setup = function(pin, value)
                equal(next(f.active), nil, "release the preceding output before setup")
                equal(value, 0, "setup must initially configure LOW")
                f.active[pin] = value -- Model partial setup before a reported failure.
                local result = event("setup", pin, value)
                if result == "false" then return false end
                if result == "nil" then return nil end
                return function(value)
                    assert(value ~= nil, "never read an output closure without an argument")
                    error("writes should use pio.pin.setval so failures are observable")
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
                f.next_id = f.next_id + 1
                local id = f.next_id
                -- Register before failure to exercise partial timer-start cleanup.
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
    function f.advance(ticks)
        for _ = 1, ticks do f.fire() end
    end
    function f.finish()
        local guard = 0
        while f.probe.status().state == "RUNNING" do
            guard = guard + 1
            assert(guard <= 6, "single pass must finish after six timers")
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
    equal(next(f.active), nil, "all GPIOs must be released")
    local before = #f.events
    for _, timer in pairs(f.timers) do timer.callback() end
    equal(#f.events, before, "stale callbacks must perform no I/O")
    equal(f.power_reads, 0, "probe must never access pmd")
    equal(f.uart_reads, 0, "probe must never access uart")
    equal(f.unauthorized_gpio_accesses, 0, "only GPIO13/22/23 may be touched")
end

local tests = {}
local function test(name, callback) tests[#tests + 1] = { name, callback } end

test("require, status and an initial STOP perform no hardware or power access", function()
    local f = fixture()
    equal(#f.events, 0)
    equal(f.required, 0)
    local status = f.probe.status()
    equal(status.state, "IDLE")
    equal(status.gpio, nil)
    equal(status.level, nil)
    equal(status.index, 0)
    equal(status.total, 3)
    equal(status.hold_ms, 5000)
    equal(status.continuous, false)
    equal(status.cycle, 0)
    equal(status.cycle_ms, 30000)
    equal(status.candidates, expected_candidates)
    equal(f.probe.start(f.emit, "true"), false)
    status.state, status.total, status.candidates = "RUNNING", 99, "other"
    equal(f.probe.status().state, "IDLE")
    equal(f.probe.status().total, 3)
    equal(f.probe.status().candidates, expected_candidates)
    assert(f.probe.stop())
    equal(#f.events, 0)
    equal(f.required, 0)
    equal(f.power_reads, 0)
    quiescent(f)
end)

test("single pass covers exactly three physical mappings with HIGH and LOW five seconds each", function()
    local f = fixture()
    assert(f.probe.start(f.emit))
    equal(f.probe.status().gpio, 13)
    equal(f.probe.status().level, 1)
    f.finish()
    equal(f.now, 30000)
    equal(f.probe.status().state, "DONE")
    equal(f.probe.status().cycle, 1)
    equal(f.probe.status().gpio, nil)
    local setups, writes, closes = f.events_of("setup"), f.events_of("set"), f.events_of("close")
    equal(#setups, 3); equal(#writes, 9); equal(#closes, 3)
    for i, pin in ipairs(expected_gpio) do
        equal(setups[i].a, pin)
        equal(setups[i].b, 0)
        equal(setups[i].time, (i - 1) * 10000)
        local high, low, released = writes[3 * i - 2], writes[3 * i - 1], writes[3 * i]
        equal(high.a, pin); equal(high.b, 1); equal(high.time, (i - 1) * 10000)
        equal(low.a, pin); equal(low.b, 0); equal(low.time, (i - 1) * 10000 + 5000)
        equal(released.a, pin); equal(released.b, 0); equal(released.time, i * 10000)
        equal(closes[i].a, pin); equal(closes[i].time, i * 10000)
    end
    local progress = {}
    for _, item in ipairs(f.messages) do
        if item.line:match("^PROBE gpio=") then progress[#progress + 1] = item end
    end
    equal(#progress, 6)
    for n, message in ipairs(progress) do
        local i = math.floor((n - 1) / 2) + 1
        local high = n % 2 == 1
        equal(message.time, (n - 1) * 5000)
        equal(message.line, string.format(
            "PROBE gpio=%d physical=%d level=%d phase=%s index=%d/3 hold_ms=5000 domain=V_GLOBAL_1V8",
            expected_gpio[i], expected_physical[i], high and 1 or 0, high and "HIGH" or "LOW", i))
    end
    equal(#f.events_of("timer_start"), 6)
    equal(f.messages[1].line, "PROBE state=RUNNING event=START total=3 hold_ms=5000 cycle_ms=30000 target=DO2 domain=V_GLOBAL_1V8 continuous=0")
    equal(f.messages[#f.messages].line, "PROBE state=DONE event=END total=3 duration_ms=30000")
    equal(#f.logs, #f.messages)
    for i, message in ipairs(f.messages) do equal(f.logs[i], message.line) end
    quiescent(f)
end)

test("continuous mode releases the third pin before restarting GPIO13 every 30 seconds", function()
    local f = fixture()
    assert(f.probe.start(f.emit, true))
    for n = 1, 12 do
        f.fire()
        if n == 6 or n == 12 then
            local status = f.probe.status()
            equal(f.now, n * 5000)
            equal(status.state, "RUNNING")
            equal(status.continuous, true)
            equal(status.gpio, 13)
            equal(status.cycle, n / 6 + 1)
            equal(status.level, 1)
            local closes = f.events_of("close")
            equal(closes[#closes].a, 23)
            equal(closes[#closes].time, f.now)
            equal(f.events_of("setup")[#closes + 1].a, 13)
            equal(f.timers[f.latest_id].due, f.now + 5000)
        end
    end
    equal(#f.events_of("setup"), 7)
    equal(#f.events_of("close"), 6)
    assert(f.probe.stop())
    quiescent(f)
end)

test("STOP at all six phases and the next cycle releases outputs and rejects old callbacks after restart", function()
    for ticks = 0, 6 do
        local f = fixture()
        assert(f.probe.start(f.emit, true))
        f.advance(ticks)
        local current = f.probe.status().gpio
        equal(current, expected_gpio[math.floor((ticks % 6) / 2) + 1])
        local id, callback = f.latest_id, f.timers[f.latest_id].callback
        assert(f.probe.stop())
        equal(f.probe.status().state, "STOPPED")
        equal(f.timers[id].active, false)
        local writes, closes = f.events_of("set"), f.events_of("close")
        equal(writes[#writes].a, current)
        equal(writes[#writes].b, 0)
        equal(writes[#writes].time, f.now, "STOP must not wait for a timer")
        equal(closes[#closes].a, current)
        quiescent(f)
        assert(f.probe.start(f.emit))
        equal(f.probe.status().continuous, false)
        equal(f.probe.status().cycle, 1)
        local count = #f.events
        callback()
        equal(#f.events, count)
        assert(f.probe.stop())
        quiescent(f)
    end
end)

test("duplicate START preserves current progress and DONE needs explicit restart", function()
    local f = fixture()
    assert(f.probe.start(f.emit))
    local count, id = #f.events, f.latest_id
    local ok, reason = f.probe.start(function() error("wrong emitter") end, true)
    equal(ok, false); equal(reason, "already_running")
    equal(#f.events, count); equal(f.latest_id, id)
    equal(f.probe.status().continuous, false)
    f.finish()
    quiescent(f)
    assert(f.probe.start(f.emit, true))
    equal(f.probe.status().cycle, 1)
    assert(f.probe.stop())
    quiescent(f)
end)

test("partial setup failure at each candidate closes the pin", function()
    for at = 1, 3 do
        for _, mode in ipairs({ "nil", "false", "throw" }) do
            local f = fixture()
            f.faults.setup = { at = at, mode = mode }
            local ok = f.probe.start(f.emit)
            if at == 1 then equal(ok, false)
            else assert(ok); f.finish() end
            equal(f.probe.status().state, "ERROR")
            equal(#f.events_of("setup"), at)
            equal(f.events_of("close")[at].a, expected_gpio[at])
            quiescent(f)
            equal(f.probe.start(f.emit), false)
            f.faults = {}
            assert(f.probe.stop())
        end
    end
end)

test("setval failure at every HIGH, LOW or release cleans all resources", function()
    for at = 1, 9 do
        for _, mode in ipairs({ "false", "throw" }) do
            local f = fixture()
            f.faults.set = { at = at, mode = mode }
            local ok = f.probe.start(f.emit)
            if at == 1 then equal(ok, false)
            else assert(ok); f.finish() end
            equal(f.probe.status().state, "ERROR")
            assert(f.messages[#f.messages].line:find("PROBE state=ERROR", 1, true))
            quiescent(f)
        end
    end
end)

test("failed timers at all six phases cancel partly registered callbacks and release GPIOs", function()
    for at = 1, 6 do
        for _, mode in ipairs({ "nil", "false", "throw" }) do
            local f = fixture()
            f.faults.timer_start = { at = at, mode = mode }
            local ok = f.probe.start(f.emit)
            if at == 1 then equal(ok, false)
            else assert(ok); f.finish() end
            equal(f.probe.status().state, "ERROR")
            equal(f.timers[f.latest_id].active, false)
            quiescent(f)
        end
    end
end)

test("STOP independently attempts timer, LOW and GPIO close even when all fail", function()
    for ticks = 0, 5 do
        for _, mode in ipairs({ "false", "throw" }) do
            local f = fixture()
            assert(f.probe.start(f.emit, true))
            f.advance(ticks)
            local pin, before = f.probe.status().gpio, {}
            for _, kind in ipairs({ "timer_stop", "set", "close" }) do
                before[kind] = #f.events_of(kind)
                f.faults[kind] = { mode = mode }
            end
            equal(f.probe.stop(), false)
            equal(f.probe.status().state, "ERROR")
            equal(f.probe.status().gpio, pin, "failed close must retain ownership")
            for kind, count in pairs(before) do equal(#f.events_of(kind), count + 1) end
            local count = #f.events
            for _, timer in pairs(f.timers) do timer.callback() end
            equal(#f.events, count)
            equal(f.probe.start(f.emit), false)
            f.faults = {}
            assert(f.probe.stop())
            quiescent(f)
        end
    end
end)

test("GPIO close failures block the next candidate or next cycle", function()
    for at = 1, 3 do
        for _, mode in ipairs({ "false", "throw" }) do
            local f = fixture()
            f.faults.close = { at = at, mode = mode }
            assert(f.probe.start(f.emit, true))
            f.finish()
            equal(f.probe.status().state, "ERROR")
            equal(#f.events_of("setup"), at)
            equal(f.probe.status().cycle, 1)
            assert(f.probe.stop())
            quiescent(f)
        end
    end
end)

test("progress-emitter exceptions at each candidate release all resources", function()
    for _, target in ipairs({ "gpio=13 physical=43 level=1", "gpio=22 physical=7 level=1",
        "gpio=23 physical=8 level=0" }) do
        local f = fixture()
        f.emit_fault = target
        local ok = f.probe.start(f.emit, true)
        if target:find("gpio=13 ", 1, true) then equal(ok, false)
        else assert(ok); f.finish() end
        equal(f.probe.status().state, "ERROR")
        quiescent(f)
    end
end)

test("STOP from START, phase progress and cycle-boundary emitters cannot resume output", function()
    for _, target in ipairs({ "event=START", "gpio=13 physical=43 level=0",
        "gpio=22 physical=7 level=1", "gpio=23 physical=8 level=0", "event=NEXT_CYCLE" }) do
        local f = fixture()
        f.on_emit = function(line)
            if line:find(target, 1, true) then assert(f.probe.stop()) end
        end
        local ok = f.probe.start(f.emit, true)
        if target == "event=START" then equal(ok, false)
        else assert(ok); f.finish() end
        equal(f.probe.status().state, "STOPPED")
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
