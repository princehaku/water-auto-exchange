-- Manual water switches; optional automatic mode uses a liquid-level relay.
-- Set the wiring and measured active/OFF levels before enabling real outputs.
return {
    -- Manual switches need only confirmed outputs; no level sensor is required.
    -- Set automatic only when the level feedback is installed and verified.
    mode = "manual",
    enabled = false,
    mapping_confirmed = false,
    -- Confirm output wiring and OFF behavior. Automatic mode additionally
    -- requires isolated level feedback and no bypass of the fill permission.
    wiring_confirmed = false,

    outputs = {
        fill = { gpio = nil, on_level = nil, off_level = nil },
        drain = { gpio = nil, on_level = nil, off_level = nil }
    },
    inputs = {
        -- Board relay requests fill below B; stays asserted until water reaches C.
        -- This is ONE latched signal, not two independent low/high switches.
        -- Typical isolated COM-to-GND / NO-to-input wiring uses active_level=0
        -- with UP; verify the actual relay logic before setting this value.
        need_fill = { gpio = nil, active_level = nil, pull = "UP" },
        -- Optional extra sensor. This software input is not an independent
        -- hardware shutoff. Use fail-active wiring when the hardware permits it.
        overflow = { enabled = false, gpio = nil, active_level = nil, pull = "UP" }
    },
    poll_ms = 100,
    timing = {
        debounce_ms = 500,
        switch_delay_ms = 1000,
        -- Commission these limits using the actual volume and pump/valve flow.
        drain_timeout_ms = 120000,
        fill_timeout_ms = 120000
    }
}
