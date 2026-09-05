-- TP18/TP29 are unverified PCB test-point labels, not GPIO numbers.
-- Fill these values only after tracing the driver inputs to the module.
return {
    enabled = false,
    mapping_confirmed = false,
    auto_start = false,

    -- Use the verified LuatOS-Air pio constants for the two control inputs.
    input_1 = nil,
    input_2 = nil,

    -- Explicit GPIO levels at the module, including any board inversion.
    -- Confirm that off_levels stops driving the motor before enabling.
    on_levels = { input_1 = nil, input_2 = nil },
    off_levels = { input_1 = nil, input_2 = nil },

    on_ms = 3000,
    off_ms = 3000
}
