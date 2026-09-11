-- Lua 5.1 USB/boot integration tests. No serial port or physical GPIO is used.
local root = (arg and arg[1]) or "."
package.path = root .. "/src/?.lua;" .. package.path

local function equal(actual, expected, message)
    assert(actual == expected, (message or "unexpected value") .. ": expected "
        .. tostring(expected) .. ", got " .. tostring(actual))
end
local function contains(actual, expected)
    assert(actual:find(expected, 1, true), "missing " .. expected .. " in " .. actual)
end

local function fixture(real_controller)
    for _, name in ipairs({"sys", "log", "pins", "netLed", "water_config", "water_control", "water_cycle", "water_usb", "water_network_config", "water_network"}) do
        package.loaded[name] = nil
    end
    local f = {rx = {}, replies = {}, calls = {}, logs = {}, setups = {}, timers = {}, gpio_calls = 0}
    local function forbidden()
        f.gpio_calls = f.gpio_calls + 1
        error("unconfigured application must not access GPIO")
    end
    package.preload.pins = function() forbidden() end
    _G.pio = {P0_12 = 12, pin = {setval = forbidden, getval = forbidden, close = forbidden}}
    package.preload.netLed = function()
        return {setup = function(enabled, gpio, lte)
            equal(enabled, true)
            equal(gpio, 12, "network indicator must use GPIO12")
            equal(lte, nil, "no second LED GPIO may be claimed")
            if f.led_failure then error("injected LED failure") end
            f.led_gpio = gpio
        end}
    end
    _G.pmd = {ldoset = forbidden}
    _G.rtos = {tick = forbidden}
    package.preload.sys = function()
        return {
            init = function(a, b) f.sys_init = {a, b} end,
            run = function() f.sys_run = true end,
            timerLoopStart = function(callback, ms)
                f.timers[#f.timers + 1] = {callback = callback, ms = ms}
                if f.timer_fail then return nil end
                return #f.timers
            end,
            timerStart = forbidden,
            timerStop = forbidden
        }
    end
    package.preload.log = function()
        return {openTrace = function(enabled, uartid)
            equal(enabled, true)
            equal(uartid, nil, "trace must not claim a physical UART")
            f.trace = true
        end}
    end
    _G.PROJECT, _G.VERSION = "water_auto_exchange", "0.5.3"
    _G.uart = {
        USB = 0x81, PAR_NONE = 0, STOP_1 = 1,
        setup = function(id, baud, bits, parity, stop)
            equal(id, 0x81)
            f.setups[#f.setups + 1] = {id, baud, bits, parity, stop}
            if f.setup_error then error("setup failure") end
            if f.setup_false then return false end
        end,
        on = function(id, event, callback)
            equal(id, 0x81)
            equal(event, "receive")
            if f.on_error then error("receive setup failure") end
            if f.on_false then return false end
            f.receive = callback
        end,
        read = function(id, mode, timeout)
            equal(id, 0x81)
            equal(mode, "*l")
            equal(timeout, 0)
            if f.read_error then error("read failure") end
            return table.remove(f.rx, 1) or ""
        end,
        write = function(id, value)
            equal(id, 0x81)
            if f.write_error then error("write failure") end
            if f.write_false then return false end
            f.replies[#f.replies + 1] = value
        end
    }
    local function count(name)
        f.calls[name] = (f.calls[name] or 0) + 1
        if f.throw_method == name then error("injected " .. name .. "\nexception") end
    end
    f.controller = {}
    function f.controller.init()
        count("init")
        assert(f.receive, "STOP handler must be installed before controller init")
        assert(f.sys_init, "sys.init must precede controller init")
        return false, "mapping_not_confirmed"
    end
    function f.controller.status()
        count("status")
        return {state = "STANDBY", reason = "mapping_not_confirmed", ready = false,
            fill = false, drain = false, need_fill = f.need_fill, overflow = false,
            cycle = 0, overflow_protection = false, outputs_known = f.outputs_known}
    end
    for _, command in ipairs({"start", "fill", "stop", "reset"}) do
        local name = command
        f.controller[name] = function()
            count(name)
            return f.reject ~= name, f.reject == name and "not_ready" or name .. "_accepted"
        end
    end
    if not real_controller then
        package.loaded.water_control = {new = function() return f.controller end}
    end
    function f.feed(chunk)
        f.rx[#f.rx + 1] = chunk
        assert(f.receive, "receiver not installed")
        f.receive()
    end
    function f.boot()
        local original_print = print
        _G.print = function(...)
            local values = {}
            for i = 1, select("#", ...) do values[i] = tostring(select(i, ...)) end
            f.logs[#f.logs + 1] = table.concat(values, " ")
        end
        local ok, err = pcall(dofile, root .. "/src/main.lua")
        _G.print = original_print
        if not ok then error(err, 0) end
    end
    return f
end

local tests = {}
local function test(name, fn) tests[#tests + 1] = {name, fn} end

test("fragmented STATUS is read-only and unknown water level is explicit", function()
    local f = fixture()
    assert(require("water_usb").start(f.controller))
    f.feed("STA")
    equal(#f.replies, 1)
    f.feed("TUS\r")
    contains(f.replies[2], "OK STATUS project=water_auto_exchange version=0.5.3")
    contains(f.replies[2], "ready=0 fill=0 drain=0 outputs_known=0 need_fill=unknown")
    f.feed("\n")
    equal(#f.replies, 2, "CRLF must yield one reply")
    equal(f.calls.status, 1)
    equal(f.calls.start, nil)
    equal(f.calls.stop, nil)
    equal(f.gpio_calls, 0)
end)

test("output certainty requires explicit true and does not imply voltage readback", function()
    local f = fixture()
    assert(require("water_usb").start(f.controller))
    f.outputs_known = false
    f.feed("STATUS\n")
    contains(f.replies[2], "fill=0 drain=0 outputs_known=0")
    f.outputs_known = true
    f.feed("STATUS\n")
    contains(f.replies[3], "fill=0 drain=0 outputs_known=1")
    f.outputs_known = nil
    f.feed("STATUS\n")
    contains(f.replies[4], "fill=0 drain=0 outputs_known=0")
    equal(f.gpio_calls, 0, "formatting must not read output voltage or GPIO")
    equal(f.calls.stop, nil)
end)

test("START FILL STOP RESET dispatch once with case folding and fragmented lines", function()
    local f = fixture()
    assert(require("water_usb").start(f.controller))
    f.feed(" start \nFI")
    f.feed("LL\r\nSTOP\nRESET\r")
    for _, command in ipairs({"start", "fill", "stop", "reset"}) do
        equal(f.calls[command], 1)
        contains(table.concat(f.replies), "OK " .. command:upper() .. " " .. command .. "_accepted")
    end
    equal(#f.replies, 5)
end)

test("controller rejection is an ERROR reply and PROBE cannot activate diagnostics", function()
    local f = fixture()
    f.reject = "start"
    assert(require("water_usb").start(f.controller))
    f.feed("START\nPROBE LOOP\n")
    contains(f.replies[2], "ERROR START not_ready")
    contains(f.replies[3], "ERROR unknown_command")
    equal(f.calls.start, 1)
    equal(f.calls.fill, nil)
    equal(f.calls.stop, nil)
end)

test("oversized command suffix is discarded through CRLF before next command", function()
    local f = fixture()
    assert(require("water_usb").start(f.controller))
    f.feed(string.rep(" ", 65))
    contains(f.replies[2], "ERROR command_too_long")
    f.feed("START")
    equal(f.calls.start, nil)
    equal(#f.replies, 2)
    f.feed("\r\nSTATUS\n")
    equal(#f.replies, 3)
    equal(f.calls.status, 1)
end)

test("exactly 64 byte command remains valid", function()
    local f = fixture()
    assert(require("water_usb").start(f.controller))
    f.feed(string.rep(" ", 59) .. "START\n")
    equal(f.calls.start, 1)
    contains(f.replies[2], "OK START")
end)

test("command and STATUS exceptions attempt STOP and return one-line errors", function()
    for _, method in ipairs({"start", "fill", "reset", "status"}) do
        local f = fixture()
        assert(require("water_usb").start(f.controller))
        f.throw_method = method
        f.feed(method:upper() .. "\n")
        equal(f.calls.stop, 1)
        contains(f.replies[2], "ERROR " .. method:upper() .. " command_exception=")
        local _, lines = f.replies[2]:gsub("\n", "")
        equal(lines, 1, "exception messages must not inject protocol lines")
    end
end)

test("STOP exception is contained and cleanup is retried", function()
    local f = fixture()
    assert(require("water_usb").start(f.controller))
    f.throw_method = "stop"
    f.feed("STOP\n")
    equal(f.calls.stop, 2)
    contains(f.replies[2], "ERROR STOP command_exception=")
end)

test("USB write failure attempts STOP even after a successful START", function()
    for _, failure in ipairs({"write_error", "write_false"}) do
        local f = fixture()
        assert(require("water_usb").start(f.controller))
        f[failure] = true
        f.feed("START\n")
        equal(f.calls.start, 1)
        equal(f.calls.stop, 1)
    end
end)

test("USB read exception attempts STOP and does not escape receive", function()
    local f = fixture()
    assert(require("water_usb").start(f.controller))
    f.read_error = true
    f.receive()
    equal(f.calls.stop, 1)
    contains(f.replies[2], "ERROR USB read_failed")
end)

test("boot prints immediately and every 5 seconds without starting outputs", function()
    local f = fixture()
    f.boot()
    equal(PROJECT, "water_auto_exchange")
    equal(VERSION, "0.5.3")
    equal(f.sys_init[1], 0)
    equal(f.sys_init[2], 0)
    equal(f.sys_run, true)
    equal(f.trace, true)
    equal(f.led_gpio, nil, "source without networking must not initialize the LED")
    contains(table.concat(f.logs), "WATER NET disabled")
    equal(f.calls.init, 1)
    equal(f.calls.start, nil)
    equal(f.calls.fill, nil)
    equal(f.gpio_calls, 0)
    contains(table.concat(f.logs), "WATER STATUS project=water_auto_exchange version=0.5.3")
    equal(#f.timers, 1)
    equal(f.timers[1].ms, 5000)
    local replies, status_calls = #f.replies, f.calls.status
    f.write_error = true
    f.timers[1].callback()
    equal(#f.replies, replies, "periodic trace must not write USB")
    equal(f.calls.status, status_calls + 1)
    equal(f.calls.stop, nil, "periodic status does not mutate the controller")
end)

test("USB startup failures never initialize controller but still print status", function()
    for _, failure in ipairs({"missing", "setup_error", "setup_false", "on_error", "on_false", "write_error"}) do
        local f = fixture()
        if failure == "missing" then _G.uart = nil else f[failure] = true end
        f.boot()
        equal(f.calls.init, nil)
        equal(f.calls.start, nil)
        equal(f.calls.fill, nil)
        equal(f.gpio_calls, 0)
        equal(f.sys_run, true)
        contains(table.concat(f.logs), "WATER usb_unavailable")
        contains(table.concat(f.logs), "WATER STATUS")
        equal(f.timers[1].ms, 5000)
        if f.receive then
            f.feed("START\n")
            equal(f.calls.start, nil, "failed USB initialization leaves handler inactive")
        end
    end
end)

test("controller initialization exception attempts STOP and keeps USB available", function()
    local f = fixture()
    f.throw_method = "init"
    f.boot()
    equal(f.calls.stop, 1)
    contains(table.concat(f.logs), "WATER init_error")
    f.feed("STOP\n")
    equal(f.calls.stop, 2)
    equal(f.sys_run, true)
end)

test("default real configuration boots unconfigured without any GPIO or scan", function()
    local f = fixture(true)
    f.boot()
    equal(f.gpio_calls, 0)
    contains(table.concat(f.logs), "mapping_not_confirmed")
    contains(table.concat(f.logs), "fill=0 drain=0 outputs_known=0")
    f.feed("STATUS\nSTART\nFILL\nSTOP\nRESET\nPROBE LOOP\n")
    local replies = table.concat(f.replies)
    contains(replies, "ERROR START mapping_not_confirmed")
    contains(replies, "ERROR FILL mapping_not_confirmed")
    contains(replies, "ERROR unknown_command")
    contains(replies, "fill=0 drain=0 outputs_known=0")
    equal(f.gpio_calls, 0)
    equal(#f.timers, 1, "only trace heartbeat is scheduled when unconfigured")
end)

test("network starts after USB and controller initialization", function()
    local f = fixture()
    package.loaded.water_network_config = {enabled = true}
    package.loaded.water_network = {start = function(controller)
        equal(controller, f.controller)
        equal(f.calls.init, 1)
        assert(f.receive)
        f.network_started = true
        return true
    end}
    f.boot()
    assert(f.network_started and f.sys_run)
    equal(f.led_gpio, 12)
    contains(table.concat(f.logs), "WATER NET started transport=wss")
end)

test("LED failure or missing pin does not claim the library default or block networking", function()
    for _, cause in ipairs({"setup", "missing_pin"}) do
        local f = fixture()
        if cause == "setup" then f.led_failure = true else _G.pio.P0_12 = nil end
        package.loaded.water_network_config = {enabled = true}
        package.loaded.water_network = {start = function() f.network_started = true; return true end}
        f.boot()
        assert(f.network_started and f.sys_run)
        equal(f.led_gpio, nil)
        contains(table.concat(f.logs), "setup_failed")
        equal(f.gpio_calls, 0)
    end
end)

test("network startup failure keeps USB STOP available", function()
    local f = fixture()
    package.loaded.water_network_config = {enabled = true}
    package.loaded.water_network = {start = function() error("network unavailable") end}
    f.boot()
    assert(f.sys_run and f.calls.stop == 1)
    f.feed("STOP\n")
    equal(f.calls.stop, 2)
end)

test("USB startup failure cannot start remote control", function()
    local f = fixture()
    f.setup_false = true
    package.loaded.water_network_config = {enabled = true}
    package.loaded.water_network = {start = function() error("must not start") end}
    f.boot()
    equal(f.calls.init, nil)
    contains(table.concat(f.logs), "WATER usb_unavailable")
    assert(not table.concat(f.logs):find("WATER NET",1,true))
end)

local failures = {}
for _, item in ipairs(tests) do
    local ok, err = xpcall(item[2], debug.traceback)
    if ok then print("PASS " .. item[1])
    else failures[#failures + 1] = item[1] .. ": " .. tostring(err); print("FAIL " .. failures[#failures]) end
end
print(string.format("Result: %d/%d passed", #tests - #failures, #tests))
if #failures > 0 then error(table.concat(failures, "\n"), 0) end
