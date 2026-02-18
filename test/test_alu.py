# SPDX-FileCopyrightText: © 2024 mysterymath
# SPDX-License-Identifier: Apache-2.0
#
# ALU, logic, shift, comparison, and immediate tests.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles
from test_helpers import *


@cocotb.test()
async def test_add_basic(dut):
    """ADD rd, rs1, rs2 basic addition."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=5))
    _place(prog, 0x0002, _encode_li(rd=2, imm=3))
    _place(prog, 0x0004, _encode_add(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 8, f"Expected 8, got {val}"


@cocotb.test()
async def test_sub_basic(dut):
    """SUB rd, rs1, rs2 basic subtraction."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=10))
    _place(prog, 0x0002, _encode_li(rd=2, imm=3))
    _place(prog, 0x0004, _encode_sub(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 7, f"Expected 7, got {val}"


@cocotb.test()
async def test_li_basic(dut):
    """LI rd, imm9 with positive and negative values."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=42))
    _place(prog, 0x0002, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0004, _encode_li(rd=2, imm=-5))
    _place(prog, 0x0006, _encode_sw(rs=2, imm=0x42))
    _place(prog, 0x0008, _spin(0x0008))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    v1 = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    v2 = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    assert v1 == 42, f"Expected 42, got {v1}"
    assert v2 == 0xFFFB, f"Expected 0xFFFB, got {v2:#06x}"


@cocotb.test()
async def test_addi_basic(dut):
    """ADD.I rd, imm9 adds immediate to register."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=100))
    _place(prog, 0x0002, _encode_addi(rd=1, imm=50))
    _place(prog, 0x0004, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 150, f"Expected 150, got {val}"


@cocotb.test()
async def test_logic_imm(dut):
    """AND.I, OR.I, XOR.I with 8-bit zero-extended immediates."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=0x7F))
    _place(prog, 0x0002, _encode_andi(rd=1, imm=0x0F))
    _place(prog, 0x0004, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0006, _encode_li(rd=2, imm=0x10))
    _place(prog, 0x0008, _encode_ori(rd=2, imm=0x03))
    _place(prog, 0x000A, _encode_sw(rs=2, imm=0x42))
    _place(prog, 0x000C, _encode_li(rd=3, imm=0x55))
    _place(prog, 0x000E, _encode_xori(rd=3, imm=0xAA))
    _place(prog, 0x0010, _encode_sw(rs=3, imm=0x44))
    _place(prog, 0x0012, _spin(0x0012))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    v1 = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    v2 = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    v3 = _read_ram(dut, 0x0044) | (_read_ram(dut, 0x0045) << 8)
    assert v1 == 0x000F, f"AND.I: expected 0x000F, got {v1:#06x}"
    assert v2 == 0x0013, f"OR.I: expected 0x0013, got {v2:#06x}"
    assert v3 == 0x00FF, f"XOR.I: expected 0x0055, got {v3:#06x}"


@cocotb.test()
async def test_lui(dut):
    """LUI: rd = sext(imm7) << 9."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_lui(rd=1, imm7=1))
    _place(prog, 0x0002, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0004, _encode_lui(rd=2, imm7=-1))
    _place(prog, 0x0006, _encode_sw(rs=2, imm=0x42))
    _place(prog, 0x0008, _spin(0x0008))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    v1 = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    v2 = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    assert v1 == 0x0200, f"LUI 1: expected 0x0200, got {v1:#06x}"
    assert v2 == 0xFE00, f"LUI -1: expected 0xFE00, got {v2:#06x}"


@cocotb.test()
async def test_shifts(dut):
    """SLL.I, SRL.I, SRA.I basic."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=1))
    _place(prog, 0x0002, _encode_slli(rd=1, shamt=4))
    _place(prog, 0x0004, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0006, _encode_li(rd=2, imm=0x40))
    _place(prog, 0x0008, _encode_slli(rd=2, shamt=2))
    _place(prog, 0x000A, _encode_srli(rd=2, shamt=4))
    _place(prog, 0x000C, _encode_sw(rs=2, imm=0x42))
    _place(prog, 0x000E, _encode_li(rd=3, imm=-16))
    _place(prog, 0x0010, _encode_srai(rd=3, shamt=2))
    _place(prog, 0x0012, _encode_sw(rs=3, imm=0x44))
    _place(prog, 0x0014, _spin(0x0014))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 400)

    v1 = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    v2 = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    v3 = _read_ram(dut, 0x0044) | (_read_ram(dut, 0x0045) << 8)
    assert v1 == 16, f"SLL.I 1<<4: expected 16, got {v1}"
    assert v2 == 0x10, f"SRL.I 0x100>>4: expected 0x10, got {v2:#06x}"
    assert v3 == 0xFFFC, f"SRA.I -16>>2: expected 0xFFFC, got {v3:#06x}"


@cocotb.test()
async def test_slt_sltu(dut):
    """SLT and SLTU comparisons."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=5))
    _place(prog, 0x0002, _encode_li(rd=2, imm=10))
    _place(prog, 0x0004, _encode_slt(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _encode_slt(rd=4, rs1=2, rs2=1))
    _place(prog, 0x000A, _encode_sw(rs=4, imm=0x42))
    _place(prog, 0x000C, _spin(0x000C))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    v1 = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    v2 = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    assert v1 == 1, f"SLT 5<10: expected 1, got {v1}"
    assert v2 == 0, f"SLT 10<5: expected 0, got {v2}"


@cocotb.test()
async def test_auipc(dut):
    """AUIPC: rd = pc + (sext(imm7) << 9)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_auipc(rd=1, imm7=0))
    _place(prog, 0x0002, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0004, _spin(0x0004))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0002, f"AUIPC 0: expected 0x0002, got {val:#06x}"


@cocotb.test()
async def test_slti_sltui_xorif(dut):
    """SLT.I, SLTU.I, XOR.IF all write result to R1."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=2, imm=5))
    _place(prog, 0x0002, _encode_slti(rs=2, imm=10))
    _place(prog, 0x0004, _encode_sw_s(rd=1, imm=0x40))
    _place(prog, 0x0006, _encode_slti(rs=2, imm=3))
    _place(prog, 0x0008, _encode_sw_s(rd=1, imm=0x42))
    _place(prog, 0x000A, _encode_xorif(rs=2, imm=5))
    _place(prog, 0x000C, _encode_sw_s(rd=1, imm=0x44))
    _place(prog, 0x000E, _encode_xorif(rs=2, imm=3))
    _place(prog, 0x0010, _encode_sw_s(rd=1, imm=0x46))
    _place(prog, 0x0012, _spin(0x0012))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    v1 = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    v2 = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    v3 = _read_ram(dut, 0x0044) | (_read_ram(dut, 0x0045) << 8)
    v4 = _read_ram(dut, 0x0046) | (_read_ram(dut, 0x0047) << 8)
    assert v1 == 1, f"SLT.I 5<10: expected 1, got {v1}"
    assert v2 == 0, f"SLT.I 5<3: expected 0, got {v2}"
    assert v3 == 0, f"XOR.IF 5^5: expected 0, got {v3}"
    assert v4 == 6, f"XOR.IF 5^3: expected 6, got {v4}"


@cocotb.test()
async def test_shift_rr(dut):
    """SLL, SRL, SRA with register shift amounts."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=1))
    _place(prog, 0x0002, _encode_li(rd=2, imm=4))
    _place(prog, 0x0004, _encode_sll(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _encode_li(rd=4, imm=1))
    _place(prog, 0x000A, _encode_slli(rd=4, shamt=8))
    _place(prog, 0x000C, _encode_srl(rd=5, rs1=4, rs2=2))
    _place(prog, 0x000E, _encode_sw(rs=5, imm=0x42))
    _place(prog, 0x0010, _spin(0x0010))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    v1 = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    v2 = _read_ram(dut, 0x0042) | (_read_ram(dut, 0x0043) << 8)
    assert v1 == 16, f"SLL 1<<4: expected 16, got {v1}"
    assert v2 == 0x10, f"SRL 0x100>>4: expected 0x10, got {v2:#06x}"


# ===========================================================================
# Corner cases
# ===========================================================================

@cocotb.test()
async def test_sub_borrow(dut):
    """SUB with borrow across 16 bits."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=1))
    _place(prog, 0x0002, _encode_li(rd=2, imm=2))
    _place(prog, 0x0004, _encode_sub(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0xFFFF, f"Expected 0xFFFF, got {val:#06x}"


@cocotb.test()
async def test_addi_negative(dut):
    """ADD.I with negative immediate."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=10))
    _place(prog, 0x0002, _encode_addi(rd=1, imm=-3))
    _place(prog, 0x0004, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 7, f"ADD.I -3 failed! Got {val}"


@cocotb.test()
async def test_addi_overflow(dut):
    """ADD.I overflow wraps at 16 bits."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0030] = 0xFE; prog[0x0031] = 0xFF
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x30))
    _place(prog, 0x0002, _encode_addi(rd=1, imm=3))
    _place(prog, 0x0004, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0001, f"ADD.I overflow! Got {val:#06x}"


@cocotb.test()
async def test_lui_negative(dut):
    """LUI with negative imm7: sext(-1) << 9 = 0xFE00."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_lui(rd=1, imm7=-1))
    _place(prog, 0x0002, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0004, _spin(0x0004))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0xFE00, f"LUI -1 failed! Got {val:#06x}"


@cocotb.test()
async def test_lui_zero(dut):
    """LUI with imm7=0: result = 0x0000."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=0x42))
    _place(prog, 0x0002, _encode_lui(rd=1, imm7=0))
    _place(prog, 0x0004, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0xFF; prog[0x0041] = 0xFF
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0000, f"LUI 0 failed! Got {val:#06x}"


@cocotb.test()
async def test_sll_by_8(dut):
    """SLL by 8: crosses byte boundary."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=0x2B))
    _place(prog, 0x0002, _encode_li(rd=2, imm=8))
    _place(prog, 0x0004, _encode_sll(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x2B00, f"SLL by 8 failed! Got {val:#06x}"


@cocotb.test()
async def test_sra_negative(dut):
    """SRA preserves sign bit for negative numbers."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0030] = 0x00; prog[0x0031] = 0xFF
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x30))
    _place(prog, 0x0002, _encode_li(rd=2, imm=4))
    _place(prog, 0x0004, _encode_sra(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0xFFF0, f"SRA negative failed! Got {val:#06x}"


@cocotb.test()
async def test_slli_by_8(dut):
    """SLL.I by 8 crosses byte boundary."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=0x2B))
    _place(prog, 0x0002, _encode_slli(rd=1, shamt=8))
    _place(prog, 0x0004, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x2B00, f"SLL.I by 8 failed! Got {val:#06x}"


@cocotb.test()
async def test_srai_negative(dut):
    """SRA.I preserves sign for negative numbers."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0030] = 0x00; prog[0x0031] = 0x80
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x30))
    _place(prog, 0x0002, _encode_srai(rd=1, shamt=4))
    _place(prog, 0x0004, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0xF800, f"SRA.I negative failed! Got {val:#06x}"


@cocotb.test()
async def test_li_negative(dut):
    """LI with negative immediate: sext(-1) = 0xFFFF."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=-1))
    _place(prog, 0x0002, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0004, _spin(0x0004))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0xFFFF, f"LI -1 failed! Got {val:#06x}"


@cocotb.test()
async def test_alu_same_reg(dut):
    """ADD rd, rs, rs with same source doubles the value."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=0x42))
    _place(prog, 0x0002, _encode_add(rd=2, rs1=1, rs2=1))
    _place(prog, 0x0004, _encode_sw(rs=2, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0084, f"ADD same reg failed! Got {val:#06x}"


@cocotb.test()
async def test_and_basic(dut):
    """AND R3, R1, R2: 0xFF0F & 0x0FFF = 0x0F0F."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0x0F; prog[0x0011] = 0xFF
    prog[0x0012] = 0xFF; prog[0x0013] = 0x0F
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_lw(rd=2, imm=0x12))
    _place(prog, 0x0004, _encode_and_rr(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0F0F, f"Expected 0x0F0F, got {val:#06x}"


@cocotb.test()
async def test_or_basic(dut):
    """OR R3, R1, R2: 0xF000 | 0x00F0 = 0xF0F0."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0x00; prog[0x0011] = 0xF0
    prog[0x0012] = 0xF0; prog[0x0013] = 0x00
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_lw(rd=2, imm=0x12))
    _place(prog, 0x0004, _encode_or_rr(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0xF0F0, f"Expected 0xF0F0, got {val:#06x}"


@cocotb.test()
async def test_xor_basic(dut):
    """XOR R3, R1, R2: 0xFFFF ^ 0xAAAA = 0x5555."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0xFF; prog[0x0011] = 0xFF
    prog[0x0012] = 0xAA; prog[0x0013] = 0xAA
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_lw(rd=2, imm=0x12))
    _place(prog, 0x0004, _encode_xor_rr(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x5555, f"Expected 0x5555, got {val:#06x}"


@cocotb.test()
async def test_slt_equal(dut):
    """SLT: 5 < 5 -> 0."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=5))
    _place(prog, 0x0002, _encode_li(rd=2, imm=5))
    _place(prog, 0x0004, _encode_slt(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"


@cocotb.test()
async def test_slt_negative(dut):
    """SLT: -5 < 5 -> 1 (signed)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=-5))
    _place(prog, 0x0002, _encode_li(rd=2, imm=5))
    _place(prog, 0x0004, _encode_slt(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0001, f"Expected 0x0001, got {val:#06x}"


@cocotb.test()
async def test_slt_negative_false(dut):
    """SLT: 5 < -5 -> 0 (signed)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=5))
    _place(prog, 0x0002, _encode_li(rd=2, imm=-5))
    _place(prog, 0x0004, _encode_slt(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"


@cocotb.test()
async def test_sltu_true(dut):
    """SLTU: 5 <u 10 -> 1."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=5))
    _place(prog, 0x0002, _encode_li(rd=2, imm=10))
    _place(prog, 0x0004, _encode_sltu(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0001, f"Expected 0x0001, got {val:#06x}"


@cocotb.test()
async def test_sltu_false(dut):
    """SLTU: 10 <u 5 -> 0."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=10))
    _place(prog, 0x0002, _encode_li(rd=2, imm=5))
    _place(prog, 0x0004, _encode_sltu(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"


@cocotb.test()
async def test_sltu_large(dut):
    """SLTU: 5 <u 0xFFFF -> 1."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=5))
    _place(prog, 0x0002, _encode_li(rd=2, imm=-1))
    _place(prog, 0x0004, _encode_sltu(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0001, f"Expected 0x0001, got {val:#06x}"


@cocotb.test()
async def test_sltu_large_reverse(dut):
    """SLTU: 0xFFFF <u 5 -> 0."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=-1))
    _place(prog, 0x0002, _encode_li(rd=2, imm=5))
    _place(prog, 0x0004, _encode_sltu(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"


@cocotb.test()
async def test_li_zero(dut):
    """LI R1, 0 -> 0x0000."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=42))
    _place(prog, 0x0002, _encode_li(rd=1, imm=0))
    _place(prog, 0x0004, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0xFF; prog[0x0041] = 0xFF
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"


@cocotb.test()
async def test_andi_all_ones(dut):
    """AND.I R1, 0xFF: 0xABCD & 0x00FF = 0x00CD (zero-ext imm8)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0xCD; prog[0x0011] = 0xAB
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_andi(rd=1, imm=0xFF))
    _place(prog, 0x0004, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x00CD, f"Expected 0x00CD, got {val:#06x}"


@cocotb.test()
async def test_ori_all_ones(dut):
    """OR.I R1, 0xFF: 0x1234 | 0x00FF = 0x12FF (zero-ext imm8)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0x34; prog[0x0011] = 0x12
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_ori(rd=1, imm=0xFF))
    _place(prog, 0x0004, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x12FF, f"Expected 0x12FF, got {val:#06x}"


@cocotb.test()
async def test_xori_all_ones(dut):
    """XOR.I R1, 0xFF: 0x1234 ^ 0x00FF = 0x12CB (zero-ext imm8)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0x34; prog[0x0011] = 0x12
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_xori(rd=1, imm=0xFF))
    _place(prog, 0x0004, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x12CB, f"Expected 0x12CB, got {val:#06x}"


@cocotb.test()
async def test_slti_negative(dut):
    """SLT.I: -2 < -1 -> R1 = 1 (signed compare)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=-2))
    _place(prog, 0x0002, _encode_slti(rs=1, imm=-1))
    _place(prog, 0x0004, _encode_sw_s(rd=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0001, f"Expected 0x0001, got {val:#06x}"


@cocotb.test()
async def test_slti_equal(dut):
    """SLT.I: 5 < 5 -> R1 = 0."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=5))
    _place(prog, 0x0002, _encode_slti(rs=1, imm=5))
    _place(prog, 0x0004, _encode_sw_s(rd=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0xFF; prog[0x0041] = 0xFF
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"


@cocotb.test()
async def test_sltui_true(dut):
    """SLTU.I: 5 <u 10 -> R1 = 1."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=5))
    _place(prog, 0x0002, _encode_sltui(rs=1, imm=10))
    _place(prog, 0x0004, _encode_sw_s(rd=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0001, f"Expected 0x0001, got {val:#06x}"


@cocotb.test()
async def test_sltui_false(dut):
    """SLTU.I: 0xFFFF <u sext(3)=3 -> R1 = 0."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=-1))
    _place(prog, 0x0002, _encode_sltui(rs=1, imm=3))
    _place(prog, 0x0004, _encode_sw_s(rd=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0xFF; prog[0x0041] = 0xFF
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"


@cocotb.test()
async def test_xorif_equality(dut):
    """XOR.IF: 5 ^ 5 = 0 (equality test pattern)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=5))
    _place(prog, 0x0002, _encode_xorif(rs=1, imm=5))
    _place(prog, 0x0004, _encode_sw_s(rd=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0xFF; prog[0x0041] = 0xFF
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"


@cocotb.test()
async def test_sll_by_zero(dut):
    """SLL: 0xABCD << 0 = 0xABCD (identity)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0xCD; prog[0x0011] = 0xAB
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_li(rd=2, imm=0))
    _place(prog, 0x0004, _encode_sll(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0xABCD, f"Expected 0xABCD, got {val:#06x}"


@cocotb.test()
async def test_sll_by_15(dut):
    """SLL: 0x0001 << 15 = 0x8000."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=1))
    _place(prog, 0x0002, _encode_li(rd=2, imm=15))
    _place(prog, 0x0004, _encode_sll(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x8000, f"Expected 0x8000, got {val:#06x}"


@cocotb.test()
async def test_sll_cross_byte(dut):
    """SLL: 0x0037 << 9 = 0x6E00 (crosses byte boundary)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=0x37))
    _place(prog, 0x0002, _encode_li(rd=2, imm=9))
    _place(prog, 0x0004, _encode_sll(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x6E00, f"Expected 0x6E00, got {val:#06x}"


@cocotb.test()
async def test_srl_by_zero(dut):
    """SRL: 0xABCD >> 0 = 0xABCD (identity)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0xCD; prog[0x0011] = 0xAB
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_li(rd=2, imm=0))
    _place(prog, 0x0004, _encode_srl(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0xABCD, f"Expected 0xABCD, got {val:#06x}"


@cocotb.test()
async def test_srl_by_8(dut):
    """SRL: 0xAB00 >> 8 = 0x00AB."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0x00; prog[0x0011] = 0xAB
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_li(rd=2, imm=8))
    _place(prog, 0x0004, _encode_srl(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x00AB, f"Expected 0x00AB, got {val:#06x}"


@cocotb.test()
async def test_srl_by_15(dut):
    """SRL: 0x8000 >> 15 = 0x0001."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0x00; prog[0x0011] = 0x80
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_li(rd=2, imm=15))
    _place(prog, 0x0004, _encode_srl(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0001, f"Expected 0x0001, got {val:#06x}"


@cocotb.test()
async def test_sra_positive(dut):
    """SRA: 0x1234 >>s 4 = 0x0123 (positive, zero-fills)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0x34; prog[0x0011] = 0x12
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_li(rd=2, imm=4))
    _place(prog, 0x0004, _encode_sra(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0123, f"Expected 0x0123, got {val:#06x}"


@cocotb.test()
async def test_sra_by_8_negative(dut):
    """SRA: 0x8000 >>s 8 = 0xFF80 (sign extends across byte boundary)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0x00; prog[0x0011] = 0x80
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_li(rd=2, imm=8))
    _place(prog, 0x0004, _encode_sra(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0xFF80, f"Expected 0xFF80, got {val:#06x}"


@cocotb.test()
async def test_sra_by_15_negative(dut):
    """SRA: 0x8000 >>s 15 = 0xFFFF."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0x00; prog[0x0011] = 0x80
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_li(rd=2, imm=15))
    _place(prog, 0x0004, _encode_sra(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs=3, imm=0x40))
    _place(prog, 0x0008, _spin(0x0008))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0xFFFF, f"Expected 0xFFFF, got {val:#06x}"


@cocotb.test()
async def test_srli_by_8(dut):
    """SRL.I R1, 8: 0xAB00 >> 8 = 0x00AB."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0x00; prog[0x0011] = 0xAB
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_srli(rd=1, shamt=8))
    _place(prog, 0x0004, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x00AB, f"Expected 0x00AB, got {val:#06x}"


@cocotb.test()
async def test_srai_by_15_negative(dut):
    """SRA.I R1, 15: 0x8000 >>s 15 = 0xFFFF."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0x00; prog[0x0011] = 0x80
    _place(prog, 0x0000, _encode_lw(rd=1, imm=0x10))
    _place(prog, 0x0002, _encode_srai(rd=1, shamt=15))
    _place(prog, 0x0004, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0xFFFF, f"Expected 0xFFFF, got {val:#06x}"


@cocotb.test()
async def test_auipc_positive_offset(dut):
    """AUIPC with positive imm7: R1 = PC+2 + (1 << 9) = 0x0002 + 0x0200 = 0x0202."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_auipc(rd=1, imm7=1))
    _place(prog, 0x0002, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0004, _spin(0x0004))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x0202, f"Expected 0x0202, got {val:#06x}"


@cocotb.test()
async def test_auipc_negative_offset(dut):
    """AUIPC at 0x0080 with imm7=-1: R1 = 0x0082 + (-1 << 9) = 0xFE82."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_j(off10=63))
    _place(prog, 0x0080, _encode_auipc(rd=2, imm7=-1))
    _place(prog, 0x0082, _encode_sw(rs=2, imm=0x50))
    _place(prog, 0x0084, _spin(0x0084))
    prog[0x0050] = 0x00; prog[0x0051] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 400)
    val = _read_ram(dut, 0x0050) | (_read_ram(dut, 0x0051) << 8)
    assert val == 0xFE82, f"Expected 0xFE82, got {val:#06x}"


@cocotb.test()
async def test_auipc_with_lw(dut):
    """AUIPC + LW for PC-relative data access (primary use case)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    prog[0x0010] = 0xEF; prog[0x0011] = 0xBE
    # AUIPC R0, 0 → R0 = 0x0002 (sets base for subsequent LW)
    _place(prog, 0x0000, _encode_auipc(rd=0, imm7=0))
    # LW R1, 0x0E → R1 = mem16[R0+0x0E] = mem16[0x0010] = 0xBEEF
    _place(prog, 0x0002, _encode_lw(rd=1, imm=0x0E))
    # R0 is non-zero; use SW.S to store via R7
    _place(prog, 0x0004, _encode_sw_s(rd=1, imm=0x40))
    _place(prog, 0x0006, _spin(0x0006))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0xBEEF, f"Expected 0xBEEF, got {val:#06x}"


@cocotb.test()
async def test_auipc_large_imm7(dut):
    """AUIPC with large imm7 (63): R1 = 0x0002 + (63 << 9) = 0x7E02."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    _place(prog, 0x0000, _encode_auipc(rd=1, imm7=63))
    _place(prog, 0x0002, _encode_sw(rs=1, imm=0x40))
    _place(prog, 0x0004, _spin(0x0004))
    prog[0x0040] = 0x00; prog[0x0041] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)
    val = _read_ram(dut, 0x0040) | (_read_ram(dut, 0x0041) << 8)
    assert val == 0x7E02, f"Expected 0x7E02, got {val:#06x}"
