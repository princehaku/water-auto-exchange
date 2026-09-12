-- Pure Lua 5.1 controller. This module never reads or writes physical GPIO.
-- need_fill is one hysteretic relay signal: true below B until water reaches C.
local M = {}
local methods = {}
local defaults = {
    debounce_ms = 500,
    switch_delay_ms = 1000,
    drain_timeout_ms = 120000,
    fill_timeout_ms = 120000
}

local function integer(value, minimum, maximum)
    return type(value) == "number" and value >= minimum and value <= maximum
        and value == math.floor(value)
end

local function outputs_off(self)
    self.fill, self.drain = false, false
end

local function invalidate_samples(self)
    self.need_fill, self.candidate, self.candidate_since = nil, nil, nil
    self.clear_since, self.inputs_ready = nil, false
end

function methods:fault(reason)
    outputs_off(self)
    self.drain_only = false
    if self.state ~= "FAULT" then
        self.state = "FAULT"
        self.reason = type(reason) == "string" and reason ~= "" and reason or "external_fault"
    end
    return false, self.reason
end

function methods:input_fault(reason)
    -- A failed read must not leave an old stable sample available to RESET.
    invalidate_samples(self)
    return self:fault(reason)
end

local function check_time(self, now)
    -- Callers must supply an unwrapped monotonic clock, not wall-clock time.
    if not integer(now, 0, 9007199254740991) then
        invalidate_samples(self)
        return self:fault("invalid_time")
    end
    if self.last_now ~= nil and now < self.last_now then
        invalidate_samples(self)
        return self:fault("time_reversed")
    end
    self.last_now = now
    return true
end

local function enter(self, state, reason, now)
    outputs_off(self)
    self.state, self.reason, self.phase_since = state, reason, now
    if state == "DRAINING" then self.drain = true end
    if state == "FILLING" then self.fill = true end
end

function methods:update(now, need_fill, overflow)
    if not check_time(self, now) then return false, self.reason end

    -- Overflow assertion never waits for the normal relay debounce.
    if overflow == true then
        self.overflow = true
        self:fault("overflow")
    end
    if (self.mode ~= "manual" and type(need_fill) ~= "boolean") or type(overflow) ~= "boolean" then
        invalidate_samples(self)
        return self:fault("invalid_input")
    end

    self.overflow = overflow
    if overflow then
        self.clear_since = nil
    elseif self.clear_since == nil then
        self.clear_since = now
    end
    if self.mode == "manual" then
        -- No synthetic water level: only optional overflow and output deadlines.
        self.need_fill = nil
        self.inputs_ready = not overflow and self.clear_since ~= nil
            and now - self.clear_since >= self.settings.debounce_ms
        if self.state == "FAULT" then return false, self.reason end
        if self.state == "DRAINING" and now - self.phase_since >= self.settings.drain_timeout_ms then
            return self:fault("drain_timeout")
        elseif self.state == "FILLING" and now - self.phase_since >= self.settings.fill_timeout_ms then
            return self:fault("fill_timeout")
        elseif self.state == "IDLE" and self.reason == "waiting_for_inputs" and self.inputs_ready then
            self.reason = "ready"
        end
        return true, self.reason
    end
    if self.candidate == nil or need_fill ~= self.candidate then
        self.candidate, self.candidate_since = need_fill, now
    end
    local settled = now - self.candidate_since >= self.settings.debounce_ms
    if settled then self.need_fill = self.candidate end
    self.inputs_ready = settled and not overflow and self.clear_since ~= nil
        and now - self.clear_since >= self.settings.debounce_ms

    -- Continue sampling while faulted so RESET can require a stable safe input.
    if self.state == "FAULT" then return false, self.reason end

    if self.state == "DRAINING" then
        -- A late sample cannot retroactively cancel a hard pumping deadline.
        if now - self.phase_since >= self.settings.drain_timeout_ms then
            return self:fault("drain_timeout")
        end
        if self.need_fill == true then
            if self.drain_only then
                self.drain_only = false
                enter(self, "DONE", "drain_completed", now)
            else
                enter(self, "SETTLING", "settling", now)
            end
        end
    elseif self.state == "SETTLING" then
        if self.need_fill == false then
            return self:fault("sensor_sequence")
        end
        if now - self.phase_since >= self.settings.switch_delay_ms + self.settings.fill_timeout_ms then
            return self:fault("sensor_unstable_timeout")
        end
        if now - self.phase_since >= self.settings.switch_delay_ms and self.inputs_ready then
            enter(self, "FILLING", "filling", now)
        end
    elseif self.state == "FILLING" then
        if now - self.phase_since >= self.settings.fill_timeout_ms then
            return self:fault("fill_timeout")
        end
        if self.need_fill == false then
            enter(self, "DONE", "completed", now)
        end
    elseif self.state == "IDLE" and self.reason == "waiting_for_inputs" and self.inputs_ready then
        self.reason = "ready"
    end
    return true, self.reason
end

local function can_start(self, now)
    if not check_time(self, now) then return false, self.reason end
    if self.state == "FAULT" then return false, "fault_latched" end
    if self.state ~= "IDLE" and self.state ~= "DONE" then return false, "busy" end
    if not self.inputs_ready then return false, "inputs_not_stable" end
    return true
end

function methods:start(now)
    local ok, reason = can_start(self, now)
    if not ok then return false, reason end
    if self.mode == "manual" then return false, "automatic_mode_required" end
    if self.need_fill ~= false then return false, "level_not_ready" end
    self.drain_only = false
    self.cycle = self.cycle + 1
    enter(self, "DRAINING", "draining", now)
    return true, "started"
end

-- Independent drain: stop at B without entering the automatic refill phase.
function methods:start_drain(now)
    local ok, reason = can_start(self, now)
    if not ok then return false, reason end
    if self.mode ~= "manual" and self.need_fill ~= false then return false, "level_not_ready" end
    self.drain_only = true
    self.cycle = self.cycle + 1
    enter(self, "DRAINING", self.mode == "manual" and "manual_draining" or "flushing", now)
    return true, "drain_started"
end

function methods:start_fill(now)
    local ok, reason = can_start(self, now)
    if not ok then return false, reason end
    if self.mode ~= "manual" and self.need_fill ~= true then return false, "fill_not_requested" end
    self.drain_only = false
    self.cycle = self.cycle + 1
    enter(self, "FILLING", self.mode == "manual" and "manual_filling" or "filling", now)
    return true, "fill_started"
end

function methods:stop()
    outputs_off(self)
    self.drain_only = false
    if self.state == "FAULT" then return false, "fault_latched" end
    self.state, self.reason, self.phase_since = "IDLE", "stopped", nil
    return true, "stopped"
end

function methods:reset(now)
    if not check_time(self, now) then return false, self.reason end
    if self.state ~= "FAULT" then return false, "not_faulted" end
    if self.overflow then return false, "overflow_active" end
    if not self.inputs_ready then return false, "inputs_not_stable" end
    self.drain_only = false
    enter(self, "IDLE", "reset", now)
    return true, "reset"
end

function methods:status()
    return {
        state = self.state, reason = self.reason,
        fill = self.fill, drain = self.drain,
        need_fill = self.need_fill, overflow = self.overflow,
        cycle = self.cycle, inputs_ready = self.inputs_ready
    }
end

function M.new(config)
    if config == nil then config = {} end
    assert(type(config) == "table", "invalid_config")
    local mode = config.mode == nil and "automatic" or config.mode
    assert(mode == "manual" or mode == "automatic", "invalid_mode")
    local settings = {}
    for key, value in pairs(defaults) do
        if config[key] ~= nil then value = config[key] end
        local minimum = key == "switch_delay_ms" and 0 or 1
        local maximum = (key == "debounce_ms" or key == "switch_delay_ms") and 60000 or 86400000
        assert(integer(value, minimum, maximum), "invalid_" .. key)
        settings[key] = value
    end
    return setmetatable({
        settings = settings, mode = mode, state = "IDLE", reason = "waiting_for_inputs",
        fill = false, drain = false, overflow = false,
        inputs_ready = false, cycle = 0
    }, { __index = methods })
end

return M
