PROJECT = "gk21_motor_test"
VERSION = "0.2.5"

local sys = require "sys"
local log = require "log"
-- Keep LuaTools trace on without assigning a physical UART to logging.
log.openTrace(true)
local config = require "motor_config"
local motor = require "motor_cycle"
local probe = require "gpio_probe"

print(PROJECT, VERSION, "boot", _VERSION)
local ready, reason = motor.can_start(config)
print("motor_cycle", "STANDBY", reason)

-- Keep USB commands and boot diagnostics on the same interlock/STOP path.
local ok, result, detail, handler = pcall(function()
    return require("usb_control").start(motor, config, probe)
end)
if not ok then print("usb_control error", tostring(result))
elseif not result then print("usb_control unavailable", tostring(detail)) end

if config.auto_start == true and ready then
    local started, start_error = motor.start(config)
    if not started then print("motor_cycle start error", start_error) end
end

sys.init(0, 0)
if ok and result and type(handler) == "function" and config.auto_start ~= true then
    local started, start_error = pcall(handler, "PROBE LOOP")
    if not started then
        pcall(handler, "STOP")
        print("gpio_probe boot error", tostring(start_error))
    end
end
sys.run()
