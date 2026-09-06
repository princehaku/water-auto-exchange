-- LuatOS-Air / Lua 5.1. Loading this module performs no hardware I/O.
local M = {}
local sequence = {
    -- All three are fixed V_GLOBAL_1V8 GPIOs, configured only in the running app.
    -- GPIO13 is also a boot calibration input: never externally pull it HIGH at power-on.
    -- GPIO22/23 share CP TX/SIM detect functions; pins.setup selects GPIO mode.
    -- Exclude the previous 20 DO2 candidates and the identified GPIO5/12.
    { gpio = 13, physical = 43 },
    { gpio = 22, physical = 7 },
    { gpio = 23, physical = 8 }
}
local candidate_ids = {}
for i, item in ipairs(sequence) do candidate_ids[i] = tostring(item.gpio) end
local candidates = table.concat(candidate_ids, ",")
local hold_ms = 5000
local phases = { "HIGH", "LOW" }
local cycle_ms = #sequence * #phases * hold_ms
local state, index, phase = "IDLE", 0, 0
local gpio, level, sys, pins, pin_api, emit
local timer_id, timer_callback
local generation = 0
local continuous_mode, cycle = false, 0
local advance, fail

local function line_text(value)
    return tostring(value):gsub("[\r\n]", " ")
end

local function report(line)
    print(line)
    if emit then emit(line) end
end

-- The native write/close functions may return no value on success.
-- An explicit false or an exception is a failure; timer/setup have stricter checks.
local function checked(name, fn, ...)
    if fn(...) == false then error(name .. "_failed", 0) end
end

local function attempt(errors, name, fn, ...)
    local ok, err = pcall(checked, name, fn, ...)
    if not ok then errors[#errors + 1] = name .. ": " .. line_text(err) end
    return ok
end

local function release_current(errors)
    if not gpio then return end
    if attempt(errors, "set_low", pin_api.setval, 0, gpio) then level = 0
    else level = nil end
    if attempt(errors, "close", pins.close, gpio) then
        gpio, level = nil, nil
    end
end

local function cleanup()
    generation = generation + 1
    local errors = {}
    -- Invalidate first: even a failed timerStop cannot allow a queued write.
    local pending = timer_id or timer_callback
    timer_id, timer_callback = nil, nil
    if pending then attempt(errors, "timer_stop", sys.timerStop, pending) end
    release_current(errors)
    return errors
end

fail = function(reason)
    state = "ERROR"
    local errors = cleanup()
    local message = line_text(reason)
    if #errors > 0 then message = message .. "; " .. table.concat(errors, "; ") end
    -- Reporting failure must never prevent hardware cleanup or recurse.
    local line = "PROBE state=ERROR reason=" .. message
    pcall(print, line)
    if emit then pcall(emit, line) end
    return false, message
end

local function schedule()
    local token = generation
    local callback
    callback = function()
        if state ~= "RUNNING" or token ~= generation or timer_callback ~= callback then return end
        timer_id, timer_callback = nil, nil
        local ok, err = pcall(advance)
        if not ok then fail(err) end
    end
    -- Keep the callback even if timerStart throws after registering it.
    timer_callback = callback
    local id = sys.timerStart(callback, hold_ms)
    if type(id) ~= "number" or id <= 0 then error("timer_start_failed", 0) end
    timer_id = id
end

local function show_phase()
    local token = generation
    local item = sequence[index]
    report(string.format(
        "PROBE gpio=%d physical=%d level=%d phase=%s index=%d/%d hold_ms=%d domain=%s",
        item.gpio, item.physical, level, phases[phase], index, #sequence, hold_ms,
        "V_GLOBAL_1V8"))
    -- An emitter is allowed to request STOP while handling a progress line.
    if state == "RUNNING" and token == generation then schedule() end
end

local function begin_pin()
    local item = sequence[index]
    gpio, level, phase = item.gpio, nil, 1
    -- Record the pin before setup so partial setup failures are also closed.
    if type(pins.setup(gpio, 0)) ~= "function" then error("setup_failed", 0) end
    checked("setval", pin_api.setval, 1, gpio)
    level = 1
    show_phase()
end

advance = function()
    if phase < #phases then
        phase = phase + 1
        local next_level = 0
        checked("setval", pin_api.setval, next_level, gpio)
        level = next_level
        show_phase()
        return
    end

    local errors = {}
    release_current(errors)
    if #errors > 0 then error(table.concat(errors, "; "), 0) end
    if index < #sequence then
        index = index + 1
        begin_pin()
    else
        -- Invalidate the completed cycle before returning to GPIO13.
        errors = cleanup()
        if #errors > 0 then error(table.concat(errors, "; "), 0) end
        if continuous_mode then
            index, cycle = 1, cycle + 1
            local token = generation
            report("PROBE state=RUNNING event=NEXT_CYCLE cycle=" .. cycle)
            if state == "RUNNING" and token == generation then begin_pin() end
        else
            state = "DONE"
            report(string.format("PROBE state=DONE event=END total=%d duration_ms=%d", #sequence, cycle_ms))
        end
    end
end

function M.status()
    return {
        state = state, gpio = gpio, level = level, index = index,
        total = #sequence, hold_ms = hold_ms, continuous = continuous_mode, cycle = cycle,
        cycle_ms = cycle_ms, candidates = candidates
    }
end

function M.start(callback, continuous)
    if state == "RUNNING" then return false, "already_running" end
    if state == "ERROR" then return false, "error_requires_stop" end
    if callback ~= nil and type(callback) ~= "function" then return false, "invalid_emit" end
    if continuous ~= nil and type(continuous) ~= "boolean" then return false, "invalid_continuous" end
    emit = callback
    state, index, phase = "RUNNING", 0, 0
    continuous_mode, cycle = continuous == true, 1
    generation = generation + 1
    local token = generation
    local ok, err = pcall(function()
        -- LuaTools scans require statements line by line when packaging.
        sys = require "sys"
        pins = require "pins"
        pin_api = pio and pio.pin
        assert(type(sys.timerStart) == "function" and type(sys.timerStop) == "function",
            "timer_api_unavailable")
        assert(type(pins.setup) == "function" and type(pins.close) == "function",
            "pins_api_unavailable")
        assert(pin_api and type(pin_api.setval) == "function", "setval_api_unavailable")
        report(string.format("PROBE state=RUNNING event=START total=%d hold_ms=%d cycle_ms=%d target=DO2 domain=V_GLOBAL_1V8",
            #sequence, hold_ms, cycle_ms)
            .. " continuous=" .. (continuous_mode and "1" or "0"))
        if state == "RUNNING" and token == generation then
            index = 1
            begin_pin()
        end
    end)
    if not ok then return fail(err) end
    if state ~= "RUNNING" then return false, state:lower() end
    return true, "started"
end

function M.stop()
    state = "STOPPED"
    local errors = cleanup()
    if #errors > 0 then
        state = "ERROR"
        local message = table.concat(errors, "; ")
        local line = "PROBE state=ERROR reason=stop: " .. message
        pcall(print, line)
        if emit then pcall(emit, line) end
        return false, message
    end
    local ok, err = pcall(report, "PROBE state=STOPPED event=STOP")
    if not ok then return fail(err) end
    return true, "stopped"
end

return M
