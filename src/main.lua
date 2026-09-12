PROJECT = "water_auto_exchange"
VERSION = "0.7.7"

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
local update_led
local controller = water.new(config, {on_status = function(status)
    if update_led then update_led(status) end
end})

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

if call_ok and ready then
    -- GK21.5PTM rev0.3 routes the marked network indicator to Air724UG
    -- physical pin 53, SPI1_DIN/GPIO12. Never fall back to the library default.
    local led_ok = pcall(function()
        assert(pio and pio.P0_12 ~= nil, "network_led_pin_unavailable")
        local netLed = require "netLed"
        assert(type(netLed.updateBlinkTime) == "function", "network_led_pattern_unavailable")
        -- LuaTask V2.4.4 defaults; keep one LED owner, including during radio changes.
        local idle_patterns = {NULL={0,65535}, FLYMODE={0,65535}, SIMERR={300,5700},
            IDLE={300,3700}, GSM={300,1700}, GPRS={300,700}, SCK={100,100}}
        local previous_active
        update_led = function(status)
            local active = status.outputs_known == true and (status.fill == true or status.drain == true)
            if active == previous_active then return end
            for state, pattern in pairs(idle_patterns) do
                netLed.updateBlinkTime(state, active and 65535 or pattern[1], active and 0 or pattern[2])
            end
            previous_active = active
        end
        netLed.setup(true, pio.P0_12)
        update_led(controller.status())
    end)
    if not led_ok then update_led = nil end
    print("WATER NET LED gpio=12 physical=53", led_ok and "enabled" or "setup_failed")
end

if call_ok and ready and network_config.enabled == true then
    local ok, started, detail = pcall(function()
        local network = require "water_network"
        return network.start(controller, network_config)
    end)
    if not ok or not started then
        pcall(controller.stop)
        print("WATER NET unavailable", ok and tostring(detail) or "initialization_failed")
    else
        print("WATER NET started transport=wss; waiting for PDP/TLS/auth")
    end
elseif network_config.enabled ~= true then
    print("WATER NET disabled; flash the generated build/firmware package for 4G")
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
