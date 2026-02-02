# SPDX-FileCopyrightText: © 2024 mysterymath
# SPDX-License-Identifier: Apache-2.0
#
# Cocotb tests for RISCY-V02 "Byte Byte Jump" core.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge


async def _reset(dut):
    """Apply reset sequence."""
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1


def _load_program(dut, program):
    """Load a program dict {addr: byte} into RAM."""
    for addr, val in program.items():
        dut.ram[addr].value = val


def _read_ram(dut, addr):
    return int(dut.ram[addr].value)


def _encode_lw(rd, rs1, off6):
    """Encode LW rd, off6(rs1) -> 16-bit little-endian bytes."""
    assert -32 <= off6 <= 31, f"off6 out of range: {off6}"
    assert 0 <= rd <= 7 and 0 <= rs1 <= 7
    off6 &= 0x3F
    insn = (0b1000 << 12) | (rs1 << 9) | (off6 << 3) | rd
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_sw(rs2, rs1, off6):
    """Encode SW rs2, off6(rs1) -> 16-bit little-endian bytes."""
    assert -32 <= off6 <= 31, f"off6 out of range: {off6}"
    assert 0 <= rs2 <= 7 and 0 <= rs1 <= 7
    off6 &= 0x3F
    insn = (0b1010 << 12) | (rs1 << 9) | (off6 << 3) | rs2
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_jr(rs, off6):
    """Encode JR rs, off6 -> 16-bit little-endian bytes."""
    assert -32 <= off6 <= 31, f"off6 out of range: {off6}"
    assert 0 <= rs <= 7
    off6 &= 0x3F
    insn = (0b1011100 << 9) | (off6 << 3) | rs
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _place(prog, addr, bytepair):
    """Place a 2-byte instruction at addr."""
    prog[addr] = bytepair[0]
    prog[addr + 1] = bytepair[1]


# ---------------------------------------------------------------------------
# Test 1: LW + SW + JR basic
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_lw_sw_jr_basic(dut):
    """LW from memory, SW to memory, JR to spin loop."""
    dut._log.info("Test 1: LW + SW + JR basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # Data at 0x0010: LE word 0x1234 (low byte 0x34 at 0x10, high 0x12 at 0x11)
    prog[0x0010] = 0x34
    prog[0x0011] = 0x12

    # 0x0000: LW R1, 8(R0)   ; R1 = MEM[0 + 8*2] = MEM[0x10] = 0x1234
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=8))
    # 0x0002: SW R1, 9(R0)   ; MEM[0 + 9*2] = MEM[0x12] = R1 = 0x1234
    _place(prog, 0x0002, _encode_sw(rs2=1, rs1=0, off6=9))
    # 0x0004: JR R0, 2       ; PC = 0 + 2*2 = 4 (spin at 0x0004)
    _place(prog, 0x0004, _encode_jr(rs=0, off6=2))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0012)
    hi = _read_ram(dut, 0x0013)
    dut._log.info(f"ram[0x12]={lo:#04x}, ram[0x13]={hi:#04x}")
    assert lo == 0x34, f"Expected 0x34 at 0x0012, got {lo:#04x}"
    assert hi == 0x12, f"Expected 0x12 at 0x0013, got {hi:#04x}"
    dut._log.info("PASS [lw_sw_jr_basic]")


# ---------------------------------------------------------------------------
# Test 2: JR with computed target
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_jr_computed(dut):
    """Load an address into a register, then JR to it."""
    dut._log.info("Test 2: JR with computed target")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # Data at 0x0020: LE word 0x0010 (target address)
    prog[0x0020] = 0x10
    prog[0x0021] = 0x00

    # At 0x0010: SW R2, 0(R0) to addr 0x0000 (marker), then spin
    # But R2 needs a value. Let's use a simpler approach:
    # At 0x0010: store a known value, then spin.

    # Data at 0x0030: LE word 0xBEEF
    prog[0x0030] = 0xEF
    prog[0x0031] = 0xBE

    # 0x0000: LW R1, 16(R0)  ; R1 = MEM[32] = MEM[0x20] = 0x0010
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    # 0x0002: LW R2, 24(R0)  ; R2 = MEM[48] = MEM[0x30] = 0xBEEF
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=24))
    # 0x0004: JR R1, 0       ; PC = R1 + 0 = 0x0010
    _place(prog, 0x0004, _encode_jr(rs=1, off6=0))

    # At target 0x0010: SW R2, 20(R0) ; MEM[40] = MEM[0x28] = 0xBEEF
    _place(prog, 0x0010, _encode_sw(rs2=2, rs1=0, off6=20))
    # 0x0012: JR R0, 9       ; PC = 0 + 9*2 = 18 = 0x12 (spin)
    _place(prog, 0x0012, _encode_jr(rs=0, off6=9))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0028)
    hi = _read_ram(dut, 0x0029)
    val = lo | (hi << 8)
    dut._log.info(f"ram[0x28:0x29] = {val:#06x}")
    assert val == 0xBEEF, f"Expected 0xBEEF, got {val:#06x}"
    dut._log.info("PASS [jr_computed]")


# ---------------------------------------------------------------------------
# Test 3: Multiple registers, negative offsets
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_negative_offsets(dut):
    """Use negative offsets and multiple registers."""
    dut._log.info("Test 3: Multiple registers, negative offsets")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Bootstrap: load a base address into R3 using two LWs.
    # Data at 0x0020: LE word 0x0050 (base address)
    prog[0x0020] = 0x50
    prog[0x0021] = 0x00

    # Data at 0x004E: LE word 0xCAFE (at base 0x50, offset -1 → addr 0x4E)
    prog[0x004E] = 0xFE
    prog[0x004F] = 0xCA

    # 0x0000: LW R3, 16(R0)  ; R3 = MEM[0 + 16*2] = MEM[0x20] = 0x0050
    _place(prog, 0x0000, _encode_lw(rd=3, rs1=0, off6=16))
    # 0x0002: LW R4, -1(R3)  ; R4 = MEM[0x50 + (-1)*2] = MEM[0x4E] = 0xCAFE
    _place(prog, 0x0002, _encode_lw(rd=4, rs1=3, off6=-1))
    # 0x0004: SW R4, 1(R3)   ; MEM[0x50 + 1*2] = MEM[0x52] = 0xCAFE
    _place(prog, 0x0004, _encode_sw(rs2=4, rs1=3, off6=1))
    # 0x0006: JR R0, 3       ; PC = 0 + 3*2 = 6 (spin at 0x0006)
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    lo = _read_ram(dut, 0x0052)
    hi = _read_ram(dut, 0x0053)
    val = lo | (hi << 8)
    dut._log.info(f"ram[0x52:0x53] = {val:#06x}")
    assert val == 0xCAFE, f"Expected 0xCAFE, got {val:#06x}"
    dut._log.info("PASS [negative_offsets]")


# ---------------------------------------------------------------------------
# Test 4: JR zero-stall timing
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_jr_zero_stall(dut):
    """Verify JR does not introduce stall cycles."""
    dut._log.info("Test 4: JR zero-stall timing")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Data at 0x0008: LE word 0x0020 (JR target address)
    prog[0x0008] = 0x20
    prog[0x0009] = 0x00

    # 0x0000: LW R1, 4(R0)   ; R1 = MEM[0x08] = 0x0020
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=4))
    # 0x0002: JR R1, 0        ; jump to 0x0020 (zero-stall)
    _place(prog, 0x0002, _encode_jr(rs=1, off6=0))
    # 0x0020: SW R0, 10(R0)   ; MEM[0x14] = 0 (marker write)
    _place(prog, 0x0020, _encode_sw(rs2=0, rs1=0, off6=10))
    # 0x0022: JR R0, 17       ; spin at 0x0022
    _place(prog, 0x0022, _encode_jr(rs=0, off6=17))

    # Pre-fill marker location with non-zero so we can detect the write
    prog[0x0014] = 0xFF
    prog[0x0015] = 0xFF

    _load_program(dut, prog)
    await _reset(dut)

    # Monitor negedges to find when the SW write lands.
    # Expected timeline (negedge-relative, cycle 0 = first negedge after reset):
    #   0-1: fetch LW
    #   2-3: exec LW (E_LOAD_LO, E_LOAD_HI), fetch stalled
    #   4-5: fetch JR (F_LO, F_HI; redirects fetch_addr to 0x0020)
    #   6-7: fetch SW at 0x0020 (F_LO, F_HI)
    #   8-9: exec SW (E_STORE_LO, E_STORE_HI — write visible at negedge 9)
    write_cycle = None
    for cycle in range(30):
        await FallingEdge(dut.clk)
        lo = _read_ram(dut, 0x0014)
        hi = _read_ram(dut, 0x0015)
        if lo != 0xFF or hi != 0xFF:
            write_cycle = cycle
            break

    dut._log.info(f"SW write detected at negedge cycle {write_cycle}")
    assert write_cycle == 10, \
        f"Expected SW write at cycle 10 (zero-stall JR), got cycle {write_cycle}"
    dut._log.info("PASS [jr_zero_stall]")
