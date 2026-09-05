-- LuatOS-Air / Lua 5.1. Requiring this module does not configure hardware.
local M = {}
local sys, settings, output_1, output_2, timer_id
local running = false
local state = "STANDBY"
local next_on = true
local tick

local function valid_level(value)
    return value == 0 or value == 1
end

local function valid_integer(value, minimum, maximum)
    return type(value) == "number" and value >= minimum
        and value <= maximum and value == math.floor(value)
end

function M.can_start(config)
    if type(config) ~= "table" then return false, "missing_config" end
    if config.mapping_confirmed ~= true then return false, "mapping_not_confirmed" end
    if config.enabled ~= true then return false, "disabled" end
    if not valid_integer(config.input_1, 0, 2147483647)
        or not valid_integer(config.input_2, 0, 2147483647) then
        return false, "missing_or_invalid_inputs"
    end
    if config.input_1 == config.input_2 then return false, "duplicate_inputs" end
    for _, name in ipairs({ "on_levels", "off_levels" }) do
        local levels = config[name]
        if type(levels) ~= "table" or not valid_level(levels.input_1)
            or not valid_level(levels.input_2) then
            return false, "missing_or_invalid_" .. name
        end
    end
    if config.on_levels.input_1 == config.off_levels.input_1
        and config.on_levels.input_2 == config.off_levels.input_2 then
        return false, "identical_on_and_off_levels"
    end
    if not valid_integer(config.on_ms, 1, 2147483647)
        or not valid_integer(config.off_ms, 1, 2147483647) then
        return false, "invalid_intervals"
    end
    return true, "ready"
end

function M.status()
    return state
end

function M.stop()
    running = false
    local errors = {}
    if timer_id then
        local ok, err = pcall(sys.timerStop, timer_id)
        timer_id = nil
        if not ok then errors[#errors + 1] = tostring(err) end
    end
    -- Try both OFF writes even if one fails. Before start(), both are nil.
    if output_1 then
        local ok, err = pcall(output_1, settings.off_levels.input_1)
        if not ok then errors[#errors + 1] = tostring(err) end
    end
    if output_2 then
        local ok, err = pcall(output_2, settings.off_levels.input_2)
        if not ok then errors[#errors + 1] = tostring(err) end
    end
    state = #errors == 0 and "STANDBY" or "STOP_ERROR"
    if #errors > 0 then return false, table.concat(errors, "; ") end
    return true, "stopped"
end

local function advance()
    local levels = next_on and settings.on_levels or settings.off_levels
    local duration = next_on and settings.on_ms or settings.off_ms
    output_1(levels.input_1)
    output_2(levels.input_2)
    state = next_on and "RUNNING_ON" or "RUNNING_OFF"
    next_on = not next_on
    timer_id = assert(sys.timerStart(tick, duration), "timer_start_failed")
    print("motor_cycle", state, duration)
end

tick = function()
    timer_id = nil
    if not running then return end
    local ok, err = pcall(advance)
    if not ok then
        local stopped, stop_error = M.stop()
        print("motor_cycle error", tostring(err))
        if not stopped then print("motor_cycle stop error", stop_error) end
    end
end

function M.start(config)
    if running then return false, "already_running" end
    local valid, reason = M.can_start(config)
    if not valid then return false, reason end

    -- Snapshot the confirmed states; later config edits cannot change a cycle.
    settings = {
        on_levels = { input_1 = config.on_levels.input_1, input_2 = config.on_levels.input_2 },
        off_levels = { input_1 = config.off_levels.input_1, input_2 = config.off_levels.input_2 },
        on_ms = config.on_ms,
        off_ms = config.off_ms
    }
    output_1, output_2 = nil, nil
    local ok, err = pcall(function()
        sys = require "sys"
        local pins = require "pins"
        output_1 = assert(pins.setup(config.input_1, settings.off_levels.input_1), "input_1_setup_failed")
        output_2 = assert(pins.setup(config.input_2, settings.off_levels.input_2), "input_2_setup_failed")
        running, next_on = true, true
        advance()
    end)
    if not ok then
        local stopped, stop_error = M.stop()
        if not stopped then print("motor_cycle stop error", stop_error) end
        return false, tostring(err)
    end
    return true, "started"
end

return M
