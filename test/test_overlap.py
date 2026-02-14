# SPDX-FileCopyrightText: © 2024 mysterymath
# SPDX-License-Identifier: Apache-2.0
#
# Register overlap tests: verify correct behavior when destination register
# overlaps a source register. Tests both known-buggy cases and safe cases.

from test_helpers import *


# ===========================================================================
# Helper: load a 16-bit value into a register via memory
# ===========================================================================
def _load_r16(prog, pc, data_addr, rd, val):
    """Emit instructions to load 16-bit val into Rd. Returns next pc.
    data_addr must be < 0x80 (signed 8-bit offset from R7=0)."""
    assert data_addr < 0x80, f"data_addr {data_addr:#x} must be < 0x80"
    prog[data_addr] = val & 0xFF
    prog[data_addr + 1] = (val >> 8) & 0xFF
    _place(prog, pc, _encode_lw(rs=7, imm=data_addr))
    _place(prog, pc + 2, _encode_or_rr(rd=rd, rs1=0, rs2=0))
    return pc + 4


def _store_r16(prog, pc, store_addr, rs):
    """Emit instructions to store Rs to memory. Returns next pc.
    store_addr must be < 0x80."""
    assert store_addr < 0x80, f"store_addr {store_addr:#x} must be < 0x80"
    _place(prog, pc, _encode_or_rr(rd=0, rs1=rs, rs2=rs))
    _place(prog, pc + 2, _encode_sw(rs=7, imm=store_addr))
    return pc + 4


# Data slots at 0x60+, output slots at 0x40+.
# Each test uses at most 2 data slots (4 bytes) and 1 output slot (2 bytes).

# ===========================================================================
# Known bugs: SLT/SLTU with rd overlapping an operand
# ===========================================================================

@cocotb.test()
async def test_slt_rd_eq_rs1(dut):
    """SLT R1, R1, R2: rd == rs1. Hi byte of rs1 corrupted by pre-clear."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    # R1 = 0x0100 (256), R2 = 0x0080 (128). Signed: 256 < 128 = false → 0.
    # Bug: R1_hi cleared to 0 → comparison becomes 0 < 128 = true → 1.
    pc = _load_r16(prog, 0x0000, 0x60, rd=1, val=0x0100)
    pc = _load_r16(prog, pc, 0x62, rd=2, val=0x0080)
    _place(prog, pc, _encode_slt(rd=1, rs1=1, rs2=2)); pc += 2
    pc = _store_r16(prog, pc, 0x40, rs=1)
    _place(prog, pc, _spin())
    prog[0x40] = 0xFF; prog[0x41] = 0xFF
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x40) | (_read_ram(dut, 0x41) << 8)
    assert val == 0x0000, f"SLT rd==rs1: expected 0 (256 not < 128), got {val:#06x}"


@cocotb.test()
async def test_sltu_rd_eq_rs2(dut):
    """SLTU R2, R1, R2: rd == rs2. Hi byte of rs2 corrupted by pre-clear."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    # R1 = 0x0080, R2 = 0x0100. Unsigned: 0x0080 <u 0x0100 = true → 1.
    # Bug: R2_hi cleared → comparison becomes 0x0080 <u 0x0000 = false → 0.
    pc = _load_r16(prog, 0x0000, 0x60, rd=1, val=0x0080)
    pc = _load_r16(prog, pc, 0x62, rd=2, val=0x0100)
    _place(prog, pc, _encode_sltu(rd=2, rs1=1, rs2=2)); pc += 2
    pc = _store_r16(prog, pc, 0x40, rs=2)
    _place(prog, pc, _spin())
    prog[0x40] = 0xFF; prog[0x41] = 0xFF
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x40) | (_read_ram(dut, 0x41) << 8)
    assert val == 0x0001, f"SLTU rd==rs2: expected 1 (0x0080 <u 0x0100), got {val:#06x}"


@cocotb.test()
async def test_slti_source_r0(dut):
    """SLTI with source R0: rd=R0 always, so R0_hi corrupted if nonzero."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    # R0 = 0x0100 (256). SLTI R0, 1: signed 256 < 1 → false → 0.
    # Bug: R0_hi cleared → comparison becomes 0 < 1 → true → 1.
    pc = _load_r16(prog, 0x0000, 0x60, rd=0, val=0x0100)
    _place(prog, pc, _encode_slti(rs=0, imm=1)); pc += 2
    _place(prog, pc, _encode_sw(rs=7, imm=0x40)); pc += 2
    _place(prog, pc, _spin())
    prog[0x40] = 0xFF; prog[0x41] = 0xFF
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x40) | (_read_ram(dut, 0x41) << 8)
    assert val == 0x0000, f"SLTI R0: expected 0 (256 not < 1), got {val:#06x}"


@cocotb.test()
async def test_sll_rr_rd_eq_rs2(dut):
    """SLL R2, R1, R2: rd == rs2 (shift amount). Shift amount corrupted."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    # R1 = 0x00FF, R2 = 0x0004 (shamt=4). Expected: 0x00FF << 4 = 0x0FF0.
    # Bug: R2_lo overwritten with shifted lo byte, corrupts shamt in HI cycle.
    pc = _load_r16(prog, 0x0000, 0x60, rd=1, val=0x00FF)
    pc = _load_r16(prog, pc, 0x62, rd=2, val=0x0004)
    _place(prog, pc, _encode_sll(rd=2, rs1=1, rs2=2)); pc += 2
    pc = _store_r16(prog, pc, 0x40, rs=2)
    _place(prog, pc, _spin())
    prog[0x40] = 0x00; prog[0x41] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x40) | (_read_ram(dut, 0x41) << 8)
    assert val == 0x0FF0, f"SLL rd==rs2: expected 0x0FF0, got {val:#06x}"


# ===========================================================================
# Safe cases: verify no corruption
# ===========================================================================

@cocotb.test()
async def test_add_rd_eq_rs1(dut):
    """ADD R1, R1, R2: rd == rs1. Safe (writes lo, reads hi next cycle)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    # R1 = 0x1234, R2 = 0x4321. Expected: 0x5555.
    pc = _load_r16(prog, 0x0000, 0x60, rd=1, val=0x1234)
    pc = _load_r16(prog, pc, 0x62, rd=2, val=0x4321)
    _place(prog, pc, _encode_add(rd=1, rs1=1, rs2=2)); pc += 2
    pc = _store_r16(prog, pc, 0x40, rs=1)
    _place(prog, pc, _spin())
    prog[0x40] = 0x00; prog[0x41] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x40) | (_read_ram(dut, 0x41) << 8)
    assert val == 0x5555, f"ADD rd==rs1: expected 0x5555, got {val:#06x}"


@cocotb.test()
async def test_sub_rd_eq_rs2(dut):
    """SUB R2, R1, R2: rd == rs2. Safe (writes lo, reads hi next cycle)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    # R1 = 0x5555, R2 = 0x1234. Expected: 0x4321.
    pc = _load_r16(prog, 0x0000, 0x60, rd=1, val=0x5555)
    pc = _load_r16(prog, pc, 0x62, rd=2, val=0x1234)
    _place(prog, pc, _encode_sub(rd=2, rs1=1, rs2=2)); pc += 2
    pc = _store_r16(prog, pc, 0x40, rs=2)
    _place(prog, pc, _spin())
    prog[0x40] = 0x00; prog[0x41] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x40) | (_read_ram(dut, 0x41) << 8)
    assert val == 0x4321, f"SUB rd==rs2: expected 0x4321, got {val:#06x}"


@cocotb.test()
async def test_addi_self(dut):
    """ADDI R1, 3: always self-overlapping. Safe (lo then hi)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    # R1 = 0x0100. ADDI R1, 3. Expected: 0x0103.
    pc = _load_r16(prog, 0x0000, 0x60, rd=1, val=0x0100)
    _place(prog, pc, _encode_addi(rd=1, imm=3)); pc += 2
    pc = _store_r16(prog, pc, 0x40, rs=1)
    _place(prog, pc, _spin())
    prog[0x40] = 0x00; prog[0x41] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x40) | (_read_ram(dut, 0x41) << 8)
    assert val == 0x0103, f"ADDI self: expected 0x0103, got {val:#06x}"


@cocotb.test()
async def test_srl_rr_rd_eq_rs2(dut):
    """SRL R2, R1, R2: rd == rs2 (shamt reg). Safe (writes hi, reads lo)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    # R1 = 0x8000, R2 = 0x0004. Expected: 0x8000 >>u 4 = 0x0800.
    pc = _load_r16(prog, 0x0000, 0x60, rd=1, val=0x8000)
    pc = _load_r16(prog, pc, 0x62, rd=2, val=0x0004)
    _place(prog, pc, _encode_srl(rd=2, rs1=1, rs2=2)); pc += 2
    pc = _store_r16(prog, pc, 0x40, rs=2)
    _place(prog, pc, _spin())
    prog[0x40] = 0x00; prog[0x41] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x40) | (_read_ram(dut, 0x41) << 8)
    assert val == 0x0800, f"SRL rd==rs2: expected 0x0800, got {val:#06x}"


@cocotb.test()
async def test_slli_self(dut):
    """SLLI R1, 4: R,4 always self-overlapping. Safe (uses tmp)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    # R1 = 0x00FF. SLLI R1, 4. Expected: 0x0FF0.
    pc = _load_r16(prog, 0x0000, 0x60, rd=1, val=0x00FF)
    _place(prog, pc, _encode_slli(rd=1, shamt=4)); pc += 2
    pc = _store_r16(prog, pc, 0x40, rs=1)
    _place(prog, pc, _spin())
    prog[0x40] = 0x00; prog[0x41] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x40) | (_read_ram(dut, 0x41) << 8)
    assert val == 0x0FF0, f"SLLI self: expected 0x0FF0, got {val:#06x}"


@cocotb.test()
async def test_jalr_link_overlap(dut):
    """JALR R6: jump target register == link register. Safe (lo/hi split)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    # R6 = 0x0040. JALR R6, 0 → jump to 0x0040, save return addr to R6.
    pc = _load_r16(prog, 0x0000, 0x60, rd=6, val=0x0040)
    jalr_pc = pc
    _place(prog, pc, _encode_jalr(rs=6, imm=0)); pc += 2
    # At 0x0040: store R6 (should be return address = jalr_pc + 2)
    _place(prog, 0x0040, _encode_or_rr(rd=0, rs1=6, rs2=6))
    _place(prog, 0x0042, _encode_sw(rs=7, imm=0x50))
    _place(prog, 0x0044, _spin())
    prog[0x50] = 0x00; prog[0x51] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x50) | (_read_ram(dut, 0x51) << 8)
    expected = jalr_pc + 2
    assert val == expected, f"JALR R6: expected {expected:#06x}, got {val:#06x}"


@cocotb.test()
async def test_lw_rr_rd_eq_rs(dut):
    """LW.RR R1, R1: rd == rs. Load overwrites pointer (defined behavior)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    # R1 = 0x0030. Memory at 0x0030 = 0xBEEF.
    # Load from [R1] into R1 → R1 = 0xBEEF (pointer lost, data correct).
    prog[0x30] = 0xEF; prog[0x31] = 0xBE
    _place(prog, 0x0000, _encode_li(rd=1, imm=0x30))
    _place(prog, 0x0002, _encode_lw_rr(rd=1, rs=1))
    pc = _store_r16(prog, 0x0004, 0x40, rs=1)
    _place(prog, pc, _spin())
    prog[0x40] = 0x00; prog[0x41] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x40) | (_read_ram(dut, 0x41) << 8)
    assert val == 0xBEEF, f"LW.RR rd==rs: expected 0xBEEF, got {val:#06x}"


@cocotb.test()
async def test_lw_a_rd_eq_rs(dut):
    """LW.A R1, R1: rd == rs. Load data overwrites increment (defined)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    prog = {}
    # R1 = 0x0030. Memory at 0x0030 = 0xBEEF.
    # LW.A: increment R1 to 0x0032, then load from [0x0030] into R1 → R1 = 0xBEEF.
    prog[0x30] = 0xEF; prog[0x31] = 0xBE
    _place(prog, 0x0000, _encode_li(rd=1, imm=0x30))
    _place(prog, 0x0002, _encode_lw_a(rd=1, rs=1))
    pc = _store_r16(prog, 0x0004, 0x40, rs=1)
    _place(prog, pc, _spin())
    prog[0x40] = 0x00; prog[0x41] = 0x00
    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)
    val = _read_ram(dut, 0x40) | (_read_ram(dut, 0x41) << 8)
    assert val == 0xBEEF, f"LW.A rd==rs: expected 0xBEEF, got {val:#06x}"
