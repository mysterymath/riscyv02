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

    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x30))
    _place(prog, 0x0002, _encode_sw(rs=1, imm=0x32))
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
    """Use negative offsets with R0 as base."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x004E] = 0xFE
    prog[0x004F] = 0xCA

    _place(prog, 0x0000, _encode_li(rd=0, imm=0x50))      # R0 = 0x50
    _place(prog, 0x0002, _encode_lw(rd=1, imm=-2))         # R1 = mem16[R0-2] = mem16[0x4E]
    _place(prog, 0x0004, _encode_sw_s(rd=1, imm=0x60))     # mem16[R7+0x60] = R1
    _place(prog, 0x0006, _spin(0x0006))

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

    _place(prog, 0x0000, _encode_lb(rd=1, imm=0x30))
    _place(prog, 0x0002, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0004, _encode_lbu(rd=2, imm=0x30))
    _place(prog, 0x0006, _encode_sw(rs=2, imm=0x42))
    _place(prog, 0x0008, _encode_sb(rs=2, imm=0x44))
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
    _place(prog, 0x0000, _encode_lb(rd=1, imm=0x30))
    _place(prog, 0x0002, _encode_sw(rs=1, imm=0x40))
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
    _place(prog, 0x0000, _encode_lbu(rd=1, imm=0x30))
    _place(prog, 0x0002, _encode_sw(rs=1, imm=0x40))
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
    _place(prog, 0x0000, _encode_li(rd=0, imm=0x20))      # R0 = 0x20
    _place(prog, 0x0002, _encode_lb(rd=1, imm=-1))         # R1 = sext(mem[R0-1]) = 0x007F
    _place(prog, 0x0004, _encode_sw_s(rd=1, imm=0x40))     # mem16[R7+0x40] = R1
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x007F, f"Expected 0x007F, got {val:#06x}"


@cocotb.test()
async def test_sp_lw_sw(dut):
    """LW.S/SW.S: word load/store via R7 (SP) with offset."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0030] = 0xEF
    prog[0x0031] = 0xBE
    # LW.S R1, 0x30 — load from R7+0x30 = 0x0030
    _place(prog, 0x0000, _encode_lw_s(rd=1, imm=0x30))
    # SW.S R1, 0x50 — store to R7+0x50 = 0x0050
    _place(prog, 0x0002, _encode_sw_s(rd=1, imm=0x50))
    _place(prog, 0x0004, _spin(0x0004))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    val = _read_ram(dut, 0x0050) | (_read_ram(dut, 0x0051) << 8)
    assert val == 0xBEEF, f"SP LW/SW: expected 0xBEEF, got {val:#06x}"


@cocotb.test()
async def test_sp_lb_sb(dut):
    """LB.S/LBU.S/SB.S: byte load/store via R7 (SP)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0030] = 0x85

    # LB.S R1, 0x30 — sign-extend load from R7+0x30
    _place(prog, 0x0000, _encode_lb_s(rd=1, imm=0x30))
    _place(prog, 0x0002, _encode_sw_s(rd=1, imm=0x40))
    # LBU.S R2, 0x30 — zero-extend load from R7+0x30
    _place(prog, 0x0004, _encode_lbu_s(rd=2, imm=0x30))
    _place(prog, 0x0006, _encode_sw_s(rd=2, imm=0x42))
    # SB.S R1, 0x44 — store low byte of R1
    _place(prog, 0x0008, _encode_sb_s(rd=1, imm=0x44))
    _place(prog, 0x000A, _spin(0x000A))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    v_lb = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    v_lbu = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    v_sb = _read_ram(dut, 0x0044)
    assert v_lb == 0xFF85, f"LB.S: expected 0xFF85, got {v_lb:#06x}"
    assert v_lbu == 0x0085, f"LBU.S: expected 0x0085, got {v_lbu:#06x}"
    assert v_sb == 0x85, f"SB.S: expected 0x85, got {v_sb:#04x}"


@cocotb.test()
async def test_sp_negative_offset(dut):
    """SP load/store with negative offset."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # Set R7 = 0x50
    _place(prog, 0x0000, _encode_li(rd=7, imm=0x50))
    # Store data: LI R1, 0x42; SW.S R1, -2 → stores at R7-2 = 0x4E
    _place(prog, 0x0002, _encode_li(rd=1, imm=0x42))
    _place(prog, 0x0004, _encode_sw_s(rd=1, imm=-2))
    # Load it back: LW.S R2, -2 → loads from 0x4E
    _place(prog, 0x0006, _encode_lw_s(rd=2, imm=-2))
    # Store R2 to a known location for checking
    _place(prog, 0x0008, _encode_sw_s(rd=2, imm=0x10))  # R7+0x10 = 0x60
    _place(prog, 0x000A, _spin(0x000A))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    val = _read_ram(dut, 0x0060) | (_read_ram(dut, 0x0061) << 8)
    assert val == 0x0042, f"SP neg offset: expected 0x0042, got {val:#06x}"


@cocotb.test()
async def test_sp_arbitrary_register(dut):
    """SP loads/stores work with any register, not just R0."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0030] = 0xCD
    prog[0x0031] = 0xAB

    # Load to R5 (not R0): LW.S R5, 0x30
    _place(prog, 0x0000, _encode_lw_s(rd=5, imm=0x30))
    # Store from R5: SW.S R5, 0x50
    _place(prog, 0x0002, _encode_sw_s(rd=5, imm=0x50))
    _place(prog, 0x0004, _spin(0x0004))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    val = _read_ram(dut, 0x0050) | (_read_ram(dut, 0x0051) << 8)
    assert val == 0xABCD, f"SP arb reg: expected 0xABCD, got {val:#06x}"
