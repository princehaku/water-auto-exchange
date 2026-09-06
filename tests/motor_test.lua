-- Pure Lua 5.1 tests. Run from the project root, or pass its path as arg[1].
-- sys/pins/uart are stubs; no serial port or physical GPIO is accessed.
local root = (arg and arg[1]) or "."
package.path = root .. "/src/?.lua;" .. package.path

local function equal(actual, expected, message)
    assert(actual == expected, (message or "unexpected value")
        .. ": expected " .. tostring(expected) .. ", got " .. tostring(actual))
end

local function contains(actual, expected)
    assert(actual:find(expected, 1, true), "missing text: " .. expected .. " in " .. actual)
end

local function confirmed_config()
    -- Synthetic test identifiers, not a proposed board mapping.
    return {
        enabled = true, mapping_confirmed = true, auto_start = false,
        input_1 = 10001, input_2 = 10002,
        on_levels = { input_1 = 1, input_2 = 0 },
        off_levels = { input_1 = 0, input_2 = 0 },
        on_ms = 3000, off_ms = 3000
    }
end

local function fixture()
    for _, name in ipairs({ "sys", "log", "pins", "motor_cycle", "motor_config", "gpio_probe", "usb_control" }) do
        package.loaded[name] = nil
    end
    local f = {
        pins_required = 0, setups = {}, writes = {}, outputs = {},
        timers = {}, timer_attempts = {}, timer_stops = {},
        rx = {}, replies = {}, uart_setups = {}
    }
    package.preload.pins = function()
        f.pins_required = f.pins_required + 1
        return {
            setup = function(pin, level)
                f.setups[#f.setups + 1] = { pin = pin, level = level }
                if f.setup_throw_at == #f.setups then error("injected setup exception") end
                if f.setup_fail_at == #f.setups then return nil end
                f.outputs[pin] = level
                return function(value)
                    f.writes[#f.writes + 1] = { pin = pin, level = value }
                    if f.write_fail_at == #f.writes then error("injected output exception") end
                    f.outputs[pin] = value
                end
            end
        }
    end
    package.preload.log = function()
        return { openTrace = function(enabled, uartid)
            equal(enabled, true)
            equal(uartid, nil, "logging must not configure a physical UART")
            f.trace_enabled = true
        end }
    end
    package.preload.sys = function()
        return {
            timerStart = function(callback, duration)
                local id = #f.timer_attempts + 1
                f.timer_attempts[id] = { callback = callback, duration = duration }
                if f.timer_throw_at == id then error("injected timer exception") end
                if f.timer_fail_at == id then return nil end
                f.timers[id] = { callback = callback, duration = duration, active = true }
                f.latest_timer = id
                return id
            end,
            timerStop = function(id)
                f.timer_stops[#f.timer_stops + 1] = id
                if f.timer_stop_fails then error("injected timer stop exception") end
                assert(f.timers[id], "unknown timer")
                f.timers[id].active = false
            end,
            init = function(first, second) f.sys_init = { first, second } end,
            run = function() f.sys_run = true end
        }
    end
    _G.uart = {
        USB = 0x81, PAR_NONE = 0, STOP_1 = 1,
        setup = function(id, baud, bits, parity, stop)
            equal(id, 0x81, "only USB may be configured")
            f.uart_setups[#f.uart_setups + 1] = { id, baud, bits, parity, stop }
        end,
        on = function(id, event, callback)
            equal(id, 0x81)
            equal(event, "receive")
            f.receive = callback
        end,
        read = function(id, mode, timeout)
            equal(id, 0x81)
            equal(mode, "*l")
            equal(timeout, 0)
            return table.remove(f.rx, 1) or ""
        end,
        write = function(id, value)
            equal(id, 0x81)
            f.replies[#f.replies + 1] = value
        end
    }
    _G.pmd = { ldoset = function() error("LDO configuration is forbidden in these tests") end }
    _G.PROJECT, _G.VERSION = "gk21_motor_test", "0.1.0"
    function f.fire(id)
        id = id or f.latest_timer
        assert(f.timers[id].active, "cannot fire an inactive timer")
        f.timers[id].active = false
        f.timers[id].callback()
    end
    function f.feed(chunk)
        f.rx[#f.rx + 1] = chunk
        assert(f.receive, "USB receiver not installed")
        f.receive()
    end
    f.motor = require "motor_cycle"
    return f
end

local function off(f)
    equal(f.outputs[10001], 0, "first input must be OFF")
    equal(f.outputs[10002], 0, "second input must be OFF")
end

local tests = {}
local function test(name, callback) tests[#tests + 1] = { name, callback } end

test("default START and STOP never load pins or configure outputs", function()
    local f = fixture()
    local config = require "motor_config"
    local ok, reason = f.motor.start(config)
    equal(ok, false)
    equal(reason, "mapping_not_confirmed")
    assert(f.motor.stop())
    equal(f.motor.status(), "STANDBY")
    equal(f.pins_required, 0)
    equal(#f.setups, 0)
    equal(#f.writes, 0)
    equal(#f.timer_attempts, 0)
end)

test("incomplete or invalid confirmed configurations have no GPIO effects", function()
    local changes = {
        function(c) c.mapping_confirmed = false end,
        function(c) c.enabled = false end,
        function(c) c.input_1 = nil end,
        function(c) c.input_2 = c.input_1 end,
        function(c) c.on_levels.input_2 = nil end,
        function(c) c.off_levels.input_1 = 2 end,
        function(c) c.on_levels = c.off_levels end,
        function(c) c.on_ms = 0 end
    }
    for _, change in ipairs(changes) do
        local f, config = fixture(), confirmed_config()
        change(config)
        equal(f.motor.start(config), false)
        equal(f.pins_required, 0)
        equal(#f.setups, 0)
        equal(#f.writes, 0)
    end
end)

test("confirmed cycle initializes OFF then alternates ON/OFF every 3000 ms", function()
    local f = fixture()
    assert(f.motor.start(confirmed_config()))
    equal(f.setups[1].level, 0)
    equal(f.setups[2].level, 0)
    equal(f.motor.status(), "RUNNING_ON")
    equal(f.outputs[10001], 1)
    equal(f.outputs[10002], 0)
    equal(f.timers[f.latest_timer].duration, 3000)
    f.fire()
    equal(f.motor.status(), "RUNNING_OFF")
    off(f)
    equal(f.timers[f.latest_timer].duration, 3000)
    f.fire()
    equal(f.motor.status(), "RUNNING_ON")
    equal(f.outputs[10001], 1)
    equal(f.timers[f.latest_timer].duration, 3000)
end)

test("STOP cancels timer and an already queued callback cannot restart output", function()
    local f = fixture()
    assert(f.motor.start(confirmed_config()))
    local id = f.latest_timer
    local queued_callback = f.timers[id].callback
    assert(f.motor.stop())
    equal(f.timer_stops[1], id)
    equal(f.timers[id].active, false)
    off(f)
    local write_count, timer_count = #f.writes, #f.timer_attempts
    queued_callback()
    equal(f.motor.status(), "STANDBY")
    equal(#f.writes, write_count)
    equal(#f.timer_attempts, timer_count)
end)

test("second input setup failure attempts to turn the first input OFF", function()
    for _, failure in ipairs({ "setup_fail_at", "setup_throw_at" }) do
        local f = fixture()
        f[failure] = 2
        equal(f.motor.start(confirmed_config()), false)
        equal(f.motor.status(), "STANDBY")
        equal(f.writes[1].pin, 10001)
        equal(f.writes[1].level, 0)
        equal(#f.timer_attempts, 0)
    end
end)

test("ON output exception triggers OFF attempts on both initialized inputs", function()
    local f = fixture()
    f.write_fail_at = 1
    equal(f.motor.start(confirmed_config()), false)
    off(f)
    equal(f.writes[2].pin, 10001)
    equal(f.writes[2].level, 0)
    equal(f.writes[3].pin, 10002)
    equal(f.writes[3].level, 0)
    equal(f.motor.status(), "STANDBY")
end)

test("output exception inside a timed transition retries OFF and stops scheduling", function()
    local f = fixture()
    assert(f.motor.start(confirmed_config()))
    f.write_fail_at = #f.writes + 1
    f.fire()
    off(f)
    equal(f.motor.status(), "STANDBY")
    equal(#f.timer_attempts, 1)
end)

test("timer creation failure turns both inputs OFF", function()
    for _, failure in ipairs({ "timer_fail_at", "timer_throw_at" }) do
        local f = fixture()
        f[failure] = 1
        equal(f.motor.start(confirmed_config()), false)
        off(f)
        equal(f.motor.status(), "STANDBY")
        equal(#f.writes, 4)
    end
end)

test("failed OFF write still attempts the second OFF and reports STOP_ERROR", function()
    local f = fixture()
    assert(f.motor.start(confirmed_config()))
    f.write_fail_at = #f.writes + 1
    equal(f.motor.stop(), false)
    equal(f.motor.status(), "STOP_ERROR")
    equal(f.writes[#f.writes].pin, 10002)
    equal(f.writes[#f.writes].level, 0)
    equal(f.timers[f.latest_timer].active, false)
end)

test("timer cancellation exception does not prevent either OFF attempt", function()
    local f = fixture()
    assert(f.motor.start(confirmed_config()))
    f.timer_stop_fails = true
    local callback = f.timers[f.latest_timer].callback
    equal(f.motor.stop(), false)
    off(f)
    local writes = #f.writes
    callback()
    equal(#f.writes, writes)
end)

test("USB fragmented STATUS/START/STOP and CRLF work without GPIO in standby", function()
    local f = fixture()
    assert(require("usb_control").start(f.motor, require "motor_config"))
    equal(#f.replies, 1)
    f.feed("STA")
    equal(#f.replies, 1, "partial command must wait for terminator")
    f.feed("TUS\r")
    equal(#f.replies, 2)
    contains(f.replies[2], "OK STATUS project=gk21_motor_test version=0.1.0 state=STANDBY")
    contains(f.replies[2], "mapping_confirmed=0 ready=0 reason=mapping_not_confirmed")
    f.feed("\nST")
    equal(#f.replies, 2, "CRLF must not produce a second reply")
    f.feed("ART\r\nSTO")
    contains(f.replies[3], "ERROR START mapping_not_confirmed")
    f.feed("P\n")
    contains(f.replies[4], "OK STOP stopped")
    equal(#f.replies, 4)
    equal(f.pins_required, 0)
    equal(#f.setups, 0)
    equal(#f.writes, 0)
end)

test("USB START with confirmed config runs and STOP immediately cancels it", function()
    local f = fixture()
    assert(require("usb_control").start(f.motor, confirmed_config()))
    f.feed("START\n")
    contains(f.replies[2], "OK START started")
    equal(f.motor.status(), "RUNNING_ON")
    local timer = f.latest_timer
    f.feed("STOP\r\nSTATUS\n")
    contains(f.replies[3], "OK STOP stopped")
    contains(f.replies[4], "state=STANDBY")
    equal(f.timers[timer].active, false)
    off(f)
end)

test("USB discards an entire oversized line and resumes at the next line", function()
    local f = fixture()
    assert(require("usb_control").start(f.motor, confirmed_config()))
    f.feed(string.rep("X", 65))
    contains(f.replies[2], "ERROR command_too_long")
    f.feed("START")
    equal(#f.replies, 2, "oversized line suffix must remain discarded")
    equal(f.pins_required, 0)
    f.feed("\r\nSTATUS\n")
    equal(#f.replies, 3)
    contains(f.replies[3], "state=STANDBY")
    f.feed("garbage\n")
    contains(f.replies[4], "ERROR unknown_command")
    equal(f.pins_required, 0)
end)

test("USB diagnostic interlock prevents simultaneous motor and probe outputs", function()
    local f = fixture()
    local state, starts, stops = "IDLE", 0, 0
    local probe = {
        status = function() return {
            state = state, gpio = 13, level = 0, total = 3,
            cycle_ms = 30000, candidates = "13,22,23"
        } end,
        start = function(emit, continuous)
            equal(continuous, true, "PROBE LOOP must request continuous diagnostics")
            starts = starts + 1
            state = "RUNNING"
            emit("PROBE gpio=13 level=0 hold_ms=5000")
            return true, "started"
        end,
        stop = function() stops = stops + 1; state = "STOPPED"; return true, "stopped" end
    }
    assert(require("usb_control").start(f.motor, confirmed_config(), probe))
    f.feed("PROBE LOOP\nSTART\nSTATUS\n")
    equal(starts, 1)
    contains(table.concat(f.replies), "ERROR START probe_not_stopped")
    contains(table.concat(f.replies), "probe_state=RUNNING probe_gpio=13 probe_level=0")
    contains(table.concat(f.replies), "probe_total=3 probe_cycle_ms=30000 probe_candidates=13,22,23")
    equal(#f.setups, 0, "motor must not configure outputs while probing")
    f.feed("STOP\nSTART\nPROBE\n")
    equal(stops, 1)
    equal(starts, 1, "a running motor blocks a new diagnostic")
    contains(table.concat(f.replies), "ERROR PROBE motor_not_stopped")
    equal(f.motor.status(), "RUNNING_ON")
    f.feed("STOP\n")
    off(f)
end)

test("USB STOP still stops motor when diagnostic cleanup reports failure", function()
    local f = fixture()
    assert(f.motor.start(confirmed_config()))
    local probe = {
        status = function() return { state = "ERROR" } end,
        stop = function() return false, "cleanup_failed" end
    }
    assert(require("usb_control").start(f.motor, confirmed_config(), probe))
    f.feed("STOP\n")
    contains(table.concat(f.replies), "ERROR STOP probe_cleanup_failed")
    equal(f.motor.status(), "STANDBY")
    off(f)
end)

local failures = {}
for _, item in ipairs(tests) do
    local ok, err = xpcall(item[2], debug.traceback)
    if ok then print("PASS " .. item[1])
    else
        failures[#failures + 1] = item[1] .. ": " .. tostring(err)
        print("FAIL " .. failures[#failures])
    end
end
print(string.format("Result: %d/%d passed", #tests - #failures, #tests))
if #failures > 0 then error(table.concat(failures, "\n"), 0) end
