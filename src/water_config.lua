-- Manual water switches; optional automatic mode uses a liquid-level relay.
-- GK21.5PTM: user's tested scan-switch-away sequence is LOW then pins.close.
return {
    -- Manual switches need only confirmed outputs; no level sensor is required.
    -- Set automatic only when the level feedback is installed and verified.
    mode = "manual",
    enabled = true,
    mapping_confirmed = true,
    -- Confirmed for manual board-output control, not pump/flow commissioning.
    wiring_confirmed = true,

    outputs = {
        -- Fill: top DO2 (~12V); drain: paper motor connector (~6.1V).
        -- off_level alone is NOT the validated OFF state: release is required.
        fill = { gpio = 23, on_level = 1, off_level = 0, off_mode = "release" },
        drain = { gpio = 5, on_level = 1, off_level = 0, off_mode = "release" }
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
