-- Fixed line protocol on the LuatOS-Air USB virtual port, not a physical UART.
local M = {}

function M.start(motor, config, probe)
    if not uart or not uart.USB then return false, "usb_uart_unavailable" end
    local pending = ""
    local discarding = false

    local function reply(message)
        uart.write(uart.USB, message .. "\r\n")
    end

    local function probe_status()
        if not probe then return " probe_state=UNAVAILABLE" end
        local state = probe.status()
        return " probe_state=" .. tostring(state.state)
            .. " probe_gpio=" .. tostring(state.gpio or "none")
            .. " probe_level=" .. tostring(state.level == nil and "none" or state.level)
            .. " probe_continuous=" .. (state.continuous == true and "1" or "0")
            .. " probe_cycle=" .. tostring(state.cycle or 0)
            .. " probe_total=" .. tostring(state.total or 0)
            .. " probe_cycle_ms=" .. tostring(state.cycle_ms or 0)
            .. " probe_candidates=" .. tostring(state.candidates or "unknown")
    end

    local function handle(line)
        local command = line:match("^%s*(.-)%s*$"):upper()
        if command == "" then return end
        if command == "STATUS" then
            local ready, reason = motor.can_start(config)
            reply("OK STATUS project=" .. tostring(PROJECT)
                .. " version=" .. tostring(VERSION)
                .. " state=" .. motor.status()
                .. " enabled=" .. (config.enabled == true and "1" or "0")
                .. " mapping_confirmed=" .. (config.mapping_confirmed == true and "1" or "0")
                .. " ready=" .. (ready and "1" or "0")
                .. " reason=" .. reason
                .. " on_ms=" .. tostring(config.on_ms)
                .. " off_ms=" .. tostring(config.off_ms) .. probe_status())
        elseif command == "PROBE" or command == "PROBE LOOP" then
            if not probe then
                reply("ERROR PROBE unavailable")
            elseif motor.status() ~= "STANDBY" then
                reply("ERROR PROBE motor_not_stopped")
            else
                local ok, reason = probe.start(reply, command == "PROBE LOOP")
                reply((ok and "OK PROBE " or "ERROR PROBE ") .. tostring(reason))
            end
        elseif command == "START" or command == "STOP" then
            local ok, reason
            if command == "START" then
                local state = probe and probe.status().state
                if state == "RUNNING" or state == "ERROR" then
                    ok, reason = false, "probe_not_stopped"
                else
                    ok, reason = motor.start(config)
                end
            else
                -- Attempt both cleanups even if one controller reports failure.
                local probe_ok, probe_reason = true, "stopped"
                if probe then probe_ok, probe_reason = probe.stop() end
                ok, reason = motor.stop()
                if not probe_ok then ok, reason = false, "probe_" .. tostring(probe_reason) end
            end
            reply((ok and "OK " or "ERROR ") .. command .. " " .. tostring(reason))
        else
            reply("ERROR unknown_command; use STATUS, START, STOP, PROBE or PROBE LOOP")
        end
    end

    local function receive()
        while true do
            local chunk = uart.read(uart.USB, "*l", 0)
            if not chunk or #chunk == 0 then break end
            -- Reads may be partial. CR, LF and CRLF all terminate commands.
            for index = 1, #chunk do
                local character = chunk:sub(index, index)
                if character == "\r" or character == "\n" then
                    if not discarding then handle(pending) end
                    pending, discarding = "", false
                elseif not discarding then
                    pending = pending .. character
                    if #pending > 64 then
                        pending, discarding = "", true
                        reply("ERROR command_too_long")
                    end
                end
            end
        end
    end

    uart.setup(uart.USB, 0, 0, uart.PAR_NONE, uart.STOP_1)
    uart.on(uart.USB, "receive", receive)
    reply("READY " .. tostring(PROJECT) .. " " .. tostring(VERSION) .. "; send STATUS")
    return true, "usb_control_ready", handle
end

return M
