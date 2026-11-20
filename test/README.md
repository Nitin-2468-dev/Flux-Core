# FluxCore Tiny Tapeout tests

These Cocotb tests target the generated Amaranth wrapper `tt_um_fluxcore`.
They test reset/idle behaviour, exact configuration completion, the registered
PE0-to-PE1 addition pipeline, 32-bit serial result readback, and MAC accumulator
readback.

## Install and run RTL simulation

From the Tiny Tapeout repository root:

```sh
cd test
python -m pip install -r requirements.txt
make -B
```

The default testbench uses Cocotb to generate the clock and drive inputs. The
Verilog `tb.v` intentionally contains no clock generator or stimulus of its own.

## Gate-level test

After Tiny Tapeout hardening has generated and copied `gate_level_netlist.v`:

```sh
make -B GATES=yes
```

The gate-level run uses the same Cocotb tests.

## Current wrapper limitations reflected in the tests

- Configuration is loaded once per reset (32 serial bits total).
- The eight 32-bit operands are loaded once per reset (256 serial bits total).
- `busy` and `done` are currently one-cycle execute pulses, not a multi-cycle
  execution handshake.
- The registered PE-to-PE path needs a second execute pulse for PE1 to consume
  PE0's newly computed value.

The tests do not assume that operand reload or a multi-cycle busy protocol exists.
