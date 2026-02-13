# SPDX-FileCopyrightText: © 2024 mysterymath
# SPDX-License-Identifier: Apache-2.0
#
# Branch and jump tests: JR, JALR, J, JAL, BZ/BNZ, single-step.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge
from test_helpers import *


@cocotb.test()
async def test_jr_computed(dut):
    """Load an address into a register, then JR to it."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0030] = 0x20
    prog[0x0031] = 0x00
    prog[0x0032] = 0xEF
    prog[0x0033] = 0xBE

    _place(prog, 0x0000, _encode_lw(rs=7, imm=0x30))
    _place(prog, 0x0002, _encode_or_rr(rd=1, rs1=0, rs2=0))
    _place(prog, 0x0004, _encode_lw(rs=7, imm=0x32))
    _place(prog, 0x0006, _encode_or_rr(rd=2, rs1=0, rs2=0))
    _place(prog, 0x0008, _encode_jr(rs=1, imm=0))
    _place(prog, 0x0020, _encode_or_rr(rd=0, rs1=2, rs2=2))
    _place(prog, 0x0022, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0024, _spin(0x0024))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0xBEEF, f"Expected 0xBEEF, got {val:#06x}"


@cocotb.test()
async def test_jr_after_lw(dut):
    """JR using a register value loaded by the immediately preceding LW."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0030] = 0x40
    prog[0x0031] = 0x00

    _place(prog, 0x0000, _encode_lw(rs=7, imm=0x30))
    _place(prog, 0x0002, _encode_or_rr(rd=1, rs1=0, rs2=0))
    _place(prog, 0x0004, _encode_jr(rs=1, imm=0))
    _place(prog, 0x0040, _encode_li(rd=0, imm=0))
    _place(prog, 0x0042, _encode_sw(rs=7, imm=0x50))
    _place(prog, 0x0044, _spin(0x0044))

    prog[0x0050] = 0xFF
    prog[0x0051] = 0xFF

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0050)
    hi = _read_ram(dut, 0x0051)
    assert lo == 0x00 and hi == 0x00, \
        f"SW at JR target did not execute (expected 0x0000, got {lo:#04x}{hi:#04x})"


@cocotb.test()
async def test_branches(dut):
    """BZ and BNZ branch behavior."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=2, imm=1))
    _place(prog, 0x0002, _encode_bz(rs=1, imm=3))
    _place(prog, 0x0004, _encode_li(rd=0, imm=0x13))
    _place(prog, 0x0006, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    _place(prog, 0x000A, _encode_bnz(rs=2, imm=3))
    _place(prog, 0x000C, _encode_li(rd=0, imm=0x13))
    _place(prog, 0x000E, _encode_sw(rs=7, imm=0x42))
    _place(prog, 0x0010, _spin(0x0010))
    _place(prog, 0x0012, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0014, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0016, _spin(0x0016))

    prog[0x0040] = 0x00
    prog[0x0041] = 0x00

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0042, f"Branch test failed, got {val:#06x}"


@cocotb.test()
async def test_j_jal(dut):
    """J forward and JAL with link to R6."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_j(off10=8))
    _place(prog, 0x0012, _encode_jal(off10=4))
    _place(prog, 0x0014, _spin(0x0014))
    _place(prog, 0x001C, _encode_or_rr(rd=0, rs1=6, rs2=6))
    _place(prog, 0x001E, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0020, _spin(0x0020))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0014, f"JAL link: expected 0x0014, got {val:#06x}"


@cocotb.test()
async def test_jalr(dut):
    """JALR: jump to rs+off, save return addr in rs."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0030] = 0x40
    prog[0x0031] = 0x00
    _place(prog, 0x0000, _encode_lw(rs=7, imm=0x30))
    _place(prog, 0x0002, _encode_or_rr(rd=1, rs1=0, rs2=0))
    _place(prog, 0x0004, _encode_jalr(rs=1, imm=0))
    _place(prog, 0x0040, _encode_or_rr(rd=0, rs1=1, rs2=1))
    _place(prog, 0x0042, _encode_sw(rs=7, imm=0x50))
    _place(prog, 0x0044, _spin(0x0044))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    val = _read_ram(dut, 0x0050) | (_read_ram(dut, 0x0051) << 8)
    assert val == 0x0006, f"JALR link: expected 0x0006, got {val:#06x}"


@cocotb.test()
async def test_single_step(dut):
    """Test single-step debugging using RDY/SYNC protocol."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=0x11))
    _place(prog, 0x0002, _encode_li(rd=2, imm=0x22))
    _place(prog, 0x0004, _encode_li(rd=3, imm=0x33))
    _place(prog, 0x0006, _encode_or_rr(rd=0, rs1=1, rs2=1))
    _place(prog, 0x0008, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x000A, _encode_or_rr(rd=0, rs1=2, rs2=2))
    _place(prog, 0x000C, _encode_sw(rs=7, imm=0x42))
    _place(prog, 0x000E, _encode_or_rr(rd=0, rs1=3, rs2=3))
    _place(prog, 0x0010, _encode_sw(rs=7, imm=0x44))
    _place(prog, 0x0012, _spin(0x0012))

    def get_sync():
        return (int(dut.uo_out.value) >> 1) & 1

    def read_markers():
        m1 = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
        m2 = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
        m3 = _read_ram(dut, 0x0044) | (_read_ram(dut, 0x0045) << 8)
        return (m1, m2, m3)

    async def run_to_sync():
        dut.ui_in.value = 0x06
        for _ in range(200):
            await FallingEdge(dut.clk)
            if get_sync():
                dut.ui_in.value = 0x02
                return True
        return False

    async def single_step():
        dut.ui_in.value = 0x06
        for _ in range(200):
            await FallingEdge(dut.clk)
            if not get_sync():
                break
        for _ in range(200):
            await FallingEdge(dut.clk)
            if get_sync():
                dut.ui_in.value = 0x02
                return True
        return False

    # Reset with RDY=0
    dut.ena.value = 1
    dut.ui_in.value = 0x02
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)

    _load_program(dut, prog)
    for addr in range(0x40, 0x46):
        dut.ram[addr].value = 0x00

    assert await run_to_sync(), "Failed to reach first boundary"

    m_before = read_markers()
    await ClockCycles(dut.clk, 20)
    m_after = read_markers()
    assert m_before == m_after, f"CPU modified state while halted"

    for i in range(3):
        assert await single_step(), f"single_step failed on LI {i+1}"

    assert await single_step(), "single_step failed on OR"
    assert await single_step(), "single_step failed on SW R1"
    m = read_markers()
    assert m == (0x0011, 0, 0), f"After SW R1: expected (0x0011, 0, 0), got {[hex(x) for x in m]}"

    assert await single_step(), "single_step failed on OR R2"
    assert await single_step(), "single_step failed on SW R2"
    m = read_markers()
    assert m == (0x0011, 0x0022, 0), f"After SW R2: expected, got {[hex(x) for x in m]}"

    assert await single_step(), "single_step failed on OR R3"
    assert await single_step(), "single_step failed on SW R3"
    m = read_markers()
    assert m == (0x0011, 0x0022, 0x0033), f"After SW R3: expected all, got {[hex(x) for x in m]}"


@cocotb.test()
async def test_bnz_high_byte(dut):
    """BNZ detects nonzero in high byte only."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0030] = 0x00; prog[0x0031] = 0x01
    _place(prog, 0x0000, _encode_lw(rs=7, imm=0x30))
    _place(prog, 0x0002, _encode_or_rr(rd=1, rs1=0, rs2=0))
    _place(prog, 0x0004, _encode_bnz(rs=1, imm=2))
    _place(prog, 0x0006, _encode_li(rd=0, imm=0x13))
    _place(prog, 0x0008, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x000A, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x000C, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x000E, _spin(0x000E))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0042, f"BNZ didn't detect high byte! Got {val:#06x}"


@cocotb.test()
async def test_j_backward(dut):
    """J with negative offset jumps backward."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_j(off10=15))
    _place(prog, 0x0010, _encode_li(rd=0, imm=0x42))
    _place(prog, 0x0012, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0014, _spin(0x0014))
    _place(prog, 0x0020, _encode_j(off10=-9))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0042, f"J backward failed! Got {val:#06x}"


@cocotb.test()
async def test_jal_link_value(dut):
    """JAL stores correct return address in R6."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_jal(off10=4))
    _place(prog, 0x0002, _spin(0x0002))
    _place(prog, 0x000A, _encode_or_rr(rd=0, rs1=6, rs2=6))
    _place(prog, 0x000C, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x000E, _spin(0x000E))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0002, f"JAL link wrong! Got {val:#06x}"


@cocotb.test()
async def test_bz_not_taken(dut):
    """BZ on non-zero register -> branch not taken."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=5))
    _place(prog, 0x0002, _encode_bz(rs=1, imm=3))
    _place(prog, 0x0004, _encode_li(rd=2, imm=7))
    _place(prog, 0x0006, _encode_or_rr(rd=0, rs1=2, rs2=2))
    _place(prog, 0x0008, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x000A, _spin(0x000A))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0007, f"Expected 0x0007, got {val:#06x}"


@cocotb.test()
async def test_bnz_not_taken(dut):
    """BNZ on zero register -> branch not taken."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_bnz(rs=0, imm=3))
    _place(prog, 0x0002, _encode_li(rd=1, imm=7))
    _place(prog, 0x0004, _encode_or_rr(rd=0, rs1=1, rs2=1))
    _place(prog, 0x0006, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0007, f"Expected 0x0007, got {val:#06x}"
