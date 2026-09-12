-- Fixed line protocol on the LuatOS-Air USB virtual port (0x81).
local M = {}

local function token(value)
    return tostring(value == nil and "unknown" or value):gsub("[%s%c]", "_"):sub(1, 160)
end

local function flag(value)
    return value == true and "1" or "0"
end

-- Formatting is shared with trace logging; it never reads or changes GPIO.
function M.format_status(state)
    return "project=" .. token(PROJECT)
        .. " version=" .. token(VERSION)
        .. " state=" .. token(state.state)
        .. " reason=" .. token(state.reason)
        .. " ready=" .. flag(state.ready)
        .. " fill=" .. flag(state.fill)
        .. " drain=" .. flag(state.drain)
        .. " outputs_known=" .. flag(state.outputs_known)
        .. " need_fill=" .. (state.need_fill == nil and "unknown" or flag(state.need_fill))
        .. " overflow=" .. flag(state.overflow)
        .. " cycle=" .. token(state.cycle or 0)
        .. " overflow_protection=" .. flag(state.overflow_protection)
        .. " control_mode=" .. token(state.control_mode or "automatic")
end

function M.start(controller)
    if not uart or uart.USB ~= 0x81 then return false, "usb_uart_unavailable" end
    local pending, discarding, active = "", false, false

    local function stop_after_error()
        -- Logging and response failures must not bypass the attempt to stop.
        local ok, stopped, reason = pcall(controller.stop)
        if not ok or stopped ~= true then
            print("WATER USB stop_error", token(ok and reason or stopped))
        end
    end

    local function reply(message)
        local ok, result = pcall(uart.write, uart.USB, message .. "\r\n")
        if not ok or result == false then
            stop_after_error()
            print("WATER USB reply_error", token(result))
            return false
        end
        return true
    end

    local function execute(command)
        if command == "STATUS" then
            reply("OK STATUS " .. M.format_status(controller.status()))
        elseif command == "START" or command == "FILL" or command == "DRAIN" or command == "STOP" or command == "RESET"
            or command == "FILL_OFF" or command == "DRAIN_OFF" then
            local ok, reason = controller[command:lower()]()
            reply((ok == true and "OK " or "ERROR ") .. command .. " " .. token(reason))
        else
            reply("ERROR unknown_command; use STATUS, START, FILL, DRAIN, FILL_OFF, DRAIN_OFF, STOP or RESET")
        end
    end

    local function handle(line)
        if not active then return end
        local command = line:match("^%s*(.-)%s*$"):upper()
        if command == "" then return end
        local ok, reason = pcall(execute, command)
        if not ok then
            stop_after_error()
            reply("ERROR " .. token(command) .. " command_exception=" .. token(reason))
        end
    end

    local function receive()
        if not active then return end
        while true do
            local read_ok, chunk = pcall(uart.read, uart.USB, "*l", 0)
            if not read_ok or (chunk ~= nil and type(chunk) ~= "string") then
                pending, discarding = "", false
                stop_after_error()
                reply("ERROR USB read_failed")
                return
            end
            if not chunk or #chunk == 0 then break end
            -- Preserve fragments, and discard the entire oversized line.
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

    local ok, result = pcall(uart.setup, uart.USB, 0, 0, uart.PAR_NONE, uart.STOP_1)
    if not ok or result == false then return false, "usb_setup_failed" end
    ok, result = pcall(uart.on, uart.USB, "receive", receive)
    if not ok or result == false then return false, "usb_receive_failed" end
    if not reply("READY " .. token(PROJECT) .. " " .. token(VERSION) .. "; send STATUS") then
        return false, "usb_reply_failed"
    end
    active = true
    return true, "water_usb_ready", handle
end

return M
