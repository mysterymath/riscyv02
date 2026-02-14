# SPDX-FileCopyrightText: © 2024 mysterymath
# SPDX-License-Identifier: Apache-2.0
#
# Memory operation tests: LW/SW, LB/LBU/SB, R,R loads/stores.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles
from test_helpers import *


@cocotb.test()
async def test_lw_sw_jr_basic(dut):
    """LW from memory, SW to memory, JR to spin loop."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0030] = 0x34
    prog[0x0031] = 0x12

    _place(prog, 0x0000, _encode_lw(rs=7, imm=0x30))
    _place(prog, 0x0002, _encode_sw(rs=7, imm=0x32))
    _place(prog, 0x0004, _spin(0x0004))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0032)
    hi = _read_ram(dut, 0x0033)
    assert lo == 0x34, f"Expected 0x34 at 0x0032, got {lo:#04x}"
    assert hi == 0x12, f"Expected 0x12 at 0x0033, got {hi:#04x}"


@cocotb.test()
async def test_negative_offsets(dut):
    """Use negative offsets and multiple registers."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0030] = 0x50
    prog[0x0031] = 0x00
    prog[0x004E] = 0xFE
    prog[0x004F] = 0xCA

    _place(prog, 0x0000, _encode_lw(rs=7, imm=0x30))
    _place(prog, 0x0002, _encode_or_rr(rd=3, rs1=0, rs2=0))
    _place(prog, 0x0004, _encode_lw(rs=3, imm=-2))
    _place(prog, 0x0006, _encode_sw(rs=7, imm=0x60))
    _place(prog, 0x0008, _spin(0x0008))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    val = _read_ram(dut, 0x0060) | (_read_ram(dut, 0x0061) << 8)
    assert val == 0xCAFE, f"Expected 0xCAFE, got {val:#06x}"


@cocotb.test()
async def test_byte_ops(dut):
    """LB, LBU, SB byte memory operations."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0030] = 0x85

    _place(prog, 0x0000, _encode_lb(rs=7, imm=0x30))
    _place(prog, 0x0002, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0004, _encode_lbu(rs=7, imm=0x30))
    _place(prog, 0x0006, _encode_sw(rs=7, imm=0x42))
    _place(prog, 0x0008, _encode_sb(rs=7, imm=0x44))
    _place(prog, 0x000A, _spin(0x000A))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    v_lb = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    v_lbu = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    v_sb = _read_ram(dut, 0x0044)
    assert v_lb == 0xFF85, f"LB: expected 0xFF85, got {v_lb:#06x}"
    assert v_lbu == 0x0085, f"LBU: expected 0x0085, got {v_lbu:#06x}"
    assert v_sb == 0x85, f"SB: expected 0x85, got {v_sb:#04x}"


@cocotb.test()
async def test_rr_load_store(dut):
    """R,R format load/store with explicit rd and rs."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0030] = 0xAD
    prog[0x0031] = 0xDE

    _place(prog, 0x0000, _encode_li(rd=1, imm=0x30))
    _place(prog, 0x0002, _encode_lw_rr(rd=2, rs=1))
    _place(prog, 0x0004, _encode_li(rd=3, imm=0x50))
    _place(prog, 0x0006, _encode_sw_rr(rd=2, rs=3))
    _place(prog, 0x0008, _spin(0x0008))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    val = _read_ram(dut, 0x0050) | (_read_ram(dut, 0x0051) << 8)
    assert val == 0xDEAD, f"R,R load/store: expected 0xDEAD, got {val:#06x}"


@cocotb.test()
async def test_lb_sign_extend(dut):
    """LB sign-extends 0x80 to 0xFF80."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0030] = 0x80
    _place(prog, 0x0000, _encode_lb(rs=7, imm=0x30))
    _place(prog, 0x0002, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0004, _spin(0x0004))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0xFF80, f"LB sign extend failed! Got {val:#06x}"


@cocotb.test()
async def test_lbu_zero_extend(dut):
    """LBU zero-extends 0x80 to 0x0080."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0030] = 0x80
    _place(prog, 0x0000, _encode_lbu(rs=7, imm=0x30))
    _place(prog, 0x0002, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0004, _spin(0x0004))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0080, f"LBU zero extend failed! Got {val:#06x}"


@cocotb.test()
async def test_byte_negative_offset(dut):
    """LB with negative offset computes correct address."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x001F] = 0x7F
    _place(prog, 0x0000, _encode_li(rd=1, imm=0x20))
    _place(prog, 0x0002, _encode_lb(rs=1, imm=-1))
    _place(prog, 0x0004, _encode_sw(rs=7, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x007F, f"Expected 0x007F, got {val:#06x}"
