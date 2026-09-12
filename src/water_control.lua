-- LuatOS-Air / Lua 5.1 hardware adapter. Loading/constructing performs no I/O.
local cycle = require "water_cycle"
local M = {}
local fixed_gpio = {}
-- GPIO12 is reserved for this board's confirmed network indicator.
for _, id in ipairs({5,9,10,11,13,14,15,17,18,19,22,23}) do fixed_gpio[id] = true end

local function copy(value)
    if type(value) ~= "table" then return value end
    local result = {}
    for k, v in pairs(value) do result[k] = copy(v) end
    return result
end
local function level(value) return value == 0 or value == 1 end
local function clean(value) return tostring(value):gsub("%s+", "_") end
local function checked(name, fn, ...)
    if fn(...) == false then error(name .. "_failed", 0) end
end

function M.validate(config)
    if type(config) ~= "table" then return false, "missing_config" end
    if config.mapping_confirmed ~= true then return false, "mapping_not_confirmed" end
    if config.wiring_confirmed ~= true then return false, "wiring_not_confirmed" end
    if config.enabled ~= true then return false, "disabled" end
    if type(config.poll_ms) ~= "number" or config.poll_ms ~= math.floor(config.poll_ms)
        or config.poll_ms < 20 or config.poll_ms > 1000 then return false, "invalid_poll_ms" end
    if type(config.outputs) ~= "table" or type(config.inputs) ~= "table" then
        return false, "missing_io_config"
    end
    local used = {}
    local function pin(item, name)
        if type(item) ~= "table" or not fixed_gpio[item.gpio] then return false, "invalid_fixed_gpio_" .. name end
        if used[item.gpio] then return false, "duplicate_gpio_" .. name end
        used[item.gpio] = true
        return true
    end
    for _, name in ipairs({"fill", "drain"}) do
        local item = config.outputs[name]
        local ok, reason = pin(item, name)
        if not ok then return ok, reason end
        if not level(item.on_level) or not level(item.off_level) or item.on_level == item.off_level then
            return false, "invalid_output_levels_" .. name
        end
    end
    if type(config.inputs.overflow) ~= "table" or type(config.inputs.overflow.enabled) ~= "boolean" then
        return false, "invalid_overflow_config"
    end
    for _, name in ipairs({"need_fill", "overflow"}) do
        local item = config.inputs[name]
        if name ~= "overflow" or item.enabled then
            local ok, reason = pin(item, name)
            if not ok then return ok, reason end
            if not level(item.active_level) then return false, "invalid_input_level_" .. name end
            if item.pull ~= "UP" and item.pull ~= "DOWN" and item.pull ~= "NONE" then
                return false, "invalid_input_pull_" .. name
            end
        end
    end
    local ok, err = pcall(cycle.new, config.timing)
    if not ok then return false, "invalid_timing_" .. clean(err) end
    return true, "ready"
end

function M.new(config, dependencies)
    local cfg, deps = copy(config), dependencies or {}
    local valid, config_reason = M.validate(cfg)
    local engine = cycle.new(valid and cfg.timing or nil)
    local self = {}
    local initialized, running = false, false
    local sys, pins, pin_api, tick_fn, pio_api
    local owned = {}
    local actual = { fill = false, drain = false }
    local uncertain = { fill = false, drain = false }
    local io_error
    local timer_id, timer_callback, generation = nil, nil, 0
    local previous_tick, elapsed_ms = nil, 0
    local schedule, fault, poll
    local last_state

    local function emit_status()
        local s = self.status()
        local signature = s.state .. ":" .. s.reason
        if signature == last_state then return end
        last_state = signature
        -- Logging failure must never bypass output cleanup or stop the poller.
        pcall(deps.emit or print, "WATER state=" .. s.state .. " reason=" .. clean(s.reason)
            .. " fill=" .. (s.fill and "1" or "0") .. " drain=" .. (s.drain and "1" or "0")
            .. " outputs_known=" .. (s.outputs_known and "1" or "0"))
    end

    local function now()
        local raw = tick_fn()
        assert(type(raw) == "number" and raw == raw and math.abs(raw) < math.huge, "invalid_clock")
        -- Air724UG rtos.tick() counts 5 ms ticks. The legacy library's
        -- os.clockms() implementation using /16 is incompatible here.
        -- https://doc.openluat.com/wiki/21?wiki_page_id=2247
        -- Normalize signed/unsigned 32-bit ticks and accumulate across rollover.
        local current = raw % 4294967296
        if previous_tick ~= nil then
            local delta = (current - previous_tick) % 4294967296
            assert(delta < 2147483648, "clock_discontinuity")
            elapsed_ms = elapsed_ms + delta * 5
        end
        previous_tick = current
        return math.floor(elapsed_ms)
    end

    local function off_all()
        local errors = {}
        for _, name in ipairs({"fill", "drain"}) do
            if owned[name] then
                local item = cfg.outputs[name]
                local ok, err = pcall(checked, "off_" .. name, pin_api.setval, item.off_level, item.gpio)
                if ok then
                    actual[name], uncertain[name] = false, false
                else
                    uncertain[name] = true
                    errors[#errors + 1] = clean(err)
                end
            end
        end
        return #errors == 0, table.concat(errors, ";")
    end

    local function cancel()
        generation = generation + 1
        local pending = timer_id or timer_callback
        timer_id, timer_callback = nil, nil
        if pending then return pcall(checked, "timer_stop", sys.timerStop, pending) end
        return true
    end

    fault = function(reason)
        engine:fault(clean(reason))
        local ok, err = off_all()
        if not ok then io_error = "off_failed:" .. err end
        emit_status()
        return false, self.status().reason
    end

    local function apply(token)
        local s = engine:status()
        assert(not (s.fill and s.drain), "output_interlock")
        if s.fill == actual.fill and s.drain == actual.drain
            and not uncertain.fill and not uncertain.drain then return end
        local ok, err = off_all()
        if not ok then error("output_off_failed:" .. err, 0) end
        if token ~= generation then return end
        local name = s.fill and "fill" or (s.drain and "drain" or nil)
        if name then
            local item = cfg.outputs[name]
            -- Mark uncertainty before a possibly partial ON write; faults retry both OFFs.
            actual[name], uncertain[name] = true, true
            checked("on_" .. name, pin_api.setval, item.on_level, item.gpio)
            uncertain[name] = false
        end
    end

    local function read(name)
        local item = cfg.inputs[name]
        local value = pin_api.getval(item.gpio)
        assert(level(value), "invalid_sensor_" .. name)
        return value == item.active_level
    end

    local function sample()
        local ok, t, need_fill, overflow = pcall(function()
            return now(), read("need_fill"), cfg.inputs.overflow.enabled and read("overflow") or false
        end)
        if not ok then
            -- A recovered input must complete debounce again before RESET.
            engine:input_fault(clean(t))
            error(t, 0)
        end
        engine:update(t, need_fill, overflow)
        return t
    end

    schedule = function()
        local token = generation
        local callback
        callback = function()
            if not running or token ~= generation or timer_callback ~= callback then return end
            timer_id, timer_callback = nil, nil
            poll()
        end
        timer_callback = callback -- Also retain partially registered timers on failure.
        local id = sys.timerStart(callback, cfg.poll_ms)
        assert(type(id) == "number" and id > 0, "timer_start_failed")
        timer_id = id
    end

    poll = function()
        local token = generation
        local ok, err = pcall(function()
            sample()
            apply(token)
        end)
        if not ok then fault(err) end
        emit_status()
        if running and token == generation then
            local scheduled, schedule_error = pcall(schedule)
            if not scheduled then
                running = false
                cancel()
                fault(schedule_error)
            end
        end
    end

    function self.init()
        if initialized then return false, "already_initialized" end
        if not valid then return false, config_reason end
        local ok, err = pcall(function()
            sys = deps.sys
            if not sys then
                sys = require "sys"
            end
            pins = deps.pins
            if not pins then
                pins = require "pins"
            end
            pio_api = deps.pio or pio
            pin_api = pio_api and pio_api.pin
            tick_fn = deps.tick or (rtos and rtos.tick)
            assert(type(sys.timerStart) == "function" and type(sys.timerStop) == "function", "timer_api_unavailable")
            assert(type(pins.setup) == "function" and pin_api and type(pin_api.setval) == "function"
                and type(pin_api.getval) == "function" and type(tick_fn) == "function", "io_api_unavailable")
            for _, name in ipairs({"fill", "drain"}) do
                owned[name] = true
                uncertain[name] = true
                local item = cfg.outputs[name]
                assert(type(pins.setup(item.gpio, item.off_level)) == "function", "setup_" .. name .. "_failed")
                uncertain[name] = false
            end
            for _, name in ipairs({"need_fill", "overflow"}) do
                local item = cfg.inputs[name]
                if name ~= "overflow" or item.enabled then
                    local pulls = { UP = "PULLUP", DOWN = "PULLDOWN", NONE = "NOPULL" }
                    local pull = pio_api[pulls[item.pull]]
                    assert(pull ~= nil, "pull_api_unavailable")
                    assert(type(pins.setup(item.gpio, nil, pull)) == "function", "setup_" .. name .. "_failed")
                end
            end
            initialized, running = true, true
            sample()
            apply(generation)
            schedule()
        end)
        if not ok then
            running = false
            cancel()
            return fault("init:" .. clean(err))
        end
        emit_status()
        return true, "initialized"
    end

    local function command(method)
        if not valid then return false, config_reason end
        if not initialized then return false, "not_initialized" end
        if not running then return false, "poller_stopped_restart_required" end
        local accepted, reason
        local token = generation
        local ok, err = pcall(function()
            local t = sample() -- Recheck raw overflow even between periodic polls.
            accepted, reason = engine[method](engine, t)
            apply(token)
            if method == "reset" and accepted then io_error = nil end
        end)
        if not ok then return fault(err) end
        emit_status()
        return accepted, reason
    end

    function self.start() return command("start") end
    function self.fill() return command("start_fill") end
    function self.drain() return command("start_drain") end
    function self.reset() return command("reset") end

    function self.stop()
        local timer_ok, timer_error = cancel()
        engine:stop()
        local outputs_ok, outputs_error = off_all()
        if not timer_ok or not outputs_ok then
            running = false
            return fault("stop:" .. clean(timer_error or "") .. ";" .. outputs_error)
        end
        -- Continue input monitoring after STOP, with a fresh callback generation.
        if running then
            local ok, err = pcall(schedule)
            if not ok then
                running = false
                cancel()
                return fault(err)
            end
        end
        emit_status()
        return true, "stopped"
    end

    function self.status()
        local s = engine:status()
        if not valid then s.state, s.reason = "UNCONFIGURED", config_reason end
        if io_error then s.reason = s.reason .. ";" .. io_error end
        s.ready = initialized and running and s.state ~= "FAULT" and s.inputs_ready == true
        s.overflow_protection = valid and cfg.inputs.overflow.enabled or false
        s.outputs_known = initialized and not uncertain.fill and not uncertain.drain
        s.fill, s.drain = actual.fill, actual.drain
        return s
    end

    return self
end

return M
