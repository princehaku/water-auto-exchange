-- Pure Lua 5.1 tests. No hardware modules or serial ports are used.
local root = (arg and arg[1]) or "."
package.path = root .. "/src/?.lua;" .. package.path
local water = require "water_cycle"
local tests = {}
local function test(name, callback) tests[#tests + 1] = { name, callback } end
local function equal(actual, expected, message)
    assert(actual == expected, (message or "unexpected value") .. ": expected "
        .. tostring(expected) .. ", got " .. tostring(actual))
end
local function fixture(changes)
    local config = { debounce_ms = 100, switch_delay_ms = 200,
        drain_timeout_ms = 1000, fill_timeout_ms = 1000 }
    for key, value in pairs(changes or {}) do config[key] = value end
    return water.new(config)
end
local function state(controller, name, fill, drain)
    local s = controller:status()
    equal(s.state, name)
    equal(s.fill, fill == true)
    equal(s.drain, drain == true)
    assert(not (s.fill and s.drain), "fill/drain interlock failed")
    return s
end
local function sample_ready(controller, need_fill)
    assert(controller:update(0, need_fill, false))
    assert(controller:update(100, need_fill, false))
end

test("configuration validates bounds, rejects nonintegers and snapshots values", function()
    assert(water.new())
    for _, value in ipairs({ false, "100", 0 / 0, math.huge, -1, 0.5 }) do
        for _, field in ipairs({ "debounce_ms", "switch_delay_ms", "drain_timeout_ms", "fill_timeout_ms" }) do
            equal(pcall(water.new, { [field] = value }), false, field)
        end
    end
    equal(pcall(water.new, "bad"), false)
    equal(pcall(water.new, { debounce_ms = 0 }), false)
    equal(pcall(water.new, { switch_delay_ms = 60001 }), false)
    equal(pcall(water.new, { fill_timeout_ms = 86400001 }), false)
    local config = { debounce_ms = 100 }
    local c = water.new(config)
    config.debounce_ms = 500
    sample_ready(c, false)
    assert(c:start(100))
end)

test("START waits for actual stable boolean observations, not elapsed command time", function()
    local c = fixture()
    local ok, reason = c:start(0)
    equal(ok, false); equal(reason, "inputs_not_stable")
    c:update(0, false, false)
    equal(c:start(100), false)
    c:update(100, false, false)
    equal(c:status().need_fill, false)
    assert(c:start(100))
    state(c, "DRAINING", false, true)
    equal(c:start(100), false)
end)

test("one complete exchange observes B and C with a closed switching interval", function()
    local c = fixture()
    sample_ready(c, false)
    assert(c:start(100))
    c:update(200, true, false)
    state(c, "DRAINING", false, true)
    c:update(299, true, false)
    state(c, "DRAINING", false, true)
    c:update(300, true, false)
    state(c, "SETTLING")
    c:update(499, true, false)
    state(c, "SETTLING")
    c:update(500, true, false)
    state(c, "FILLING", true)
    c:update(600, false, false)
    state(c, "FILLING", true)
    c:update(700, false, false)
    local s = state(c, "DONE")
    equal(s.cycle, 1); equal(s.reason, "completed")
    c:update(100000, false, false)
    state(c, "DONE")
    assert(c:start(100000))
    equal(c:status().cycle, 2)
end)

test("low initial state rejects drain and allows one explicit fill", function()
    local c = fixture()
    sample_ready(c, true)
    local ok, reason = c:start(100)
    equal(ok, false); equal(reason, "level_not_ready")
    state(c, "IDLE")
    assert(c:start_fill(100))
    state(c, "FILLING", true)
    c:update(200, false, false)
    c:update(300, false, false)
    state(c, "DONE")
    ok, reason = c:start_fill(300)
    equal(ok, false); equal(reason, "fill_not_requested")
    equal(c:status().cycle, 1)
end)

test("relay chatter never resets a drain deadline", function()
    local c = fixture()
    sample_ready(c, false)
    assert(c:start(100))
    for t = 200, 1050, 50 do
        c:update(t, (t / 50) % 2 == 0, false)
        state(c, "DRAINING", false, true)
    end
    c:update(1100, true, false)
    equal(state(c, "FAULT").reason, "drain_timeout")
end)

test("fill deadline is hard and late full readings cannot erase a timeout", function()
    local c = fixture()
    sample_ready(c, true)
    assert(c:start_fill(100))
    c:update(1000, false, false)
    state(c, "FILLING", true)
    c:update(1100, false, false)
    equal(state(c, "FAULT").reason, "fill_timeout")
end)

test("long delayed polling fails closed during drain and fill", function()
    for _, filling in ipairs({ false, true }) do
        local c = fixture()
        sample_ready(c, filling)
        if filling then assert(c:start_fill(100)) else assert(c:start(100)) end
        c:update(100000, not filling, false)
        equal(state(c, "FAULT").reason, filling and "fill_timeout" or "drain_timeout")
    end
end)

test("overflow assertion immediately latches off in every state", function()
    for _, target in ipairs({ "IDLE", "DRAINING", "SETTLING", "FILLING", "DONE" }) do
        local c = fixture()
        sample_ready(c, false)
        if target ~= "IDLE" then assert(c:start(100)) end
        if target == "SETTLING" or target == "FILLING" or target == "DONE" then
            c:update(200, true, false); c:update(300, true, false)
        end
        if target == "FILLING" or target == "DONE" then c:update(500, true, false) end
        if target == "DONE" then c:update(600, false, false); c:update(700, false, false) end
        c:update(800, false, true)
        local s = state(c, "FAULT")
        equal(s.reason, "overflow"); equal(s.overflow, true)
        equal(c:start(800), false)
        equal(c:stop(), false)
        equal(state(c, "FAULT").reason, "overflow")
    end
end)

test("RESET needs stable overflow clear and never restarts outputs", function()
    local c = fixture()
    sample_ready(c, false)
    c:update(200, false, true)
    local ok, reason = c:reset(200)
    equal(ok, false); equal(reason, "overflow_active")
    c:update(300, false, false)
    ok, reason = c:reset(399)
    equal(ok, false); equal(reason, "inputs_not_stable")
    c:update(400, false, false)
    assert(c:reset(400))
    state(c, "IDLE")
    equal(c:reset(400), false)
    assert(c:start(400))
end)

test("nil and numeric GPIO readings are invalid, including Lua-truthy zero", function()
    for _, input in ipairs({ 0, 1, "false", {}, function() end }) do
        local c = fixture(); sample_ready(c, false); assert(c:start(100))
        c:update(200, input, false)
        equal(state(c, "FAULT").reason, "invalid_input")
        equal(c:status().need_fill, nil)
        equal(c:reset(200), false)
    end
    local c = fixture(); sample_ready(c, true); assert(c:start_fill(100))
    c:update(200, nil, false)
    equal(state(c, "FAULT").reason, "invalid_input")
    c = fixture(); c:update(0, false, 0)
    equal(state(c, "FAULT").reason, "invalid_input")
    c = fixture(); c:update(0, false, nil)
    equal(state(c, "FAULT").reason, "invalid_input")
end)

test("time reversal and malformed timestamps fault even on command paths", function()
    for _, command in ipairs({ "update", "start", "start_fill", "reset" }) do
        local c = fixture(); sample_ready(c, false)
        c[command](c, 99, false, false)
        equal(state(c, "FAULT").reason, "time_reversed")
        c:update(100, false, false)
        equal(c:reset(100), false)
        c:update(200, false, false)
        assert(c:reset(200))
    end
    for _, now in ipairs({ -1, 0.1, math.huge, 0 / 0, "200", false }) do
        local c = fixture(); sample_ready(c, false)
        c:update(now, false, false)
        equal(state(c, "FAULT").reason, "invalid_time")
    end
    local c = fixture(); c:update(nil, false, false)
    equal(state(c, "FAULT").reason, "invalid_time")
end)

test("unexpected full indication during settling stops the sequence", function()
    local c = fixture(); sample_ready(c, false); assert(c:start(100))
    c:update(200, true, false); c:update(300, true, false)
    c:update(350, false, false); c:update(450, false, false)
    equal(state(c, "FAULT").reason, "sensor_sequence")
end)

test("settling chatter keeps both outputs off and has a bounded deadline", function()
    local c = fixture(); sample_ready(c, false); assert(c:start(100))
    c:update(200, true, false); c:update(300, true, false)
    for t = 350, 1450, 50 do
        c:update(t, (t / 50) % 2 == 0, false)
        state(c, "SETTLING")
    end
    c:update(1500, true, false)
    equal(state(c, "FAULT").reason, "sensor_unstable_timeout")
end)

test("STOP cancels active work and future samples never restart it", function()
    for _, filling in ipairs({ false, true }) do
        local c = fixture(); sample_ready(c, filling)
        if filling then assert(c:start_fill(100)) else assert(c:start(100)) end
        assert(c:stop()); state(c, "IDLE")
        c:update(200, not filling, false); c:update(100000, not filling, false)
        state(c, "IDLE")
        equal(c:status().cycle, 1)
    end
end)

test("FAULT preserves the first cause through STOP and subsequent errors", function()
    local c = fixture(); sample_ready(c, false); assert(c:start(100))
    c:fault("output_write_failed")
    equal(c:stop(), false)
    c:fault("second_error")
    c:update(200, false, true)
    equal(state(c, "FAULT").reason, "output_write_failed")
end)

test("input faults invalidate old samples and require a fresh full debounce before RESET", function()
    local c = fixture(); sample_ready(c, false); assert(c:start(100))
    local ok, reason = c:input_fault("input_read_failed")
    equal(ok, false); equal(reason, "input_read_failed")
    local s = state(c, "FAULT")
    equal(s.need_fill, nil); equal(s.inputs_ready, false)
    ok, reason = c:reset(100)
    equal(ok, false); equal(reason, "inputs_not_stable")
    c:update(1000, false, false)
    equal(c:reset(1000), false, "time since stale sample must not count")
    c:update(1099, false, false)
    equal(c:reset(1099), false)
    c:input_fault("second_read_failure")
    equal(state(c, "FAULT").reason, "input_read_failed")
    c:update(1100, false, false)
    equal(c:reset(1100), false, "each read failure restarts sample stabilization")
    c:update(1199, false, false)
    equal(c:reset(1199), false)
    c:update(1200, false, false)
    assert(c:reset(1200))
    state(c, "IDLE")
end)

test("status snapshots cannot mutate the controller", function()
    local c = fixture(); sample_ready(c, false)
    local s = c:status()
    s.state, s.need_fill, s.cycle, s.fill = "FILLING", true, 100, true
    state(c, "IDLE")
    equal(c:status().cycle, 0)
    assert(c:start(100))
end)

test("independent drain stops at debounced low level and never refills", function()
    local c = fixture(); sample_ready(c, false)
    assert(c:start_drain(100)); state(c, "DRAINING", false, true)
    equal(c:status().reason, "flushing")
    c:update(200, true, false); c:update(299, true, false)
    state(c, "DRAINING", false, true)
    c:update(300, true, false)
    equal(state(c, "DONE").reason, "drain_completed")
    c:update(100000, true, false); state(c, "DONE")
    assert(c:start_fill(100000)); state(c, "FILLING", true)
end)

test("independent drain rejects low, unstable, busy and faulted inputs", function()
    local c = fixture()
    equal(c:start_drain(0), false)
    sample_ready(c, true); equal(c:start_drain(100), false)
    assert(c:start_fill(100)); equal(c:start_drain(100), false)
    c:fault("overflow"); equal(c:start_drain(100), false)
    state(c, "FAULT")
end)

test("independent drain timeout, overflow, STOP and bad inputs turn outputs off", function()
    for _, action in ipairs({"timeout", "overflow", "stop", "bad_input"}) do
        local c = fixture(); sample_ready(c, false); assert(c:start_drain(100))
        if action == "stop" then
            assert(c:stop()); c:update(300, true, false); c:update(10000, true, false)
            state(c, "IDLE")
        else
            if action == "timeout" then c:update(1100, true, false)
            elseif action == "overflow" then c:update(200, false, true)
            else c:update(200, nil, false) end
            state(c, "FAULT")
        end
    end
end)

test("a full exchange after independent drain still includes refilling", function()
    local c = fixture(); sample_ready(c, false); assert(c:start_drain(100))
    assert(c:stop()); assert(c:start(100))
    c:update(200, true, false); c:update(300, true, false)
    state(c, "SETTLING"); c:update(500, true, false); state(c, "FILLING", true)
end)

local failures = 0
for _, item in ipairs(tests) do
    local ok, reason = pcall(item[2])
    if ok then
        print("PASS " .. item[1])
    else
        failures = failures + 1
        print("FAIL " .. item[1] .. ": " .. tostring(reason))
    end
end
assert(failures == 0, tostring(failures) .. " water-cycle tests failed")
print("water_cycle_test: " .. #tests .. "/" .. #tests .. " passed")
