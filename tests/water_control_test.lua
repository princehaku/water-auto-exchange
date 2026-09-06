-- Lua 5.1 adapter tests: every GPIO, timer and clock is simulated.
local root = (arg and arg[1]) or "."
package.path = root .. "/src/?.lua;" .. package.path
local water = require "water_control"
local tests = {}
local function test(name, callback) tests[#tests + 1] = { name, callback } end
local function equal(actual, expected, message)
    assert(actual == expected, (message or "unexpected value") .. ": expected "
        .. tostring(expected) .. ", got " .. tostring(actual))
end
local function contains(value, part)
    assert(tostring(value):find(part, 1, true), "expected '" .. tostring(value) .. "' to include '" .. part .. "'")
end
local function config()
    -- These are mock assignments, not the project's measured PCB mappings.
    return {
        enabled = true, mapping_confirmed = true, wiring_confirmed = true,
        outputs = {
            fill = { gpio = 5, on_level = 1, off_level = 0 },
            drain = { gpio = 9, on_level = 0, off_level = 1 }
        },
        inputs = {
            need_fill = { gpio = 10, active_level = 0, pull = "UP" },
            overflow = { enabled = false, gpio = 11, active_level = 1, pull = "DOWN" }
        },
        poll_ms = 100,
        timing = { debounce_ms = 100, switch_delay_ms = 200,
            drain_timeout_ms = 1000, fill_timeout_ms = 1000 }
    }
end
local function fixture(cfg)
    cfg = cfg or config()
    local h = { config = cfg, events = {}, levels = {}, timers = {},
        tick = 0, next_id = 0, signed = false, reads = 0, messages = {} }
    for _, name in ipairs({ "need_fill", "overflow" }) do
        local item = cfg.inputs[name]
        if item and item.gpio and type(item.active_level) == "number" then
            h.levels[item.gpio] = 1 - item.active_level
        end
    end
    local function event(kind, gpio, value)
        h.events[#h.events + 1] = { kind = kind, gpio = gpio, value = value, tick = h.tick }
    end
    local function physical_interlock()
        local fill, drain = cfg.outputs.fill, cfg.outputs.drain
        if h.levels[fill.gpio] == fill.on_level and h.levels[drain.gpio] == drain.on_level then
            error("simultaneous_physical_fill_and_drain")
        end
    end
    local deps = {
        sys = {
            timerStart = function(callback, delay)
                event("timer_start", nil, delay)
                h.next_id = h.next_id + 1
                h.timers[h.next_id] = { callback = callback, active = true }
                if h.on_timer_start then return h.on_timer_start(callback, delay, h.next_id) end
                return h.next_id
            end,
            timerStop = function(id)
                event("timer_stop", nil, id)
                if h.on_timer_stop then h.on_timer_stop(id) end
                for key, timer in pairs(h.timers) do
                    if key == id or timer.callback == id then timer.active = false end
                end
            end
        },
        pins = {
            setup = function(gpio, value, pull)
                event(value == nil and "input_setup" or "output_setup", gpio, value)
                if value ~= nil then h.levels[gpio] = value end
                if h.on_setup then h.on_setup(gpio, value, pull) end
                return function() error("output_closure_must_not_be_read") end
            end
        },
        pio = { PULLUP = 1001, PULLDOWN = 1002, NOPULL = 1003, pin = {
            setval = function(value, gpio)
                event("write", gpio, value)
                if h.on_write then
                    local result = h.on_write(value, gpio)
                    if result == false then return false end
                end
                h.levels[gpio] = value
                physical_interlock()
            end,
            getval = function(gpio)
                h.reads = h.reads + 1
                event("read", gpio)
                if h.on_read then return h.on_read(gpio) end
                return h.levels[gpio]
            end
        } },
        tick = function()
            event("tick")
            if h.clock_value ~= nil then return h.clock_value end
            local value = h.tick % 4294967296
            if h.signed and value >= 2147483648 then value = value - 4294967296 end
            return value
        end,
        emit = function(line)
            h.messages[#h.messages + 1] = line
            if h.on_emit then h.on_emit(line) end
        end
    }
    h.controller = water.new(cfg, deps)
    function h.sensor(name, active)
        local item = cfg.inputs[name]
        h.levels[item.gpio] = active and item.active_level or (1 - item.active_level)
    end
    function h.elapse(ms) h.tick = h.tick + ms * 16 end
    function h.pending()
        local result
        for id, timer in pairs(h.timers) do
            if timer.active and (result == nil or id < result) then result = id end
        end
        assert(result, "no_pending_timer")
        return result, h.timers[result].callback
    end
    function h.poll(ms)
        h.elapse(ms or cfg.poll_ms)
        local id, callback = h.pending()
        h.timers[id].active = false
        callback()
    end
    function h.ready(need_fill)
        h.sensor("need_fill", need_fill == true)
        assert(h.controller.init())
        h.poll(cfg.timing.debounce_ms)
        equal(h.controller.status().ready, true)
    end
    function h.writes_since(index, gpio, value)
        local count = 0
        for i = (index or 0) + 1, #h.events do
            local e = h.events[i]
            if e.kind == "write" and (gpio == nil or e.gpio == gpio)
                and (value == nil or e.value == value) then count = count + 1 end
        end
        return count
    end
    return h
end
local function state(h, name, fill, drain)
    local s = h.controller.status()
    equal(s.state, name)
    equal(s.fill, fill == true)
    equal(s.drain, drain == true)
    assert(not (s.fill and s.drain), "commanded_interlock")
    return s
end
local function both_off_attempted(h, since)
    for _, name in ipairs({ "fill", "drain" }) do
        local item = h.config.outputs[name]
        assert(h.writes_since(since, item.gpio, item.off_level) > 0, "missing_off_attempt_" .. name)
    end
end

test("default unconfigured controller has no hardware side effects on any command", function()
    local cfg = require "water_config"
    local calls = 0
    local function forbidden() calls = calls + 1; error("unexpected_hardware_access") end
    local c = water.new(cfg, {
        sys = { timerStart = forbidden, timerStop = forbidden },
        pins = { setup = forbidden }, pio = { pin = { setval = forbidden, getval = forbidden } },
        tick = forbidden, emit = function() end
    })
    equal(c.status().state, "UNCONFIGURED")
    for _, command in ipairs({ "init", "start", "fill", "reset" }) do
        equal(c[command](), false, command)
    end
    assert(c.stop())
    equal(c.status().ready, false)
    equal(c.status().outputs_known, false)
    equal(calls, 0)
end)

test("GPIO conflicts and invalid electrical settings are rejected before initialization", function()
    local changes = {
        function(c) c.outputs.drain.gpio = c.outputs.fill.gpio end,
        function(c) c.inputs.need_fill.gpio = c.outputs.fill.gpio end,
        function(c) c.inputs.overflow.enabled = true; c.inputs.overflow.gpio = c.inputs.need_fill.gpio end,
        function(c) c.outputs.fill.gpio = 0 end,
        function(c) c.outputs.fill.gpio = 29 end,
        function(c) c.outputs.fill.off_level = c.outputs.fill.on_level end,
        function(c) c.inputs.need_fill.active_level = true end,
        function(c) c.inputs.need_fill.pull = "INVALID" end,
        function(c) c.poll_ms = 0 end,
        function(c) c.timing.fill_timeout_ms = math.huge end,
        function(c) c.enabled = false end,
        function(c) c.mapping_confirmed = false end,
        function(c) c.wiring_confirmed = false end
    }
    for _, change in ipairs(changes) do
        local cfg = config(); change(cfg)
        local h = fixture(cfg)
        equal(h.controller.init(), false)
        equal(h.controller.status().state, "UNCONFIGURED")
        equal(#h.events, 0)
    end
end)

test("initialization applies both measured OFF levels before reading inputs and stays idle", function()
    local h = fixture()
    equal(#h.events, 0)
    assert(h.controller.init())
    equal(h.controller.status().outputs_known, true)
    equal(h.events[1].kind, "output_setup"); equal(h.events[1].gpio, 5); equal(h.events[1].value, 0)
    equal(h.events[2].kind, "output_setup"); equal(h.events[2].gpio, 9); equal(h.events[2].value, 1)
    equal(h.events[3].kind, "input_setup"); equal(h.events[3].gpio, 10)
    state(h, "IDLE")
    equal(h.controller.status().ready, false)
    local ok, reason = h.controller.start()
    equal(ok, false); equal(reason, "inputs_not_stable")
    h.poll(100)
    equal(h.controller.status().ready, true)
    state(h, "IDLE")
    equal(h.controller.init(), false)
    equal(h.writes_since(0), 0)
end)

test("configuration is snapshotted and status tables cannot alter live hardware settings", function()
    local cfg = config(); local h = fixture(cfg)
    cfg.outputs.fill.gpio, cfg.outputs.fill.off_level = 23, 1
    cfg.inputs.need_fill.gpio = 22
    cfg.poll_ms = 700
    assert(h.controller.init())
    equal(h.events[1].gpio, 5); equal(h.events[1].value, 0)
    equal(h.events[3].gpio, 10)
    equal(h.events[#h.events].kind, "timer_start")
    equal(h.events[#h.events].value, 100)
    local s = h.controller.status(); s.state, s.fill, s.reason = "FILLING", true, "mutated"
    state(h, "IDLE")
end)

test("relay debounce, switch deadtime and C threshold complete exactly one exchange", function()
    local h = fixture(); h.ready(false)
    assert(h.controller.start()); state(h, "DRAINING", false, true)
    local low_change = #h.events
    h.sensor("need_fill", true); h.poll(100)
    state(h, "DRAINING", false, true)
    h.sensor("need_fill", false); h.poll(50)
    h.sensor("need_fill", true); h.poll(50)
    state(h, "DRAINING", false, true)
    h.poll(99); state(h, "DRAINING", false, true)
    h.poll(1); state(h, "SETTLING")
    both_off_attempted(h, low_change)
    local stopped_at = h.tick
    h.poll(199); state(h, "SETTLING")
    h.poll(1); state(h, "FILLING", true)
    equal(h.tick - stopped_at, 200 * 16)
    h.sensor("need_fill", false); h.poll(100); state(h, "FILLING", true)
    h.poll(100); equal(state(h, "DONE").cycle, 1)
    local done_events = #h.events
    h.poll(2000); state(h, "DONE")
    equal(h.writes_since(done_events), 0)
end)

test("initial refill is explicit and cannot be substituted by START below B", function()
    local h = fixture(); h.ready(true)
    local ok, reason = h.controller.start()
    equal(ok, false); equal(reason, "level_not_ready")
    assert(h.controller.fill()); state(h, "FILLING", true)
    equal(h.controller.start(), false)
    h.sensor("need_fill", false); h.poll(100); h.poll(100)
    state(h, "DONE")
    ok, reason = h.controller.fill()
    equal(ok, false); equal(reason, "fill_not_requested")
end)

test("STOP invalidates retained timer callbacks and monitoring never restarts outputs", function()
    local h = fixture(); h.ready(false); assert(h.controller.start())
    local _, stale = h.pending()
    assert(h.controller.stop()); state(h, "IDLE")
    local after_stop = #h.events
    h.sensor("need_fill", true); h.elapse(500); stale()
    equal(#h.events, after_stop, "stale callback accessed hardware")
    h.poll(100); h.poll(100); state(h, "IDLE")
    equal(h.writes_since(after_stop), 0)
end)

test("drain and fill timeouts attempt both outputs OFF and remain latched", function()
    for _, filling in ipairs({ false, true }) do
        local h = fixture(); h.ready(filling)
        if filling then assert(h.controller.fill()) else assert(h.controller.start()) end
        local before = #h.events
        h.poll(1000)
        equal(state(h, "FAULT").reason, filling and "fill_timeout" or "drain_timeout")
        both_off_attempted(h, before)
        assert(h.controller.stop()); state(h, "FAULT")
        equal(h.controller.start(), false)
        h.poll(100); state(h, "FAULT")
    end
end)

test("bad sensor reads stop both outputs and reject commands while the sensor is unreadable", function()
    for _, failure in ipairs({ "throw", "nil", "boolean", "two" }) do
        local h = fixture(); h.ready(false); assert(h.controller.start())
        local before = #h.events
        h.on_read = function()
            if failure == "throw" then error("sensor_disconnected") end
            if failure == "boolean" then return false end
            if failure == "two" then return 2 end
        end
        h.poll(100); state(h, "FAULT")
        both_off_attempted(h, before)
        equal(h.controller.reset(), false)
        equal(h.controller.fill(), false)
    end
end)

test("sensor fault recovery must observe a fresh stable interval before RESET", function()
    local h = fixture(); h.ready(false); assert(h.controller.start())
    h.on_read = function() error("read_failed") end
    h.poll(100); state(h, "FAULT")
    h.on_read = nil
    local ok, reason = h.controller.reset()
    equal(ok, false, "one recovered sample must not reset the fault")
    equal(reason, "inputs_not_stable")
    h.poll(99); equal(h.controller.reset(), false)
    h.poll(1); assert(h.controller.reset()); state(h, "IDLE")
end)

test("partially successful ON write failures retry both OFF writes", function()
    for _, filling in ipairs({ false, true }) do
        local h = fixture(); h.ready(filling)
        local item = h.config.outputs[filling and "fill" or "drain"]
        local failed_at
        h.on_write = function(value, gpio)
            if gpio == item.gpio and value == item.on_level then
                h.levels[gpio] = value -- Native operation may take effect before throwing.
                failed_at = #h.events
                error("partial_on_failure")
            end
        end
        local before = #h.events
        local ok
        if filling then ok = h.controller.fill() else ok = h.controller.start() end
        equal(ok, false)
        state(h, "FAULT")
        both_off_attempted(h, before)
        both_off_attempted(h, failed_at)
        equal(h.levels[item.gpio], item.off_level)
    end
end)

test("OFF failure still attempts the other output and never energizes the requested output", function()
    local h = fixture(); h.ready(false)
    local item = h.config.outputs.fill
    h.on_write = function(value, gpio)
        if gpio == item.gpio and value == item.off_level then return false end
    end
    local before = #h.events
    equal(h.controller.start(), false)
    equal(h.controller.status().state, "FAULT")
    equal(h.controller.status().outputs_known, false)
    both_off_attempted(h, before)
    equal(h.writes_since(before, h.config.outputs.drain.gpio, h.config.outputs.drain.on_level), 0)
    contains(h.controller.status().reason, "off")
end)

test("unknown OFF state is retried even when the last commanded boolean was already false", function()
    local h = fixture(); h.ready(false)
    h.on_write = function(value, gpio)
        if gpio == 5 and value == 0 then return false end
    end
    equal(h.controller.start(), false)
    local s = h.controller.status()
    equal(s.state, "FAULT"); equal(s.fill, false); equal(s.outputs_known, false)
    h.on_write = nil
    local before = #h.events
    h.poll(100)
    both_off_attempted(h, before)
    equal(h.controller.status().outputs_known, true)
    assert(h.controller.reset()); state(h, "IDLE")
end)

test("active output OFF failure remains faulted with its commanded uncertainty visible", function()
    local h = fixture(); h.ready(true); assert(h.controller.fill())
    h.on_write = function(value, gpio)
        if gpio == 5 and value == 0 then error("off_stuck") end
    end
    local before = #h.events
    equal(h.controller.stop(), false)
    local s = h.controller.status()
    equal(s.state, "FAULT"); equal(s.fill, true); equal(s.drain, false)
    equal(s.outputs_known, false)
    both_off_attempted(h, before)
    equal(h.controller.start(), false)
    equal(h.controller.reset(), false)
end)

test("output cleanup failures are visible alongside an earlier overflow cause", function()
    local cfg = config(); cfg.inputs.overflow.enabled = true
    local h = fixture(cfg); h.ready(true); assert(h.controller.fill())
    h.on_write = function(value, gpio)
        if gpio == 5 and value == 0 then error("fill_off_stuck") end
    end
    h.sensor("overflow", true); h.poll(100)
    local s = h.controller.status()
    equal(s.state, "FAULT")
    contains(s.reason, "overflow")
    local description = s.reason .. " " .. tostring(s.cleanup_reason or s.cleanup_error or s.output_error)
    contains(description, "off")
end)

test("partially configured outputs are switched OFF after setup failure", function()
    local h = fixture()
    h.on_setup = function(gpio, value)
        if gpio == 9 and value ~= nil then error("partial_drain_setup") end
    end
    equal(h.controller.init(), false)
    state(h, "FAULT")
    both_off_attempted(h, 0)
    equal(h.controller.start(), false)
end)

test("timer registration failure cancels partial registration and cleans up outputs", function()
    local h = fixture()
    h.on_timer_start = function() return nil end
    equal(h.controller.init(), false)
    state(h, "FAULT")
    both_off_attempted(h, 0)
    local before = #h.events
    for _, timer in pairs(h.timers) do timer.callback() end
    equal(#h.events, before)
    equal(h.controller.start(), false)
end)

test("timer cancellation failure still stops both outputs and old callbacks do nothing", function()
    local h = fixture(); h.ready(false); assert(h.controller.start())
    local _, stale = h.pending()
    h.on_timer_stop = function() error("cancel_failed") end
    local before = #h.events
    equal(h.controller.stop(), false); state(h, "FAULT")
    both_off_attempted(h, before)
    local stopped = #h.events
    stale(); equal(#h.events, stopped)
end)

test("timer rescheduling failure during active work stops outputs and forbids restart", function()
    local h = fixture(); h.ready(false); assert(h.controller.start())
    h.on_timer_start = function() error("timer_queue_full") end
    local before = #h.events
    h.poll(100); state(h, "FAULT")
    both_off_attempted(h, before)
    equal(h.controller.start(), false)
    equal(h.controller.reset(), false)
    local stopped = #h.events
    for _, timer in pairs(h.timers) do timer.callback() end
    equal(#h.events, stopped)
end)

test("tick conversion uses sixteen ticks per millisecond", function()
    local h = fixture(); assert(h.controller.init())
    h.poll(99); equal(h.controller.status().ready, false)
    h.poll(1); equal(h.controller.status().ready, true)
    assert(h.controller.start())
    h.poll(999); state(h, "DRAINING", false, true)
    h.poll(1); equal(state(h, "FAULT").reason, "drain_timeout")
end)

test("signed tick boundary and complete 32-bit rollover preserve deadlines", function()
    for _, start_tick in ipairs({ 2147483008, 4294966400 }) do
        local h = fixture(); h.signed = true; h.tick = start_tick
        h.ready(false); assert(h.controller.start())
        h.poll(999); state(h, "DRAINING", false, true)
        h.poll(1); equal(state(h, "FAULT").reason, "drain_timeout")
    end
end)

test("reversed or malformed clocks fail closed", function()
    for _, clock in ipairs({ -1, "bad", math.huge, 0 / 0 }) do
        local h = fixture(); h.ready(false); assert(h.controller.start())
        local before = #h.events
        h.clock_value = clock
        h.poll(100); state(h, "FAULT")
        both_off_attempted(h, before)
    end
end)

test("overflow is immediate and RESET requires a debounced clear without automatic restart", function()
    local cfg = config(); cfg.inputs.overflow.enabled = true
    local h = fixture(cfg); h.ready(true); assert(h.controller.fill())
    equal(h.controller.status().overflow_protection, true)
    h.sensor("overflow", true)
    local before = #h.events
    equal(h.controller.start(), false) -- Command samples before the next timer tick.
    equal(state(h, "FAULT").reason, "overflow")
    both_off_attempted(h, before)
    local ok, reason = h.controller.reset()
    equal(ok, false); equal(reason, "overflow_active")
    h.sensor("overflow", false)
    ok, reason = h.controller.reset()
    equal(ok, false); equal(reason, "inputs_not_stable")
    h.poll(99); equal(h.controller.reset(), false)
    h.poll(1); assert(h.controller.reset()); state(h, "IDLE")
    h.poll(1000); state(h, "IDLE")
end)

test("logging failure cannot interrupt cleanup or sensor monitoring", function()
    local h = fixture(); h.on_emit = function() error("logger_down") end
    h.ready(false); assert(h.controller.start())
    h.poll(1000); state(h, "FAULT")
    assert(h.controller.reset()); state(h, "IDLE")
    assert(h.controller.stop())
    h.poll(100); state(h, "IDLE")
end)

local failures = 0
for _, item in ipairs(tests) do
    local ok, reason = pcall(item[2])
    if ok then print("PASS " .. item[1]) else
        failures = failures + 1
        print("FAIL " .. item[1] .. ": " .. tostring(reason))
    end
end
assert(failures == 0, tostring(failures) .. " water-control tests failed")
print("water_control_test: " .. #tests .. "/" .. #tests .. " passed")
