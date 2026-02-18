# SPDX-FileCopyrightText: © 2024 mysterymath
# SPDX-License-Identifier: Apache-2.0
#
# Shared test infrastructure for RISCY-V02 cocotb tests.
#
# Register convention: R0 is used as a zero-base address register throughout
# tests. After reset, all registers are 0, so R0 starts at 0 and is kept at 0
# for R,8 loads/stores (which use R0 as implicit base). R,8 loads write to
# ir[2:0] (rd); R,8 stores read data from ir[2:0] (rs). SP variants use R7.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

__all__ = [
    'cocotb', 'Clock', 'ClockCycles', 'FallingEdge',
    '_reset', '_load_program', '_read_ram', '_place', '_set_ui', '_spin',
    '_measure_instruction_cycles',
    '_encode_r8', '_encode_addi', '_encode_li',
    '_encode_lw', '_encode_lb', '_encode_lbu', '_encode_sw', '_encode_sb',
    '_encode_jr', '_encode_jalr',
    '_encode_andi', '_encode_ori', '_encode_xori',
    '_encode_slti', '_encode_sltui', '_encode_bz', '_encode_bnz', '_encode_xorif', '_encode_andif',
    '_encode_r7', '_encode_lui', '_encode_auipc',
    '_encode_10', '_encode_j', '_encode_jal',
    '_encode_rrr', '_encode_add', '_encode_sub',
    '_encode_and_rr', '_encode_or_rr', '_encode_xor_rr',
    '_encode_slt', '_encode_sltu', '_encode_sll', '_encode_srl', '_encode_sra',
    '_encode_r4', '_encode_slli', '_encode_srli', '_encode_srai',
    '_encode_rr',
    '_encode_lw_rr', '_encode_lb_rr', '_encode_lbu_rr',
    '_encode_sw_rr', '_encode_sb_rr',
    '_encode_sys', '_encode_sei', '_encode_cli', '_encode_reti',
    '_encode_epcr', '_encode_epcw',
    '_encode_brk', '_encode_wai', '_encode_stp', '_encode_nop',
    '_encode_lw_s', '_encode_lb_s', '_encode_lbu_s',
    '_encode_sw_s', '_encode_sb_s',
]


async def _reset(dut):
    """Apply reset sequence."""
    dut.ena.value = 1
    dut.ui_in.value = 0x06  # RDY=1, NMIB=1 (inactive)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1


def _load_program(dut, program):
    """Load a program dict {addr: byte} into RAM."""
    for addr, val in program.items():
        dut.ram[addr].value = val


def _read_ram(dut, addr):
    return int(dut.ram[addr].value)


def _place(prog, addr, bytepair):
    """Place a 2-byte instruction at addr."""
    prog[addr] = bytepair[0]
    prog[addr + 1] = bytepair[1]


def _set_ui(dut, rdy=True, irqb=True, nmib=True):
    """Set ui_in control signals. IRQB/NMIB are active-low."""
    val = (int(rdy) << 2) | (int(nmib) << 1) | int(irqb)
    dut.ui_in.value = val


# ===========================================================================
# Encoding helpers — variable-width prefix-free encoding
# ===========================================================================

# R,8 format: [prefix:5 @ 15:11][imm8:8 @ 10:3][reg:3 @ 2:0]
def _encode_r8(prefix, imm8, reg):
    insn = (prefix << 11) | ((imm8 & 0xFF) << 3) | (reg & 0x7)
    return (insn & 0xFF, (insn >> 8) & 0xFF)

def _encode_addi(rd, imm):
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b00000, imm, rd)

def _encode_li(rd, imm):
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b00001, imm, rd)

def _encode_lw(rd, imm):
    """LW: rd = mem16[R0 + sext(imm)]. Base is R0."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b00010, imm, rd)

def _encode_lb(rd, imm):
    """LB: rd = sext(mem[R0 + sext(imm)]). Base is R0."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b00011, imm, rd)

def _encode_lbu(rd, imm):
    """LBU: rd = zext(mem[R0 + sext(imm)]). Base is R0."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b00100, imm, rd)

def _encode_sw(rs, imm):
    """SW: mem16[R0 + sext(imm)] = rs. Base is R0."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b00101, imm, rs)

def _encode_sb(rs, imm):
    """SB: mem[R0 + sext(imm)] = rs[7:0]. Base is R0."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b00110, imm, rs)

def _encode_jr(rs, imm):
    """JR: pc = rs + sext(imm). Byte offset, no shift."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b00111, imm, rs)

def _encode_jalr(rs, imm):
    """JALR: rs=pc+2; pc = rs + sext(imm). Byte offset, no shift."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b01000, imm, rs)

def _encode_andi(rd, imm):
    assert 0 <= imm <= 255, f"imm out of range: {imm}"
    return _encode_r8(0b01001, imm, rd)

def _encode_ori(rd, imm):
    assert 0 <= imm <= 255, f"imm out of range: {imm}"
    return _encode_r8(0b01010, imm, rd)

def _encode_xori(rd, imm):
    assert 0 <= imm <= 255, f"imm out of range: {imm}"
    return _encode_r8(0b01011, imm, rd)

def _encode_slti(rs, imm):
    """SLTI: R0 = (rs < sext(imm)) ? 1 : 0. Dest is R0."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b01100, imm, rs)

def _encode_sltui(rs, imm):
    """SLTUI: R0 = (rs <u sext(imm)) ? 1 : 0. Dest is R0."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b01101, imm, rs)

def _encode_bz(rs, imm):
    """BZ: if rs == 0, pc += sext(imm) << 1.

    RISC-V trick encoding: imm is the half-word offset. Bits are scrambled
    so that ir[9:4] matches the non-shifted immediate format, reducing the
    ALU input mux from 8 bits to 2.
    """
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    off = imm & 0xFF
    scrambled = ((off & 0x80) |          # off[7] → bit 7 (sign)
                 ((off & 0x3F) << 1) |   # off[5:0] → bits [6:1]
                 ((off >> 6) & 1))       # off[6] → bit 0
    return _encode_r8(0b01110, scrambled, rs)

def _encode_bnz(rs, imm):
    """BNZ: if rs != 0, pc += sext(imm) << 1.

    Same RISC-V trick encoding as BZ.
    """
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    off = imm & 0xFF
    scrambled = ((off & 0x80) |          # off[7] → bit 7 (sign)
                 ((off & 0x3F) << 1) |   # off[5:0] → bits [6:1]
                 ((off >> 6) & 1))       # off[6] → bit 0
    return _encode_r8(0b01111, scrambled, rs)

def _encode_xorif(rs, imm):
    """XORIF: R0 = rs ^ zext(imm). Dest is R0."""
    assert 0 <= imm <= 255, f"imm out of range: {imm}"
    return _encode_r8(0b10000, imm, rs)

def _encode_andif(rs, imm):
    """ANDIF: R1 = rs & zext(imm). Dest is R1."""
    assert 0 <= imm <= 255, f"imm out of range: {imm}"
    return _encode_r8(0b10110, imm, rs)

def _encode_lw_s(rd, imm):
    """LWS: rd = mem16[R7 + sext(imm)]. Base is R7 (SP)."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b10001, imm, rd)

def _encode_lb_s(rd, imm):
    """LBS: rd = sext(mem[R7 + sext(imm)]). Base is R7 (SP)."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b10010, imm, rd)

def _encode_lbu_s(rd, imm):
    """LBUS: rd = zext(mem[R7 + sext(imm)]). Base is R7 (SP)."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b10011, imm, rd)

def _encode_sw_s(rd, imm):
    """SWS: mem16[R7 + sext(imm)] = rd. Base is R7 (SP)."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b10100, imm, rd)

def _encode_sb_s(rd, imm):
    """SBS: mem[R7 + sext(imm)] = rd[7:0]. Base is R7 (SP)."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b10101, imm, rd)

# R,7 format: [prefix:6 @ 15:10][imm7:7 @ 9:3][reg:3 @ 2:0]
def _encode_r7(prefix, imm7, reg):
    insn = (prefix << 10) | ((imm7 & 0x7F) << 3) | (reg & 0x7)
    return (insn & 0xFF, (insn >> 8) & 0xFF)

def _encode_lui(rd, imm7):
    """LUI: rd = sext(imm7) << 9."""
    assert -64 <= imm7 <= 63, f"imm7 out of range: {imm7}"
    return _encode_r7(0b110100, imm7, rd)

def _encode_auipc(rd, imm7):
    """AUIPC: rd = pc + (sext(imm7) << 9)."""
    assert -64 <= imm7 <= 63, f"imm7 out of range: {imm7}"
    return _encode_r7(0b110101, imm7, rd)

# "10" format: [prefix:6 @ 15:10][imm10:10 @ 9:0]
def _encode_10(prefix, imm10):
    insn = (prefix << 10) | (imm10 & 0x3FF)
    return (insn & 0xFF, (insn >> 8) & 0xFF)

def _encode_j(off10):
    """J: pc += sext(off10) << 1."""
    assert -512 <= off10 <= 511, f"off10 out of range: {off10}"
    return _encode_10(0b110110, off10)

def _encode_jal(off10):
    """JAL: R6 = pc+2; pc += sext(off10) << 1. Links to R6."""
    assert -512 <= off10 <= 511, f"off10 out of range: {off10}"
    return _encode_10(0b110111, off10)

# R,R,R format: [prefix:7 @ 15:9][rd:3 @ 8:6][rs2:3 @ 5:3][rs1:3 @ 2:0]
def _encode_rrr(prefix, rd, rs2, rs1):
    insn = (prefix << 9) | ((rd & 0x7) << 6) | ((rs2 & 0x7) << 3) | (rs1 & 0x7)
    return (insn & 0xFF, (insn >> 8) & 0xFF)

def _encode_add(rd, rs1, rs2):  return _encode_rrr(0b1110000, rd, rs2, rs1)
def _encode_sub(rd, rs1, rs2):  return _encode_rrr(0b1110001, rd, rs2, rs1)
def _encode_and_rr(rd, rs1, rs2): return _encode_rrr(0b1110010, rd, rs2, rs1)
def _encode_or_rr(rd, rs1, rs2):  return _encode_rrr(0b1110011, rd, rs2, rs1)
def _encode_xor_rr(rd, rs1, rs2): return _encode_rrr(0b1110100, rd, rs2, rs1)
def _encode_slt(rd, rs1, rs2):  return _encode_rrr(0b1110101, rd, rs2, rs1)
def _encode_sltu(rd, rs1, rs2): return _encode_rrr(0b1110110, rd, rs2, rs1)
def _encode_sll(rd, rs1, rs2):  return _encode_rrr(0b1110111, rd, rs2, rs1)
def _encode_srl(rd, rs1, rs2):  return _encode_rrr(0b1111000, rd, rs2, rs1)
def _encode_sra(rd, rs1, rs2):  return _encode_rrr(0b1111001, rd, rs2, rs1)

# R,4 format: [prefix:9 @ 15:7][shamt:4 @ 6:3][reg:3 @ 2:0]
def _encode_r4(prefix, shamt, reg):
    insn = (prefix << 7) | ((shamt & 0xF) << 3) | (reg & 0x7)
    return (insn & 0xFF, (insn >> 8) & 0xFF)

def _encode_slli(rd, shamt): return _encode_r4(0b111101000, shamt, rd)
def _encode_srli(rd, shamt): return _encode_r4(0b111101001, shamt, rd)
def _encode_srai(rd, shamt): return _encode_r4(0b111101010, shamt, rd)

# R,R format: [prefix:10 @ 15:6][rd:3 @ 5:3][rs:3 @ 2:0]
def _encode_rr(prefix, rd, rs):
    insn = (prefix << 6) | ((rd & 0x7) << 3) | (rs & 0x7)
    return (insn & 0xFF, (insn >> 8) & 0xFF)

def _encode_lw_rr(rd, rs):  return _encode_rr(0b1111010110, rd, rs)
def _encode_lb_rr(rd, rs):  return _encode_rr(0b1111010111, rd, rs)
def _encode_lbu_rr(rd, rs): return _encode_rr(0b1111011000, rd, rs)
def _encode_sw_rr(rd, rs):  return _encode_rr(0b1111011001, rd, rs)
def _encode_sb_rr(rd, rs):  return _encode_rr(0b1111011010, rd, rs)

# System format: [1111100000:10 @ 15:6][sub:6 @ 5:0]
def _encode_sys(sub):
    insn = (0b1111100000 << 6) | (sub & 0x3F)
    return (insn & 0xFF, (insn >> 8) & 0xFF)

def _encode_sei():  return _encode_sys(0b000001)
def _encode_cli():  return _encode_sys(0b000010)
def _encode_reti(): return _encode_sys(0b000011)

def _encode_epcr(rd):
    """EPCR Rd: copy EPC to Rd."""
    return _encode_sys(0b010_000 | (rd & 0x7))

def _encode_epcw(rs):
    """EPCW Rs: copy Rs to EPC."""
    return _encode_sys(0b011_000 | (rs & 0x7))

def _encode_brk():  return _encode_sys(0b100001)  # INT, vector 1 → addr 0x0004
def _encode_wai():  return _encode_sys(0b000101)
def _encode_stp():  return _encode_sys(0b000111)

def _encode_nop():
    """NOP = ADDI R0, 0 = 0x0000."""
    return (0x00, 0x00)

def _spin(addr=None):
    """Self-loop: J -1 (pc-relative, works at any address)."""
    return _encode_j(off10=-1)


# ===========================================================================
# Cycle measurement helper
# ===========================================================================
async def _measure_instruction_cycles(dut, prog, expected_cycles, test_name):
    """Measure cycles for the first instruction in prog."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    _load_program(dut, prog)
    await _reset(dut)

    def get_sync():
        return (int(dut.uo_out.value) >> 1) & 1

    # Wait for first SYNC
    for _ in range(200):
        await FallingEdge(dut.clk)
        if get_sync():
            break

    # Count cycles until next SYNC
    cycles = 0
    # Wait for SYNC to drop
    for _ in range(200):
        await FallingEdge(dut.clk)
        cycles += 1
        if not get_sync():
            break

    # Wait for SYNC to rise
    for _ in range(200):
        await FallingEdge(dut.clk)
        cycles += 1
        if get_sync():
            break

    dut._log.info(f"{test_name}: {cycles} cycles (expected {expected_cycles})")
    assert cycles == expected_cycles, f"{test_name}: expected {expected_cycles} cycles, got {cycles}"
