"""Cocotb tests for the FluxCore Tiny Tapeout wrapper.

Tested interface:
  ui[0]   serial configuration data
  ui[1]   configuration clock (rising edge detected by the wrapper)
  ui[2]   configuration shift enable
  ui[3]   execute pulse
  ui[4]   serial operand data
  ui[5]   operand data-valid
  ui[6]   read-result pulse
  ui[7]   result source: 0=PE result, 1=PE accumulator
  uio[1:0] PE index

The wrapper accepts one 256-bit operand batch after reset. The tests reset
between workloads because the current RTL does not implement operand reload.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer


CLK_PERIOD_NS = 10

UI_CFG_DATA = 1 << 0
UI_CFG_CLK = 1 << 1
UI_CFG_ENABLE = 1 << 2
UI_EXECUTE = 1 << 3
UI_OPERAND_DATA = 1 << 4
UI_OPERAND_VALID = 1 << 5
UI_READ_RESULT = 1 << 6
UI_READ_ACCUMULATOR = 1 << 7

UO_RESULT_BIT = 1 << 0
UO_BUSY = 1 << 1
UO_CONFIG_DONE = 1 << 2
UO_DONE = 1 << 3
UO_RESULT_VALID = 1 << 4


async def start_and_reset(dut):
    """Start the host-driven clock and apply a synchronous active-low reset."""
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.ena.value = 1
    dut.rst_n.value = 0
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await ClockCycles(dut.clk, 5)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    await Timer(1, unit="ns")


async def tick(dut, ui_value=0, uio_value=None):
    """Drive inputs away from the active edge, then advance one system clock."""
    await FallingEdge(dut.clk)
    dut.ui_in.value = ui_value & 0xFF
    if uio_value is not None:
        dut.uio_in.value = uio_value & 0xFF
    await RisingEdge(dut.clk)
    await Timer(1, unit="ns")


def output(dut):
    return int(dut.uo_out.value)


async def shift_config_bit(dut, bit):
    """Shift one serial config bit using a sampled low-high-low config clock."""
    data = UI_CFG_ENABLE | (UI_CFG_DATA if bit else 0)
    await tick(dut, data)  # ensure sampled low level
    await tick(dut, data | UI_CFG_CLK)  # sampled rising edge performs one shift
    await tick(dut, data)  # return low for the next bit


async def configure_pes(dut, pe3, pe2, pe1, pe0, check_done=True):
    """Load four 8-bit config words; chain order is PE3, PE2, PE1, PE0.

    Each byte is transmitted most-significant bit first.
    """
    for value in (pe3, pe2, pe1, pe0):
        for bit_index in range(7, -1, -1):
            await shift_config_bit(dut, (value >> bit_index) & 1)
    await tick(dut, 0)
    if check_done:
        assert output(dut) & UO_CONFIG_DONE, "config_done did not assert after 32 bits"


async def load_operands(dut, words):
    """Load eight 32-bit words, MSB first, in data_in_0..data_in_7 order."""
    if len(words) != 8:
        raise ValueError("FluxCore wrapper requires exactly eight 32-bit words")
    for word_index, word in enumerate(words):
        if not 0 <= word <= 0xFFFFFFFF:
            raise ValueError(f"operand {word_index} is not an unsigned 32-bit value")
        for bit_index in range(31, -1, -1):
            bit = (word >> bit_index) & 1
            ui = UI_OPERAND_VALID | (UI_OPERAND_DATA if bit else 0)
            await tick(dut, ui)
    await tick(dut, 0)  # deassert data_valid before execute


async def execute_once(dut):
    """Generate one execute rising edge and check the wrapper status pulses."""
    await tick(dut, UI_EXECUTE)
    status = output(dut)
    assert status & UO_BUSY, "busy pulse did not assert on execute"
    assert status & UO_DONE, "done pulse did not assert on execute"
    await tick(dut, 0)
    status = output(dut)
    assert not (status & UO_BUSY), "busy should be a one-cycle pulse in this RTL"
    assert not (status & UO_DONE), "done should be a one-cycle pulse in this RTL"


async def read_serial_result(dut, pe_index, accumulator=False):
    """Read one 32-bit result MSB-first and return it as an unsigned integer."""
    if not 0 <= pe_index <= 3:
        raise ValueError("PE index must be in range 0..3")

    select = UI_READ_ACCUMULATOR if accumulator else 0
    await tick(dut, UI_READ_RESULT | select, pe_index)

    value = 0
    for bit_index in range(32):
        status = output(dut)
        assert status & UO_RESULT_VALID, f"result_valid dropped before bit {bit_index}"
        value = ((value << 1) | (1 if status & UO_RESULT_BIT else 0)) & 0xFFFFFFFF
        if bit_index != 31:
            # result_valid causes the serializer to shift once on this clock.
            await tick(dut, select, pe_index)

    await tick(dut, select, pe_index)
    assert not (output(dut) & UO_RESULT_VALID), "result_valid did not clear after 32 bits"
    return value


@cocotb.test()
async def test_reset_and_idle(dut):
    """Reset state, idle outputs, and unused bidirectional pins."""
    await start_and_reset(dut)
    assert int(dut.uio_oe.value) == 0, "uio_oe must be zero (all bidirectional pins input)"
    assert int(dut.uio_out.value) == 0, "uio_out must be tied low"
    assert output(dut) == 0, "all uo_out status/data bits should be zero after reset"


@cocotb.test()
async def test_config_done_asserts_on_exactly_32nd_shift(dut):
    """The configuration-complete flag must assert on the 32nd shift edge."""
    await start_and_reset(dut)
    for _ in range(31):
        await shift_config_bit(dut, 0)
    assert not (output(dut) & UO_CONFIG_DONE), "config_done asserted before 32 bits"
    await shift_config_bit(dut, 0)
    assert output(dut) & UO_CONFIG_DONE, "config_done did not assert on bit 32"


@cocotb.test()
async def test_two_pe_registered_add_pipeline_and_serial_readback(dut):
    """PE0 computes 10+7=17; PE1 consumes that registered result plus 5=22."""
    await start_and_reset(dut)

    # C0 = ADD(North, West). The transmission order is PE3, PE2, PE1, PE0.
    await configure_pes(dut, 0x00, 0x00, 0xC0, 0xC0)
    await load_operands(dut, [10, 7, 5, 0, 0, 0, 0, 0])

    await execute_once(dut)
    # One more execute lets PE1 consume PE0's result from the previous cycle.
    await execute_once(dut)

    pe0_value = await read_serial_result(dut, pe_index=0)
    pe1_value = await read_serial_result(dut, pe_index=1)

    assert pe0_value == 17, f"PE0 expected 17, got {pe0_value}"
    assert pe1_value == 22, f"PE1 expected 22, got {pe1_value}"


@cocotb.test()
async def test_mac_accumulator_readback(dut):
    """MAC accumulator adds 7*9 on each execute; two executions produce 126."""
    await start_and_reset(dut)

    # C3 = MAC(North, West); other PEs remain at their default zero config.
    await configure_pes(dut, 0x00, 0x00, 0x00, 0xC3)
    await load_operands(dut, [7, 9, 0, 0, 0, 0, 0, 0])

    await execute_once(dut)
    await execute_once(dut)

    accumulator = await read_serial_result(dut, pe_index=0, accumulator=True)
    assert accumulator == 126, f"PE0 accumulator expected 126, got {accumulator}"
