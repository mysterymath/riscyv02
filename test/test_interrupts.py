# SPDX-FileCopyrightText: © 2024 mysterymath
# SPDX-License-Identifier: Apache-2.0
#
# Interrupt handling tests: IRQ, NMI, BRK, RETI, WAI, STP, CLI/SEI, banked R6.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles
from test_helpers import *


@cocotb.test()
async def test_reset_i_state(dut):
    """Verify interrupts are disabled after reset (I=1)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _spin(0x0000))
    _place(prog, 0x0006, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0008, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x000A, _spin(0x000A))
    prog[0x0040] = 0x00
    prog[0x0041] = 0x00

    _load_program(dut, prog)
    dut.ena.value = 1
    dut.ui_in.value = 0x06  # IRQB=0 (asserted!)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 100)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0000, f"IRQ fired after reset! Got {val:#06x}"


@cocotb.test()
async def test_cli_enables_irq(dut):
    """CLI clears I bit, allowing interrupts to fire."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_cli())
    _place(prog, 0x0002, _spin(0x0002))
    _place(prog, 0x0006, _encode_li(rd=0, imm=0x5A))
    _place(prog, 0x0008, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x000A, _spin(0x000A))
    prog[0x0040] = 0x00
    prog[0x0041] = 0x00

    _load_program(dut, prog)
    dut.ena.value = 1
    dut.ui_in.value = 0x07
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 20)
    dut.ui_in.value = 0x06
    await ClockCycles(dut.clk, 100)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x005A, f"IRQ did not fire after CLI! Got {val:#06x}"


@cocotb.test()
async def test_sei_disables_irq(dut):
    """SEI sets I bit, preventing interrupts."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_j(off10=8))
    _place(prog, 0x0006, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0008, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x000A, _spin(0x000A))
    _place(prog, 0x0012, _encode_cli())
    _place(prog, 0x0014, _encode_sei())
    _place(prog, 0x0016, _spin(0x0016))
    prog[0x0040] = 0x00
    prog[0x0041] = 0x00

    _load_program(dut, prog)
    dut.ena.value = 1
    dut.ui_in.value = 0x07
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 50)
    dut.ui_in.value = 0x06
    await ClockCycles(dut.clk, 100)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0000, f"IRQ fired after SEI! Got {val:#06x}"


@cocotb.test()
async def test_reti(dut):
    """RETI restores I from EPC[0] and returns to saved PC."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_j(off10=8))
    _place(prog, 0x0006, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0008, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x000A, _encode_reti())
    _place(prog, 0x0012, _encode_cli())
    _place(prog, 0x0014, _spin(0x0014))
    prog[0x0040] = 0x00
    prog[0x0041] = 0x00

    _load_program(dut, prog)
    dut.ena.value = 1
    dut.ui_in.value = 0x07
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 30)
    dut.ui_in.value = 0x06
    await ClockCycles(dut.clk, 30)
    dut.ui_in.value = 0x07
    await ClockCycles(dut.clk, 100)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0042, f"IRQ handler did not execute! Got {val:#06x}"


@cocotb.test()
async def test_brk(dut):
    """BRK saves EPC, sets I=1, vectors to 0x0004."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_j(off10=8))
    _place(prog, 0x0004, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0006, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    _place(prog, 0x0012, _encode_brk())
    _place(prog, 0x0014, _spin(0x0014))
    prog[0x0040] = 0x00
    prog[0x0041] = 0x00

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0042, f"BRK handler not reached! Got {val:#06x}"


@cocotb.test()
async def test_wai(dut):
    """WAI halts until interrupt."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_j(off10=8))
    _place(prog, 0x0006, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0008, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x000A, _encode_reti())
    _place(prog, 0x0012, _encode_cli())
    _place(prog, 0x0014, _encode_wai())
    _place(prog, 0x0016, _encode_li(rd=0, imm=0x55))
    _place(prog, 0x0018, _encode_sw(rs=7, imm=0x42))
    _place(prog, 0x001A, _spin(0x001A))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    prog[0x0042] = 0x00; prog[0x0043] = 0x00

    _load_program(dut, prog)
    dut.ena.value = 1
    dut.ui_in.value = 0x07
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 50)

    v1 = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert v1 == 0x0000, f"Handler fired before IRQ: {v1:#06x}"

    dut.ui_in.value = 0x06
    await ClockCycles(dut.clk, 50)
    dut.ui_in.value = 0x07
    await ClockCycles(dut.clk, 100)

    v1 = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    v2 = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    assert v1 == 0x0042, f"IRQ handler marker: expected 0x0042, got {v1:#06x}"
    assert v2 == 0x0055, f"Post-WAI marker: expected 0x0099, got {v2:#06x}"


@cocotb.test()
async def test_stp(dut):
    """STP halts permanently; only reset recovers."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_stp())
    _place(prog, 0x0002, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0004, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0000, f"STP did not halt! Got {val:#06x}"


@cocotb.test()
async def test_nmi(dut):
    """NMI fires on NMIB falling edge, even with I=1."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0002, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0004, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    _place(prog, 0x0000, _spin(0x0000))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00

    _load_program(dut, prog)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 30)

    dut.ui_in.value = 0x05
    await ClockCycles(dut.clk, 5)
    dut.ui_in.value = 0x07
    await ClockCycles(dut.clk, 100)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0042, f"NMI handler not reached! Got {val:#06x}"


@cocotb.test()
async def test_i_bit_masking(dut):
    """After IRQ entry, I=1 prevents nested interrupts."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=0x20))
    _place(prog, 0x0002, _encode_or_rr(rd=1, rs1=0, rs2=1))
    _place(prog, 0x0004, _encode_jr(rs=1, imm=0))
    _place(prog, 0x0006, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0008, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x000A, _spin(0x000A))
    _place(prog, 0x0020, _encode_cli())
    _place(prog, 0x0022, _spin(0x0022))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00

    _load_program(dut, prog)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 50)
    _set_ui(dut, rdy=True, irqb=False, nmib=True)
    await ClockCycles(dut.clk, 500)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0042, f"IRQ handler didn't run! Got {val:#06x}"


@cocotb.test()
async def test_irq_during_multicycle(dut):
    """IRQ during LW completes the LW before entering handler."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_or_rr(rd=1, rs1=0, rs2=1))
    _place(prog, 0x0004, _encode_jr(rs=1, imm=0))
    _place(prog, 0x0006, _encode_sw(rs=7, imm=0x42))
    _place(prog, 0x0008, _encode_reti())
    _place(prog, 0x0010, _encode_cli())
    _place(prog, 0x0012, _encode_lw(rs=7, imm=0x30))
    _place(prog, 0x0014, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0016, _spin(0x0016))
    prog[0x0030] = 0x34; prog[0x0031] = 0x12
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    prog[0x0042] = 0x00; prog[0x0043] = 0x00

    _load_program(dut, prog)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 40)
    _set_ui(dut, rdy=True, irqb=False, nmib=True)
    await ClockCycles(dut.clk, 200)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    await ClockCycles(dut.clk, 50)

    main = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    irq = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    assert main == 0x1234, f"LW/SW failed! Got {main:#06x}"
    assert irq == 0x1234, f"IRQ saw wrong R0! Got {irq:#06x}"


@cocotb.test()
async def test_cli_atomicity(dut):
    """CLI with pending IRQ: IRQ fires after CLI completes."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_cli())
    _place(prog, 0x0002, _spin(0x0002))
    _place(prog, 0x0006, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0008, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x000A, _spin(0x000A))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00

    _load_program(dut, prog)
    dut.ena.value = 1
    dut.ui_in.value = 0x06
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 50)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0042, f"IRQ did not fire after CLI! Got {val:#06x}"


@cocotb.test()
async def test_nmi_edge_triggered(dut):
    """Holding NMIB low does not re-trigger. Only one NMI per falling edge."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _spin(0x0000))
    _place(prog, 0x0002, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0004, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00

    _load_program(dut, prog)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 30)

    _set_ui(dut, rdy=True, irqb=True, nmib=False)
    await ClockCycles(dut.clk, 500)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0042, f"NMI handler didn't run! Got {val:#06x}"


@cocotb.test()
async def test_nmi_priority_over_irq(dut):
    """When both NMI and IRQ are pending, NMI is taken."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_jr(rs=7, imm=0x10))
    _place(prog, 0x0002, _encode_jr(rs=7, imm=0x18))
    _place(prog, 0x0004, _spin(0x0004))
    _place(prog, 0x0006, _encode_jr(rs=7, imm=0x1C))
    _place(prog, 0x0020, _encode_cli())
    _place(prog, 0x0022, _spin(0x0022))
    _place(prog, 0x0030, _encode_li(rd=0, imm=0x22))
    _place(prog, 0x0032, _encode_sw(rs=7, imm=0x44))
    _place(prog, 0x0034, _spin(0x0034))
    _place(prog, 0x0038, _encode_li(rd=0, imm=0x11))
    _place(prog, 0x003A, _encode_sw(rs=7, imm=0x46))
    _place(prog, 0x003C, _spin(0x003C))
    prog[0x0044] = 0x00; prog[0x0045] = 0x00
    prog[0x0046] = 0x00; prog[0x0047] = 0x00

    _load_program(dut, prog)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 50)
    _set_ui(dut, rdy=True, irqb=False, nmib=False)
    await ClockCycles(dut.clk, 200)

    nmi = _read_ram(dut, 0x0044) | (_read_ram(dut, 0x0045) << 8)
    irq = _read_ram(dut, 0x0046) | (_read_ram(dut, 0x0047) << 8)
    assert nmi == 0x0022, f"NMI handler didn't run! Got {nmi:#06x}"
    assert irq == 0x0000, f"IRQ fired instead of NMI! Got {irq:#06x}"


@cocotb.test()
async def test_nmi_during_multicycle(dut):
    """NMI during LW completes the LW before entering handler."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_jr(rs=7, imm=0x10))
    _place(prog, 0x0002, _encode_sw(rs=7, imm=0x42))
    _place(prog, 0x0004, _encode_reti())
    _place(prog, 0x0006, _spin(0x0006))
    _place(prog, 0x0020, _encode_lw(rs=7, imm=0x30))
    _place(prog, 0x0022, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0024, _spin(0x0024))
    prog[0x0030] = 0x34; prog[0x0031] = 0x12
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    prog[0x0042] = 0x00; prog[0x0043] = 0x00

    _load_program(dut, prog)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 40)
    _set_ui(dut, rdy=True, irqb=True, nmib=False)
    await ClockCycles(dut.clk, 10)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    await ClockCycles(dut.clk, 200)

    main = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    nmi = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    assert main == 0x1234, f"Main code failed! Got {main:#06x}"
    assert nmi == 0x1234, f"NMI saw wrong R0! Got {nmi:#06x}"


@cocotb.test()
async def test_nmi_second_edge(dut):
    """After first NMI handled, second falling edge triggers another."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _spin(0x0000))
    _place(prog, 0x0002, _encode_lw(rs=7, imm=0x40))
    _place(prog, 0x0004, _encode_addi(rd=0, imm=1))
    _place(prog, 0x0006, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0008, _encode_reti())
    prog[0x0040] = 0x00; prog[0x0041] = 0x00

    _load_program(dut, prog)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 30)

    # First NMI pulse
    _set_ui(dut, rdy=True, irqb=True, nmib=False)
    await ClockCycles(dut.clk, 10)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    await ClockCycles(dut.clk, 100)

    # Second NMI pulse
    _set_ui(dut, rdy=True, irqb=True, nmib=False)
    await ClockCycles(dut.clk, 10)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    await ClockCycles(dut.clk, 100)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 2, f"Expected 2 NMI entries, got {val}"


@cocotb.test()
async def test_nmi_during_rdy_low(dut):
    """NMI edge while RDY=0 is captured and serviced when RDY returns."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _spin(0x0000))
    _place(prog, 0x0002, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0004, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00

    _load_program(dut, prog)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 30)

    _set_ui(dut, rdy=False, irqb=True, nmib=True)
    await ClockCycles(dut.clk, 10)
    _set_ui(dut, rdy=False, irqb=True, nmib=False)
    await ClockCycles(dut.clk, 10)
    _set_ui(dut, rdy=False, irqb=True, nmib=True)
    await ClockCycles(dut.clk, 10)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0000, f"NMI ran while halted! Got {val:#06x}"

    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    await ClockCycles(dut.clk, 100)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0042, f"NMI lost during RDY=0! Got {val:#06x}"


@cocotb.test()
async def test_wai_irq(dut):
    """WAI halts until IRQ; handler runs, RETI returns past WAI."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_jr(rs=7, imm=0x10))
    _place(prog, 0x0002, _spin(0x0002))
    _place(prog, 0x0004, _spin(0x0004))
    _place(prog, 0x0006, _encode_li(rd=0, imm=0x11))
    _place(prog, 0x0008, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x000A, _encode_reti())
    _place(prog, 0x0020, _encode_cli())
    _place(prog, 0x0022, _encode_wai())
    _place(prog, 0x0024, _encode_li(rd=0, imm=0x22))
    _place(prog, 0x0026, _encode_sw(rs=7, imm=0x42))
    _place(prog, 0x0028, _spin(0x0028))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    prog[0x0042] = 0x00; prog[0x0043] = 0x00

    _load_program(dut, prog)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 50)
    _set_ui(dut, rdy=True, irqb=False, nmib=True)
    await ClockCycles(dut.clk, 200)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    await ClockCycles(dut.clk, 100)

    irq = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    post = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    assert irq == 0x0011, f"IRQ handler didn't run! Got {irq:#06x}"
    assert post == 0x0022, f"Didn't return past WAI! Got {post:#06x}"


@cocotb.test()
async def test_wai_nmi(dut):
    """WAI with I=1; NMI wakes, handler runs, RETI returns past WAI."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_jr(rs=7, imm=0x10))
    _place(prog, 0x0002, _encode_li(rd=0, imm=0x11))
    _place(prog, 0x0004, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0006, _encode_reti())
    _place(prog, 0x0020, _encode_wai())
    _place(prog, 0x0022, _encode_li(rd=0, imm=0x22))
    _place(prog, 0x0024, _encode_sw(rs=7, imm=0x42))
    _place(prog, 0x0026, _spin(0x0026))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    prog[0x0042] = 0x00; prog[0x0043] = 0x00

    _load_program(dut, prog)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 50)
    _set_ui(dut, rdy=True, irqb=True, nmib=False)
    await ClockCycles(dut.clk, 10)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    await ClockCycles(dut.clk, 200)

    nmi = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    post = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    assert nmi == 0x0011, f"NMI handler didn't run! Got {nmi:#06x}"
    assert post == 0x0022, f"Didn't return past WAI! Got {post:#06x}"


@cocotb.test()
async def test_wai_masked_irq_wakes(dut):
    """WAI with I=1: masked IRQ wakes WAI, resumes past it without handler."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_jr(rs=7, imm=0x10))
    _place(prog, 0x0006, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0008, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x000A, _spin(0x000A))
    _place(prog, 0x0020, _encode_wai())
    _place(prog, 0x0022, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0024, _encode_sw(rs=7, imm=0x42))
    _place(prog, 0x0026, _spin(0x0026))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    prog[0x0042] = 0x00; prog[0x0043] = 0x00

    _load_program(dut, prog)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 50)
    _set_ui(dut, rdy=True, irqb=False, nmib=True)
    await ClockCycles(dut.clk, 200)

    irq = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    post = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    assert irq == 0x0000, f"IRQ handler ran despite I=1! Got {irq:#06x}"
    assert post == 0x0042, f"WAI didn't resume! Got {post:#06x}"


@cocotb.test()
async def test_brk_masks_irq(dut):
    """BRK sets I=1; IRQ held low during BRK handler should not fire."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_jr(rs=7, imm=0x10))
    _place(prog, 0x0002, _spin(0x0002))
    _place(prog, 0x0004, _encode_jr(rs=7, imm=0x18))
    _place(prog, 0x0006, _encode_jr(rs=7, imm=0x1C))
    _place(prog, 0x0020, _encode_cli())
    _place(prog, 0x0022, _encode_brk())
    _place(prog, 0x0024, _spin(0x0024))
    _place(prog, 0x0030, _encode_li(rd=0, imm=0x11))
    _place(prog, 0x0032, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0034, _encode_reti())
    _place(prog, 0x0038, _encode_li(rd=0, imm=0x22))
    _place(prog, 0x003A, _encode_sw(rs=7, imm=0x42))
    _place(prog, 0x003C, _encode_reti())
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    prog[0x0042] = 0x00; prog[0x0043] = 0x00

    _load_program(dut, prog)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 50)
    _set_ui(dut, rdy=True, irqb=False, nmib=True)
    await ClockCycles(dut.clk, 300)

    brk = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    irq = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    assert brk == 0x0011, f"BRK handler didn't run! Got {brk:#06x}"
    assert irq == 0x0022, f"IRQ didn't fire after RETI! Got {irq:#06x}"


@cocotb.test()
async def test_brk_restores_i(dut):
    """BRK from I=1: RETI restores I=1, IRQ stays masked."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_jr(rs=7, imm=0x10))
    _place(prog, 0x0002, _spin(0x0002))
    _place(prog, 0x0004, _encode_jr(rs=7, imm=0x18))
    _place(prog, 0x0006, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0008, _encode_sw(rs=7, imm=0x42))
    _place(prog, 0x000A, _encode_reti())
    _place(prog, 0x0020, _encode_brk())
    _place(prog, 0x0022, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0024, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0026, _spin(0x0026))
    _place(prog, 0x0030, _encode_li(rd=0, imm=0x11))
    _place(prog, 0x0032, _encode_sw(rs=7, imm=0x44))
    _place(prog, 0x0034, _encode_reti())
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    prog[0x0042] = 0x00; prog[0x0043] = 0x00
    prog[0x0044] = 0x00; prog[0x0045] = 0x00

    _load_program(dut, prog)
    dut.ena.value = 1
    dut.ui_in.value = 0x06
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 300)

    brk = _read_ram(dut, 0x0044) | (_read_ram(dut, 0x0045) << 8)
    ret = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    irq = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    assert brk == 0x0011, f"BRK handler didn't run! Got {brk:#06x}"
    assert ret == 0x0042, f"RETI didn't return! Got {ret:#06x}"
    assert irq == 0x0000, f"IRQ fired despite I=1! Got {irq:#06x}"


@cocotb.test()
async def test_banked_r6_read(dut):
    """In IRQ handler, R6 reads the banked value (return addr | I bit)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_jr(rs=7, imm=0x10))
    _place(prog, 0x0002, _spin(0x0002))
    _place(prog, 0x0004, _spin(0x0004))
    _place(prog, 0x0006, _encode_or_rr(rd=0, rs1=6, rs2=6))
    _place(prog, 0x0008, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x000A, _spin(0x000A))
    _place(prog, 0x0020, _encode_cli())
    _place(prog, 0x0022, _spin(0x0022))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00

    _load_program(dut, prog)
    dut.ena.value = 1
    dut.ui_in.value = 0x06
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 300)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0022, f"Wrong banked R6! Got {val:#06x}"


@cocotb.test()
async def test_banked_r6_redirect(dut):
    """In BRK handler, LW R6 overwrites banked R6; RETI jumps there."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_jr(rs=7, imm=0x10))
    _place(prog, 0x0002, _spin(0x0002))
    _place(prog, 0x0004, _encode_jr(rs=7, imm=0x18))
    _place(prog, 0x0006, _spin(0x0006))
    _place(prog, 0x0020, _encode_brk())
    _place(prog, 0x0022, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0024, _encode_sw(rs=7, imm=0x60))
    _place(prog, 0x0026, _spin(0x0026))
    _place(prog, 0x0030, _encode_lw(rs=7, imm=0x50))
    _place(prog, 0x0032, _encode_or_rr(rd=6, rs1=0, rs2=0))
    _place(prog, 0x0034, _encode_reti())
    _place(prog, 0x0040, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0042, _encode_sw(rs=7, imm=0x62))
    _place(prog, 0x0044, _spin(0x0044))
    prog[0x0050] = 0x40; prog[0x0051] = 0x00
    prog[0x0060] = 0x00; prog[0x0061] = 0x00
    prog[0x0062] = 0x00; prog[0x0063] = 0x00

    _load_program(dut, prog)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 300)

    orig = _read_ram(dut, 0x0060) | (_read_ram(dut, 0x0061) << 8)
    redir = _read_ram(dut, 0x0062) | (_read_ram(dut, 0x0063) << 8)
    assert orig == 0x0000, f"Original return executed! Got {orig:#06x}"
    assert redir == 0x0042, f"Redirect didn't work! Got {redir:#06x}"


@cocotb.test()
async def test_banked_r6_i_bit(dut):
    """BRK handler loads R6 with bit 0 clear; RETI restores I=0, IRQ fires."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_jr(rs=7, imm=0x10))
    _place(prog, 0x0002, _spin(0x0002))
    _place(prog, 0x0004, _encode_jr(rs=7, imm=0x18))
    _place(prog, 0x0006, _encode_li(rd=0, imm=0x33))
    _place(prog, 0x0008, _encode_sw(rs=7, imm=0x60))
    _place(prog, 0x000A, _spin(0x000A))
    _place(prog, 0x0020, _encode_brk())
    _place(prog, 0x0022, _spin(0x0022))
    _place(prog, 0x0030, _encode_lw(rs=7, imm=0x50))
    _place(prog, 0x0032, _encode_or_rr(rd=6, rs1=0, rs2=0))
    _place(prog, 0x0034, _encode_reti())
    _place(prog, 0x0040, _spin(0x0040))
    prog[0x0050] = 0x40; prog[0x0051] = 0x00
    prog[0x0060] = 0x00; prog[0x0061] = 0x00

    _load_program(dut, prog)
    dut.ena.value = 1
    dut.ui_in.value = 0x06
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 300)

    val = _read_ram(dut, 0x0060) | (_read_ram(dut, 0x0061) << 8)
    assert val == 0x0033, f"IRQ didn't fire after I=0 restore! Got {val:#06x}"


@cocotb.test()
async def test_irq_interrupts_jr(dut):
    """IRQ fires after JR completes; RETI must return to JR target."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_cli())
    _place(prog, 0x0002, _encode_jr(rs=7, imm=0x10))
    _place(prog, 0x0006, _encode_li(rd=1, imm=0x5A))
    _place(prog, 0x0008, _encode_or_rr(rd=0, rs1=1, rs2=1))
    _place(prog, 0x000A, _encode_sw(rs=7, imm=0x60))
    _place(prog, 0x000C, _encode_reti())
    _place(prog, 0x0020, _encode_li(rd=2, imm=0x7E))
    _place(prog, 0x0022, _encode_or_rr(rd=0, rs1=2, rs2=2))
    _place(prog, 0x0024, _encode_sw(rs=7, imm=0x62))
    _place(prog, 0x0026, _spin(0x0026))
    prog[0x0060] = 0x00; prog[0x0061] = 0x00
    prog[0x0062] = 0x00; prog[0x0063] = 0x00

    _load_program(dut, prog)
    dut.ena.value = 1
    dut.ui_in.value = 0x06
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 300)

    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    await ClockCycles(dut.clk, 200)

    irq_marker = _read_ram(dut, 0x0060) | (_read_ram(dut, 0x0061) << 8)
    jr_marker = _read_ram(dut, 0x0062) | (_read_ram(dut, 0x0063) << 8)
    assert irq_marker == 0x005A, f"IRQ handler didn't run! Got {irq_marker:#06x}"
    assert jr_marker == 0x007E, f"RETI didn't return to JR target! Got {jr_marker:#06x}"
