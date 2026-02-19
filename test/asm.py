# SPDX-FileCopyrightText: © 2024 mysterymath
# SPDX-License-Identifier: Apache-2.0
#
# Assembler library for RISCY-V02.
#
# Contains all instruction encoding functions (canonical source) and the Asm
# class for building programs with auto-advancing PC and label support.

__all__ = ['Asm']


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
    """CMPI: T = (rs < sext(imm)). Signed comparison, sets T flag."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b01100, imm, rs)

_encode_cmpi = _encode_slti

def _encode_sltui(rs, imm):
    """CMPUI: T = (rs <u sext(imm)). Unsigned comparison, sets T flag."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_r8(0b01101, imm, rs)

_encode_cmpui = _encode_sltui

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
    """XORIF: T = (rs ^ zext(imm)) != 0. Sets T flag."""
    assert 0 <= imm <= 255, f"imm out of range: {imm}"
    return _encode_r8(0b10000, imm, rs)

def _encode_8(prefix8, off8):
    """Encode an 8-bit prefix instruction: [prefix:8|off8:8]."""
    insn = (prefix8 << 8) | (off8 & 0xFF)
    return (insn & 0xFF, (insn >> 8) & 0xFF)

def _encode_bt(imm):
    """BT: if T, pc += sext(off8) << 1."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_8(0b10110_000, imm)

def _encode_bf(imm):
    """BF: if !T, pc += sext(off8) << 1."""
    assert -128 <= imm <= 127, f"imm out of range: {imm}"
    return _encode_8(0b10110_001, imm)

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

def _encode_movt(rd):
    """MOVT Rd: rd = T (0 or 1)."""
    return _encode_sys(0b100_000 | (rd & 0x7))

def _encode_srr(rd):
    """SRR Rd: rd = SR ({I, T})."""
    return _encode_sys(0b101_000 | (rd & 0x7))

def _encode_srw(rs):
    """SRW Rs: SR = rs ({I, T})."""
    return _encode_sys(0b001_000 | (rs & 0x7))

def _encode_brk():  return _encode_sys(0b11_0001)  # INT, sub[5:4]=11, vector 1 → addr 0x0004
def _encode_wai():  return _encode_sys(0b000101)
def _encode_stp():  return _encode_sys(0b000111)

def _encode_nop():
    """NOP = ADDI R0, 0 = 0x0000."""
    return (0x00, 0x00)

def _spin(addr=None):
    """Self-loop: J -1 (pc-relative, works at any address)."""
    return _encode_j(off10=-1)


# ===========================================================================
# Assembler class
# ===========================================================================

class Asm:
    def __init__(self, org=0):
        self.pc = org
        self.prog = {}
        self.labels = {}
        self.fixups = []

    def _emit(self, bytepair):
        self.prog[self.pc] = bytepair[0]
        self.prog[self.pc + 1] = bytepair[1]
        self.pc += 2

    def label(self, name):
        assert name not in self.labels, f"duplicate label: {name}"
        self.labels[name] = self.pc

    def org(self, addr):
        self.pc = addr

    def db(self, *bytes):
        for b in bytes:
            self.prog[self.pc] = b & 0xFF
            self.pc += 1

    def dw(self, word):
        self.prog[self.pc] = word & 0xFF
        self.prog[self.pc + 1] = (word >> 8) & 0xFF
        self.pc += 2

    # R,8 format
    def li(self, rd, imm):      self._emit(_encode_li(rd, imm))
    def addi(self, rd, imm):    self._emit(_encode_addi(rd, imm))
    def lw(self, rd, imm):      self._emit(_encode_lw(rd, imm))
    def lb(self, rd, imm):      self._emit(_encode_lb(rd, imm))
    def lbu(self, rd, imm):     self._emit(_encode_lbu(rd, imm))
    def sw(self, rs, imm):      self._emit(_encode_sw(rs, imm))
    def sb(self, rs, imm):      self._emit(_encode_sb(rs, imm))
    def jr(self, rs, imm):      self._emit(_encode_jr(rs, imm))
    def jalr(self, rs, imm):    self._emit(_encode_jalr(rs, imm))
    def andi(self, rd, imm):    self._emit(_encode_andi(rd, imm))
    def ori(self, rd, imm):     self._emit(_encode_ori(rd, imm))
    def xori(self, rd, imm):    self._emit(_encode_xori(rd, imm))
    def slti(self, rs, imm):    self._emit(_encode_slti(rs, imm))
    def sltui(self, rs, imm):   self._emit(_encode_sltui(rs, imm))
    def xorif(self, rs, imm):   self._emit(_encode_xorif(rs, imm))
    # SP-relative
    def lw_s(self, rd, imm):    self._emit(_encode_lw_s(rd, imm))
    def lb_s(self, rd, imm):    self._emit(_encode_lb_s(rd, imm))
    def lbu_s(self, rd, imm):   self._emit(_encode_lbu_s(rd, imm))
    def sw_s(self, rs, imm):    self._emit(_encode_sw_s(rs, imm))
    def sb_s(self, rs, imm):    self._emit(_encode_sb_s(rs, imm))
    # R,7 format
    def lui(self, rd, imm7):    self._emit(_encode_lui(rd, imm7))
    def auipc(self, rd, imm7):  self._emit(_encode_auipc(rd, imm7))
    # R,R,R format
    def add(self, rd, rs1, rs2):   self._emit(_encode_add(rd, rs1, rs2))
    def sub(self, rd, rs1, rs2):   self._emit(_encode_sub(rd, rs1, rs2))
    def and_(self, rd, rs1, rs2):  self._emit(_encode_and_rr(rd, rs1, rs2))
    def or_(self, rd, rs1, rs2):   self._emit(_encode_or_rr(rd, rs1, rs2))
    def xor(self, rd, rs1, rs2):   self._emit(_encode_xor_rr(rd, rs1, rs2))
    def slt(self, rd, rs1, rs2):   self._emit(_encode_slt(rd, rs1, rs2))
    def sltu(self, rd, rs1, rs2):  self._emit(_encode_sltu(rd, rs1, rs2))
    def sll(self, rd, rs1, rs2):   self._emit(_encode_sll(rd, rs1, rs2))
    def srl(self, rd, rs1, rs2):   self._emit(_encode_srl(rd, rs1, rs2))
    def sra(self, rd, rs1, rs2):   self._emit(_encode_sra(rd, rs1, rs2))
    # R,4 format
    def slli(self, rd, shamt):  self._emit(_encode_slli(rd, shamt))
    def srli(self, rd, shamt):  self._emit(_encode_srli(rd, shamt))
    def srai(self, rd, shamt):  self._emit(_encode_srai(rd, shamt))
    # R,R format
    def lw_rr(self, rd, rs):    self._emit(_encode_lw_rr(rd, rs))
    def lb_rr(self, rd, rs):    self._emit(_encode_lb_rr(rd, rs))
    def lbu_rr(self, rd, rs):   self._emit(_encode_lbu_rr(rd, rs))
    def sw_rr(self, rd, rs):    self._emit(_encode_sw_rr(rd, rs))
    def sb_rr(self, rd, rs):    self._emit(_encode_sb_rr(rd, rs))
    # System
    def sei(self):              self._emit(_encode_sei())
    def cli(self):              self._emit(_encode_cli())
    def reti(self):             self._emit(_encode_reti())
    def epcr(self, rd):         self._emit(_encode_epcr(rd))
    def epcw(self, rs):         self._emit(_encode_epcw(rs))
    def movt(self, rd):         self._emit(_encode_movt(rd))
    def srr(self, rd):          self._emit(_encode_srr(rd))
    def srw(self, rs):          self._emit(_encode_srw(rs))
    def brk(self):              self._emit(_encode_brk())
    def wai(self):              self._emit(_encode_wai())
    def stp(self):              self._emit(_encode_stp())
    def nop(self):              self._emit(_encode_nop())

    # Branch/jump instructions — accept label string or integer offset
    def bz(self, rs, target):
        if isinstance(target, str):
            self.fixups.append(('bz', self.pc, rs, target))
            self._emit((0, 0))
        else:
            self._emit(_encode_bz(rs, target))

    def bnz(self, rs, target):
        if isinstance(target, str):
            self.fixups.append(('bnz', self.pc, rs, target))
            self._emit((0, 0))
        else:
            self._emit(_encode_bnz(rs, target))

    def bt(self, target):
        if isinstance(target, str):
            self.fixups.append(('bt', self.pc, target))
            self._emit((0, 0))
        else:
            self._emit(_encode_bt(target))

    def bf(self, target):
        if isinstance(target, str):
            self.fixups.append(('bf', self.pc, target))
            self._emit((0, 0))
        else:
            self._emit(_encode_bf(target))

    def j(self, target):
        if isinstance(target, str):
            self.fixups.append(('j', self.pc, target))
            self._emit((0, 0))
        else:
            self._emit(_encode_j(target))

    def jal(self, target):
        if isinstance(target, str):
            self.fixups.append(('jal', self.pc, target))
            self._emit((0, 0))
        else:
            self._emit(_encode_jal(target))

    # Pseudo-instructions
    def read_t(self, rd):
        """Read T flag into rd: SRR rd; ANDI rd, 1."""
        self._emit(_encode_srr(rd))
        self._emit(_encode_andi(rd, 1))

    def spin(self):
        """Self-loop: J -1."""
        self._emit(_spin())

    # Assemble — resolve label fixups and return {addr: byte}
    def assemble(self):
        for fixup in self.fixups:
            kind, addr = fixup[0], fixup[1]
            if kind in ('bz', 'bnz'):
                _, addr, rs, label = fixup
                assert label in self.labels, f"undefined label: {label}"
                off = (self.labels[label] - addr) // 2 - 1
                bytepair = (_encode_bz if kind == 'bz' else _encode_bnz)(rs, off)
            elif kind in ('bt', 'bf'):
                _, addr, label = fixup
                assert label in self.labels, f"undefined label: {label}"
                off = (self.labels[label] - addr) // 2 - 1
                bytepair = (_encode_bt if kind == 'bt' else _encode_bf)(off)
            elif kind in ('j', 'jal'):
                _, addr, label = fixup
                assert label in self.labels, f"undefined label: {label}"
                off = (self.labels[label] - addr) // 2 - 1
                bytepair = (_encode_j if kind == 'j' else _encode_jal)(off)
            self.prog[addr] = bytepair[0]
            self.prog[addr + 1] = bytepair[1]
        return self.prog
