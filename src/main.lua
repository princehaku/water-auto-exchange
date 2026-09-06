PROJECT = "water_auto_exchange"
VERSION = "0.5.0"

local sys = require "sys"
local log = require "log"
-- Default trace does not assign a physical UART or wait for a SIM/network.
log.openTrace(true)
local config = require "water_config"
local water = require "water_control"
local usb = require "water_usb"
local network_config = require "water_network_config"

print(PROJECT, VERSION, "boot", _VERSION)
sys.init(0, 0)
local controller = water.new(config)

-- Install the STOP command before initializing any configured outputs.
local call_ok, ready, reason = pcall(usb.start, controller)
if call_ok and ready then
    local init_ok, initialized, detail = pcall(controller.init)
    if not init_ok then
        pcall(controller.stop)
        print("WATER init_error", tostring(initialized))
    elseif not initialized then
        print("WATER standby", tostring(detail))
    end
else
    print("WATER usb_unavailable", tostring(call_ok and reason or ready))
end

if call_ok and ready and network_config.enabled == true then
    local ok, started, detail = pcall(function()
        local network = require "water_network"
        return network.start(controller, network_config)
    end)
    if not ok or not started then
        pcall(controller.stop)
        print("WATER NET unavailable", ok and tostring(detail) or "initialization_failed")
    end
end

-- Trace remains independent from USB writes; no automatic START/FILL/PROBE.
local function print_status()
    local ok, status = pcall(function()
        return usb.format_status(controller.status())
    end)
    if ok then print("WATER STATUS " .. status)
    else print("WATER status_error", tostring(status)) end
end

print_status()
local timer_ok, timer_id = pcall(sys.timerLoopStart, print_status, 5000)
if not timer_ok or not timer_id or timer_id == 0 then
    print("WATER status_timer_error", tostring(timer_id))
end
sys.run()
