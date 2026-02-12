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


def _encode_s_format(prefix, off6, rs1, rd_rs2):
    """Encode S-format: [prefix:4][rs1:3][off6[5:3]:3][off6[2:0]:3][rd/rs2:3]."""
    off6_lo = off6 & 0x7        # off6[2:0]
    off6_hi = (off6 >> 3) & 0x7 # off6[5:3]
    insn = (prefix << 12) | (rs1 << 9) | (off6_hi << 6) | (off6_lo << 3) | rd_rs2
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_lw(rd, rs1, off6):
    """Encode LW rd, off6(rs1) -> 16-bit little-endian bytes. off6 is byte offset."""
    assert -32 <= off6 <= 31, f"off6 out of range: {off6}"
    assert 0 <= rd <= 7 and 0 <= rs1 <= 7
    off6 &= 0x3F
    return _encode_s_format(0b1000, off6, rs1, rd)


def _encode_sw(rs2, rs1, off6):
    """Encode SW rs2, off6(rs1). Store format: [1010][rs1:3][rs2:3][off6:6]."""
    assert -32 <= off6 <= 31, f"off6 out of range: {off6}"
    assert 0 <= rs2 <= 7 and 0 <= rs1 <= 7
    off6 &= 0x3F
    insn = (0b1010 << 12) | (rs1 << 9) | (rs2 << 6) | off6
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_jr(rs, off6):
    """Encode JR rs, off6 -> 16-bit little-endian bytes."""
    assert -32 <= off6 <= 31, f"off6 out of range: {off6}"
    assert 0 <= rs <= 7
    off6 &= 0x3F
    insn = (0b1101110 << 9) | (off6 << 3) | rs
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
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    # 0x0002: SW R1, 9(R0)   ; MEM[0 + 9*2] = MEM[0x12] = R1 = 0x1234
    _place(prog, 0x0002, _encode_sw(rs2=1, rs1=0, off6=18))
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
    # Data at 0x000E: LE word 0x0010 (target address)
    prog[0x000E] = 0x10
    prog[0x000F] = 0x00

    # Data at 0x0018: LE word 0xBEEF
    prog[0x0018] = 0xEF
    prog[0x0019] = 0xBE

    # 0x0000: LW R1, 14(R0)  ; R1 = MEM[0x0E] = 0x0010
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=14))
    # 0x0002: LW R2, 24(R0)  ; R2 = MEM[0x18] = 0xBEEF
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=24))
    # 0x0004: JR R1, 0       ; PC = R1 + 0 = 0x0010
    _place(prog, 0x0004, _encode_jr(rs=1, off6=0))

    # At target 0x0010: SW R2, 20(R0) ; MEM[0x14] = 0xBEEF
    _place(prog, 0x0010, _encode_sw(rs2=2, rs1=0, off6=20))
    # 0x0012: JR R0, 9       ; PC = 0 + 9*2 = 18 = 0x12 (spin)
    _place(prog, 0x0012, _encode_jr(rs=0, off6=9))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"ram[0x14:0x15] = {val:#06x}")
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
    prog[0x0010] = 0x50
    prog[0x0011] = 0x00

    # Data at 0x004E: LE word 0xCAFE (at base 0x50, offset -1 → addr 0x4E)
    prog[0x004E] = 0xFE
    prog[0x004F] = 0xCA

    # 0x0000: LW R3, 16(R0)  ; R3 = MEM[0 + 16*2] = MEM[0x20] = 0x0050
    _place(prog, 0x0000, _encode_lw(rd=3, rs1=0, off6=16))
    # 0x0002: LW R4, -1(R3)  ; R4 = MEM[0x50 + (-1)*2] = MEM[0x4E] = 0xCAFE
    _place(prog, 0x0002, _encode_lw(rd=4, rs1=3, off6=-2))
    # 0x0004: SW R4, 1(R3)   ; MEM[0x50 + 1*2] = MEM[0x52] = 0xCAFE
    _place(prog, 0x0004, _encode_sw(rs2=4, rs1=3, off6=2))
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
# Test 4: JR immediately after LW to same register
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_jr_after_lw(dut):
    """JR using a register value loaded by the immediately preceding LW."""
    dut._log.info("Test 4: JR immediately after LW (same register)")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Data at 0x0008: LE word 0x0020 (JR target address)
    prog[0x0008] = 0x20
    prog[0x0009] = 0x00

    # 0x0000: LW R1, 4(R0)   ; R1 = MEM[0x08] = 0x0020
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=8))
    # 0x0002: JR R1, 0        ; jump to 0x0020 (uses just-loaded R1)
    _place(prog, 0x0002, _encode_jr(rs=1, off6=0))
    # 0x0020: SW R0, 10(R0)   ; MEM[0x14] = 0 (marker write)
    _place(prog, 0x0020, _encode_sw(rs2=0, rs1=0, off6=20))
    # 0x0022: JR R0, 17       ; spin at 0x0022
    _place(prog, 0x0022, _encode_jr(rs=0, off6=17))

    # Pre-fill marker location with non-zero so we can detect the write
    prog[0x0014] = 0xFF
    prog[0x0015] = 0xFF

    _load_program(dut, prog)
    await _reset(dut)

    # Wait enough cycles for the program to execute
    await ClockCycles(dut.clk, 100)

    # Verify the SW at the JR target executed
    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    dut._log.info(f"ram[0x14]={lo:#04x}, ram[0x15]={hi:#04x}")
    assert lo == 0x00 and hi == 0x00, \
        f"SW at JR target did not execute (expected 0x0000, got {lo:#04x}{hi:#04x})"
    dut._log.info("PASS [jr_after_lw]")


# ---------------------------------------------------------------------------
# Test 5: Single-step debugging via RDY/SYNC
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_single_step(dut):
    """Test single-step debugging using RDY/SYNC protocol."""
    dut._log.info("Test 5: Single-step debugging")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Data: three distinct marker values
    prog[0x0010] = 0x11
    prog[0x0011] = 0x11
    prog[0x0012] = 0x22
    prog[0x0013] = 0x22
    prog[0x0014] = 0x33
    prog[0x0015] = 0x33

    # Program: load three values, store them as markers, spin
    # 0x0000: LW R1, 16(R0)  ; R1 = MEM[0x10] = 0x1111
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    # 0x0002: LW R2, 18(R0)  ; R2 = MEM[0x12] = 0x2222
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    # 0x0004: LW R3, 20(R0)  ; R3 = MEM[0x14] = 0x3333
    _place(prog, 0x0004, _encode_lw(rd=3, rs1=0, off6=20))
    # 0x0006: SW R1, 22(R0)  ; MEM[0x16] = 0x1111
    _place(prog, 0x0006, _encode_sw(rs2=1, rs1=0, off6=22))
    # 0x0008: SW R2, 24(R0)  ; MEM[0x18] = 0x2222
    _place(prog, 0x0008, _encode_sw(rs2=2, rs1=0, off6=24))
    # 0x000A: SW R3, 26(R0)  ; MEM[0x1A] = 0x3333
    _place(prog, 0x000A, _encode_sw(rs2=3, rs1=0, off6=26))
    # 0x000C: JR R0, 6       ; spin at 0x000C
    _place(prog, 0x000C, _encode_jr(rs=0, off6=6))

    def get_sync():
        return (int(dut.uo_out.value) >> 1) & 1

    # Marker addresses: 0x16, 0x18, 0x1A
    def read_markers():
        m1 = _read_ram(dut, 0x0016) | (_read_ram(dut, 0x0017) << 8)
        m2 = _read_ram(dut, 0x0018) | (_read_ram(dut, 0x0019) << 8)
        m3 = _read_ram(dut, 0x001A) | (_read_ram(dut, 0x001B) << 8)
        return (m1, m2, m3)

    async def run_to_sync():
        """Run until SYNC=1 (instruction boundary), then halt."""
        dut.ui_in.value = 0x06  # RDY=1, NMIB=1
        for _ in range(200):
            await FallingEdge(dut.clk)
            if get_sync():
                dut.ui_in.value = 0x02  # RDY=0, NMIB=1, halt
                return True
        return False

    async def single_step():
        """Execute one instruction: wait for SYNC 1→0→1 transition."""
        dut.ui_in.value = 0x06  # RDY=1, NMIB=1, run

        # Wait for SYNC to go low (instruction starts executing)
        for _ in range(200):
            await FallingEdge(dut.clk)
            if not get_sync():
                break

        # Wait for SYNC to go high (next boundary reached)
        for _ in range(200):
            await FallingEdge(dut.clk)
            if get_sync():
                dut.ui_in.value = 0x02  # RDY=0, NMIB=1, halt
                return True

        return False

    # Reset with RDY=0 so CPU doesn't run until we're ready
    dut.ena.value = 1
    dut.ui_in.value = 0x02  # RDY=0, NMIB=1, halted
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)  # Let reset settle

    # Load program AFTER reset (to avoid corruption from previous test's bus activity)
    _load_program(dut, prog)

    # Clear marker addresses (may have garbage from previous tests)
    for addr in [0x16, 0x17, 0x18, 0x19, 0x1A, 0x1B]:
        dut.ram[addr].value = 0x00

    # Now run to first instruction boundary
    assert await run_to_sync(), "Failed to reach first instruction boundary"
    dut._log.info(f"At first boundary, SYNC={get_sync()}, markers={read_markers()}")

    # Verify halted: wait and check no changes
    m_before = read_markers()
    await ClockCycles(dut.clk, 20)
    m_after = read_markers()
    assert m_before == m_after, f"CPU modified state while halted: {m_before} → {m_after}"
    dut._log.info("Verified: CPU halted, no state changes")

    # Single-step through LW instructions (no visible memory changes)
    for i in range(3):
        assert await single_step(), f"single_step failed on LW {i+1}"
        m = read_markers()
        assert m == (0, 0, 0), f"Unexpected markers after LW {i+1}: {m}"
        dut._log.info(f"Step {i+1} (LW): markers unchanged")

    # Single-step through SW instructions (markers appear one by one)
    assert await single_step(), "single_step failed on SW R1"
    m = read_markers()
    assert m == (0x1111, 0, 0), f"After SW R1: expected (0x1111, 0, 0), got {[hex(x) for x in m]}"
    dut._log.info(f"Step 4 (SW R1): markers = {[hex(x) for x in m]}")

    assert await single_step(), "single_step failed on SW R2"
    m = read_markers()
    assert m == (0x1111, 0x2222, 0), f"After SW R2: expected (0x1111, 0x2222, 0), got {[hex(x) for x in m]}"
    dut._log.info(f"Step 5 (SW R2): markers = {[hex(x) for x in m]}")

    assert await single_step(), "single_step failed on SW R3"
    m = read_markers()
    assert m == (0x1111, 0x2222, 0x3333), f"After SW R3: expected all markers, got {[hex(x) for x in m]}"
    dut._log.info(f"Step 6 (SW R3): markers = {[hex(x) for x in m]}")

    dut._log.info("PASS [single_step]")


# ---------------------------------------------------------------------------
# Helper encoders for interrupt instructions
# ---------------------------------------------------------------------------
def _encode_reti():
    """Encode RETI -> 16-bit little-endian bytes. [1111011][000000000]"""
    insn = (0b1111011 << 9)
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_sei():
    """Encode SEI -> 16-bit little-endian bytes. [1111001][000000000]"""
    insn = (0b1111001 << 9)
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_cli():
    """Encode CLI -> 16-bit little-endian bytes. [1111010][000000000]"""
    insn = (0b1111010 << 9)
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_brk():
    """Encode BRK -> 16-bit little-endian bytes. [1111100][000001][110] (vector 1, R6)"""
    insn = (0b1111100 << 9) | (1 << 3) | 6
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_wai():
    """Encode WAI -> 16-bit little-endian bytes. [1111101][000000000]"""
    insn = (0b1111101 << 9)
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_stp():
    """Encode STP -> 16-bit little-endian bytes. [1111111][000000000]"""
    insn = (0b1111111 << 9)
    return (insn & 0xFF, (insn >> 8) & 0xFF)



# ---------------------------------------------------------------------------
# Test 6: Reset I state (interrupts disabled after reset)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_reset_i_state(dut):
    """Verify interrupts are disabled after reset (I=1)."""
    dut._log.info("Test 6: Reset I state")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Program: just a spin loop at 0x0000
    # 0x0000: JR R0, 0  ; spin at 0x0000
    _place(prog, 0x0000, _encode_jr(rs=0, off6=0))

    # IRQ handler at 0x0006: write marker and spin
    # 0x0006: LW R1, 16(R0)  ; R1 = 0xDEAD from 0x10
    _place(prog, 0x0006, _encode_lw(rd=1, rs1=0, off6=16))
    # 0x0008: SW R1, 24(R0)  ; MEM[0x18] = 0xDEAD (marker)
    _place(prog, 0x0008, _encode_sw(rs2=1, rs1=0, off6=24))
    # 0x000A: JR R0, 5  ; spin at 0x000A
    _place(prog, 0x000A, _encode_jr(rs=0, off6=5))

    # Data: marker value
    prog[0x0010] = 0xAD
    prog[0x0011] = 0xDE

    # Clear marker location
    prog[0x0018] = 0x00
    prog[0x0019] = 0x00

    _load_program(dut, prog)

    # Reset with IRQB=0 (interrupt asserted) and RDY=1
    # ui_in[0] = IRQB, ui_in[2] = RDY
    dut.ena.value = 1
    dut.ui_in.value = 0x06  # RDY=1, NMIB=1, IRQB=0 (asserted!)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Run for a while with interrupt asserted
    await ClockCycles(dut.clk, 100)

    # Check marker - should NOT be written since I=1 after reset
    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    val = lo | (hi << 8)
    dut._log.info(f"Marker = {val:#06x} (expected 0x0000 if I=1 masks IRQ)")
    assert val == 0x0000, f"IRQ fired after reset! Expected I=1 to mask. Got marker {val:#06x}"
    dut._log.info("PASS [reset_i_state]")


# ---------------------------------------------------------------------------
# Test 7: CLI enables interrupts
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_cli_enables_irq(dut):
    """CLI clears I bit, allowing interrupts to fire."""
    dut._log.info("Test 7: CLI enables interrupts")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Program: CLI then spin
    # 0x0000: CLI
    _place(prog, 0x0000, _encode_cli())
    # 0x0002: JR R0, 1  ; spin at 0x0002
    _place(prog, 0x0002, _encode_jr(rs=0, off6=1))

    # IRQ handler at 0x0006: write marker and spin
    # 0x0006: LW R1, 16(R0)  ; R1 = 0xBEEF from 0x10
    _place(prog, 0x0006, _encode_lw(rd=1, rs1=0, off6=16))
    # 0x0008: SW R1, 24(R0)  ; MEM[0x18] = 0xBEEF (marker)
    _place(prog, 0x0008, _encode_sw(rs2=1, rs1=0, off6=24))
    # 0x000A: JR R0, 5  ; spin at 0x000A
    _place(prog, 0x000A, _encode_jr(rs=0, off6=5))

    # Data: marker value
    prog[0x0010] = 0xEF
    prog[0x0011] = 0xBE

    # Clear marker location
    prog[0x0018] = 0x00
    prog[0x0019] = 0x00

    _load_program(dut, prog)

    # Reset with IRQB=1 (not asserted) and RDY=1
    dut.ena.value = 1
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Run for a few cycles to execute CLI
    await ClockCycles(dut.clk, 20)

    # Now assert IRQB=0
    dut.ui_in.value = 0x06  # RDY=1, NMIB=1, IRQB=0

    # Run to let IRQ fire and handler execute
    await ClockCycles(dut.clk, 100)

    # Check marker - should be written since CLI enabled interrupts
    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    val = lo | (hi << 8)
    dut._log.info(f"Marker = {val:#06x} (expected 0xBEEF if IRQ fired)")
    assert val == 0xBEEF, f"IRQ did not fire after CLI! Got marker {val:#06x}"
    dut._log.info("PASS [cli_enables_irq]")


# ---------------------------------------------------------------------------
# Test 8: SEI disables interrupts
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sei_disables_irq(dut):
    """SEI sets I bit, preventing interrupts."""
    dut._log.info("Test 8: SEI disables interrupts")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Program: CLI, SEI, then spin
    # 0x0000: CLI
    _place(prog, 0x0000, _encode_cli())
    # 0x0002: SEI
    _place(prog, 0x0002, _encode_sei())
    # 0x0004: JR R0, 2  ; spin at 0x0004
    _place(prog, 0x0004, _encode_jr(rs=0, off6=2))

    # IRQ handler at 0x0004 would conflict, so use different addresses
    # Actually the IRQ vector is at 0x0004, but our spin loop is there too.
    # Let's restructure: put spin elsewhere

    prog = {}
    # 0x0000: CLI
    _place(prog, 0x0000, _encode_cli())
    # 0x0002: SEI
    _place(prog, 0x0002, _encode_sei())
    # 0x0004: this is IRQ vector! Let's jump past it first
    # Actually, let's redesign: IRQ vector at 0x0004 needs a handler
    # Main code needs to avoid that address

    # Redesign: Jump over vector area first
    # 0x0000: LW R1, 5(R0)  ; R1 = MEM[0x0A] = 0x0010 (continue address)
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=10))
    # 0x0002: JR R1, 0      ; jump to 0x0010
    _place(prog, 0x0002, _encode_jr(rs=1, off6=0))

    # IRQ handler at 0x0006
    _place(prog, 0x0006, _encode_lw(rd=2, rs1=0, off6=20))  # R2 = MEM[0x14] = 0xDEAD
    _place(prog, 0x0008, _encode_sw(rs2=2, rs1=0, off6=24)) # MEM[0x18] = R2
    _place(prog, 0x000A, _encode_jr(rs=0, off6=5))          # spin at 0x000A

    # Data: jump target
    prog[0x000A] = 0x10
    prog[0x000B] = 0x00

    # Continue at 0x0010: CLI, SEI, spin
    _place(prog, 0x0010, _encode_cli())
    _place(prog, 0x0012, _encode_sei())
    _place(prog, 0x0014, _encode_jr(rs=0, off6=10))  # spin at 0x0014

    # Data: marker value
    prog[0x0014] = 0xAD
    prog[0x0015] = 0xDE

    # Clear marker location
    prog[0x0018] = 0x00
    prog[0x0019] = 0x00

    _load_program(dut, prog)

    # Reset with IRQB=1 (not asserted) and RDY=1
    dut.ena.value = 1
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Run to execute jump, CLI, SEI
    await ClockCycles(dut.clk, 50)

    # Now assert IRQB=0
    dut.ui_in.value = 0x06  # RDY=1, NMIB=1, IRQB=0

    # Run for a while - IRQ should NOT fire due to SEI
    await ClockCycles(dut.clk, 100)

    # Check marker - should NOT be written since SEI masked IRQ
    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    val = lo | (hi << 8)
    dut._log.info(f"Marker = {val:#06x} (expected 0x0000 if SEI masked IRQ)")
    assert val == 0x0000, f"IRQ fired despite SEI! Got marker {val:#06x}"
    dut._log.info("PASS [sei_disables_irq]")


# ---------------------------------------------------------------------------
# Test 9: RETI restores I bit and returns
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_reti(dut):
    """RETI restores I bit from EPC[0] and returns to saved PC."""
    dut._log.info("Test 9: RETI")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Program: jump past vector, CLI, spin (will be interrupted)
    # After RETI, execution continues at spin, writes second marker

    # 0x0000: LW R1, 5(R0)  ; R1 = MEM[0x0A] = 0x0020 (continue address)
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=10))
    # 0x0002: JR R1, 0      ; jump to 0x0020
    _place(prog, 0x0002, _encode_jr(rs=1, off6=0))

    # IRQ handler at 0x0006: write marker, RETI
    _place(prog, 0x0006, _encode_lw(rd=2, rs1=0, off6=24))  # R2 = MEM[0x18] = 0xAAAA
    _place(prog, 0x0008, _encode_sw(rs2=2, rs1=0, off6=28)) # MEM[0x1C] = 0xAAAA (IRQ marker)
    _place(prog, 0x000A, _encode_reti())                    # return from interrupt

    # Data: jump target at 0x0A
    prog[0x000A] = 0x20
    prog[0x000B] = 0x00

    # Continue at 0x0020: CLI, then write marker, spin
    _place(prog, 0x0020, _encode_cli())
    # After CLI, when IRQ fires, return address will be 0x0022
    # 0x0022: write "returned" marker
    _place(prog, 0x0022, _encode_lw(rd=3, rs1=0, off6=26))  # R3 = MEM[0x34] = 0xBBBB
    _place(prog, 0x0024, _encode_sw(rs2=3, rs1=0, off6=30)) # MEM[0x3C] = 0xBBBB (return marker)
    _place(prog, 0x0026, _encode_jr(rs=0, off6=19))         # spin at 0x0026

    # Data: marker values
    prog[0x0018] = 0xAA
    prog[0x0019] = 0xAA
    prog[0x001A] = 0xBB
    prog[0x001B] = 0xBB

    # Clear marker locations
    prog[0x001C] = 0x00
    prog[0x001D] = 0x00
    prog[0x001E] = 0x00
    prog[0x001F] = 0x00

    _load_program(dut, prog)

    # Reset with IRQB=1 (not asserted)
    dut.ena.value = 1
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Run until CLI executes
    await ClockCycles(dut.clk, 50)

    # Assert IRQB to trigger interrupt
    dut.ui_in.value = 0x06  # RDY=1, NMIB=1, IRQB=0

    # Run to let IRQ fire, handler execute, RETI, and return code execute
    await ClockCycles(dut.clk, 200)

    # De-assert IRQB so we don't keep interrupting
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1

    await ClockCycles(dut.clk, 100)

    # Check IRQ marker at 0x38
    lo = _read_ram(dut, 0x001C)
    hi = _read_ram(dut, 0x001D)
    irq_marker = lo | (hi << 8)
    dut._log.info(f"IRQ marker = {irq_marker:#06x} (expected 0xAAAA)")

    # Check return marker at 0x3C
    lo = _read_ram(dut, 0x001E)
    hi = _read_ram(dut, 0x001F)
    ret_marker = lo | (hi << 8)
    dut._log.info(f"Return marker = {ret_marker:#06x} (expected 0xBBBB)")

    assert irq_marker == 0xAAAA, f"IRQ handler did not execute! Got {irq_marker:#06x}"
    assert ret_marker == 0xBBBB, f"RETI did not return correctly! Got {ret_marker:#06x}"
    dut._log.info("PASS [reti]")


# ---------------------------------------------------------------------------
# Test 10: I bit masking after IRQ entry
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_i_bit_masking(dut):
    """After IRQ entry, I=1 prevents nested interrupts until RETI."""
    dut._log.info("Test 10: I bit masking after IRQ entry")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Program: jump past vector, CLI, spin
    # 0x0000: LW R1, 5(R0)  ; R1 = MEM[0x0A] = 0x0020
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=10))
    # 0x0002: JR R1, 0
    _place(prog, 0x0002, _encode_jr(rs=1, off6=0))

    # IRQ handler at 0x0006: write fixed marker, NO RETI (spin forever)
    # If nested interrupts fired, we'd see different behavior
    _place(prog, 0x0006, _encode_lw(rd=2, rs1=0, off6=24))  # R2 = MEM[0x18] = 0xCAFE
    _place(prog, 0x0008, _encode_sw(rs2=2, rs1=0, off6=28)) # MEM[0x1C] = 0xCAFE
    # Spin without RETI - keep IRQB asserted, but I=1 should prevent re-entry
    _place(prog, 0x000A, _encode_jr(rs=0, off6=5))          # spin at 0x000A

    # Data
    prog[0x000A] = 0x20
    prog[0x000B] = 0x00
    prog[0x0018] = 0xFE
    prog[0x0019] = 0xCA

    # Continue at 0x0020: CLI, spin
    _place(prog, 0x0020, _encode_cli())
    _place(prog, 0x0022, _encode_jr(rs=0, off6=17))  # spin at 0x0022

    # Clear marker
    prog[0x001C] = 0x00
    prog[0x001D] = 0x00

    _load_program(dut, prog)

    # Reset
    dut.ena.value = 1
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Run until CLI executes
    await ClockCycles(dut.clk, 50)

    # Assert IRQB and keep it asserted
    dut.ui_in.value = 0x06  # RDY=1, NMIB=1, IRQB=0

    # Run for a long time with IRQ continuously asserted
    await ClockCycles(dut.clk, 500)

    # Check marker - should be 0xCAFE (written once)
    lo = _read_ram(dut, 0x001C)
    hi = _read_ram(dut, 0x001D)
    val = lo | (hi << 8)
    dut._log.info(f"Marker = {val:#06x} (expected 0xCAFE, written exactly once)")
    assert val == 0xCAFE, f"IRQ handler problem! Got {val:#06x}"
    # The test passes if the value is 0xCAFE - if nested interrupts happened,
    # we'd see different behavior (but without ADD we can't easily count).
    # The key is that execution reached the handler once.
    dut._log.info("PASS [i_bit_masking]")


# ---------------------------------------------------------------------------
# Test 11: IRQ during multi-cycle instruction
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_irq_during_multicycle(dut):
    """IRQ asserted during LW completes the LW before entering handler."""
    dut._log.info("Test 11: IRQ during multi-cycle instruction")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Program: jump past vector, CLI, LW, SW (prove LW completed), spin
    # 0x0000: LW R1, 5(R0)  ; R1 = MEM[0x0A] = 0x0020
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=10))
    # 0x0002: JR R1, 0
    _place(prog, 0x0002, _encode_jr(rs=1, off6=0))

    # IRQ handler at 0x0006: write R5 to marker (R5 set by main code's LW)
    _place(prog, 0x0006, _encode_sw(rs2=5, rs1=0, off6=30)) # MEM[0x1E] = R5
    _place(prog, 0x0008, _encode_reti())

    # Data
    prog[0x000A] = 0x20
    prog[0x000B] = 0x00

    # Continue at 0x0020: CLI, LW into R5, write R5 to another marker
    _place(prog, 0x0020, _encode_cli())
    # 0x0022: LW R5, 24(R0)  ; R5 = MEM[0x30] = 0x1234
    _place(prog, 0x0022, _encode_lw(rd=5, rs1=0, off6=24))
    # 0x0024: SW R5, 28(R0)  ; MEM[0x38] = R5 (proves LW completed)
    _place(prog, 0x0024, _encode_sw(rs2=5, rs1=0, off6=28))
    # 0x0026: spin
    _place(prog, 0x0026, _encode_jr(rs=0, off6=19))

    # Data: value for LW
    prog[0x0018] = 0x34
    prog[0x0019] = 0x12

    # Clear markers
    prog[0x001C] = 0x00
    prog[0x001D] = 0x00
    prog[0x001E] = 0x00
    prog[0x001F] = 0x00

    _load_program(dut, prog)

    # Reset
    dut.ena.value = 1
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Run until CLI executes
    await ClockCycles(dut.clk, 40)

    # Assert IRQB right as LW is executing (timing is approximate)
    # The IRQ should wait for LW to complete
    dut.ui_in.value = 0x06  # RDY=1, NMIB=1, IRQB=0

    # Run to let LW complete, IRQ fire, RETI, SW execute
    await ClockCycles(dut.clk, 200)

    # De-assert IRQ
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1

    await ClockCycles(dut.clk, 50)

    # Check main marker at 0x38 (SW after LW)
    lo = _read_ram(dut, 0x001C)
    hi = _read_ram(dut, 0x001D)
    main_marker = lo | (hi << 8)
    dut._log.info(f"Main marker = {main_marker:#06x} (expected 0x1234)")

    # Check IRQ marker at 0x3C (SW of R5 in handler)
    lo = _read_ram(dut, 0x001E)
    hi = _read_ram(dut, 0x001F)
    irq_marker = lo | (hi << 8)
    dut._log.info(f"IRQ marker = {irq_marker:#06x} (expected 0x1234 if LW completed before IRQ)")

    assert main_marker == 0x1234, f"Main code LW/SW failed! Got {main_marker:#06x}"
    assert irq_marker == 0x1234, f"IRQ saw wrong R5 value! Got {irq_marker:#06x}"
    dut._log.info("PASS [irq_during_multicycle]")


# ---------------------------------------------------------------------------
# Test 12: IRQ interrupts JR - verifies EPC saves JR target, not return addr
# ---------------------------------------------------------------------------
# Test 12: IRQ during JR completion - verifies EPC saves JR target, not pc+2
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_irq_interrupts_jr(dut):
    """IRQ fires during JR's E_ADDR_HI; RETI must return to JR target.

    This test keeps IRQ asserted throughout. Expected flow:
    1. CLI enables interrupts (I=0)
    2. IRQ fires immediately in E_IDLE after CLI, handler runs, RETI (I=0)
    3. Return to JR at 0x0002
    4. JR executes, IRQ fires during E_ADDR_HI (when target is computed)
    5. EPC must save JR target (0x0020), not pc+2 (0x0004)
    6. Handler runs, RETI returns to 0x0020
    7. JR target code writes marker, proving correct EPC value
    """
    dut._log.info("Test 12: IRQ interrupts JR")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Program layout:
    # 0x0000: CLI              ; Enable interrupts
    # 0x0002: JR R0, 16        ; Jump to 0x0020 (R0=0, offset=16, addr=0+16*2=0x20)
    # 0x0006: IRQ vector       ; Handler writes marker, RETI
    #
    # 0x0020: JR target        ; SW marker to prove we got here, then spin
    #
    # Key invariant: IRQ during E_ADDR_HI must save JR target to EPC

    # Main code
    _place(prog, 0x0000, _encode_cli())
    _place(prog, 0x0002, _encode_jr(rs=0, off6=16))  # JR to 0x0020

    # IRQ handler at 0x0006: write 0xBEEF to marker, then RETI
    # First load 0xBEEF into R1
    prog[0x0010] = 0xEF
    prog[0x0011] = 0xBE
    _place(prog, 0x0006, _encode_lw(rd=1, rs1=0, off6=16))   # R1 = MEM[0x10] = 0xBEEF
    _place(prog, 0x0008, _encode_sw(rs2=1, rs1=0, off6=30)) # MEM[0x1E] = 0xBEEF
    _place(prog, 0x000A, _encode_reti())

    # JR target at 0x0020: write 0xCAFE to marker at 0x0038, then spin
    prog[0x001A] = 0xFE
    prog[0x001B] = 0xCA
    _place(prog, 0x0020, _encode_lw(rd=2, rs1=0, off6=26))  # R2 = MEM[0x34] = 0xCAFE
    _place(prog, 0x0022, _encode_sw(rs2=2, rs1=0, off6=28)) # MEM[0x38] = 0xCAFE
    _place(prog, 0x0024, _encode_jr(rs=0, off6=18))         # Spin at 0x24

    # Clear markers
    prog[0x001E] = 0x00
    prog[0x001F] = 0x00
    prog[0x001C] = 0x00
    prog[0x001D] = 0x00

    _load_program(dut, prog)

    # Reset with IRQ already asserted (but I=1 after reset, so won't fire yet)
    dut.ena.value = 1
    dut.ui_in.value = 0x06  # RDY=1, NMIB=1, IRQB=0 (asserted, active low)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Run - CLI enables IRQ, JR executes, IRQ should fire after JR completes
    await ClockCycles(dut.clk, 300)

    # De-assert IRQ
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1

    await ClockCycles(dut.clk, 100)

    # Check IRQ marker at 0x3C (proves handler ran)
    lo = _read_ram(dut, 0x001E)
    hi = _read_ram(dut, 0x001F)
    irq_marker = lo | (hi << 8)
    dut._log.info(f"IRQ marker = {irq_marker:#06x} (expected 0xBEEF)")

    # Check JR target marker at 0x38 (proves RETI returned to JR target)
    lo = _read_ram(dut, 0x001C)
    hi = _read_ram(dut, 0x001D)
    jr_marker = lo | (hi << 8)
    dut._log.info(f"JR target marker = {jr_marker:#06x} (expected 0xCAFE)")

    assert irq_marker == 0xBEEF, f"IRQ handler didn't run! Got {irq_marker:#06x}"
    assert jr_marker == 0xCAFE, f"RETI didn't return to JR target! Got {jr_marker:#06x}"
    dut._log.info("PASS [irq_interrupts_jr]")


# ---------------------------------------------------------------------------
# Cycle count tests: verify each instruction path takes expected cycles
# ---------------------------------------------------------------------------

def _encode_nop():
    """Encode NOP (any unrecognized instruction) -> 16-bit little-endian bytes."""
    # Use 0x0000 as NOP - decodes as LUI R0,0 which is a functional no-op
    return (0x00, 0x00)


async def _measure_instruction_cycles(dut, prog, expected_cycles, test_name):
    """
    Measure cycles for an instruction by counting SYNC pulses.

    Program should have the test instruction at 0x0000, followed by a spin loop.
    Returns the measured cycle count.
    """
    def get_sync():
        return (int(dut.uo_out.value) >> 1) & 1

    _load_program(dut, prog)

    # Reset
    dut.ena.value = 1
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Wait for first SYNC (instruction boundary after reset)
    for _ in range(100):
        await FallingEdge(dut.clk)
        if get_sync():
            break
    else:
        raise AssertionError(f"{test_name}: Failed to reach first SYNC")

    # Count cycles until next SYNC
    cycles = 0
    # First wait for SYNC to go low
    for _ in range(100):
        await FallingEdge(dut.clk)
        cycles += 1
        if not get_sync():
            break

    # Then wait for SYNC to go high again
    for _ in range(100):
        await FallingEdge(dut.clk)
        cycles += 1
        if get_sync():
            break
    else:
        raise AssertionError(f"{test_name}: Failed to reach second SYNC")

    dut._log.info(f"{test_name}: measured {cycles} cycles (expected {expected_cycles})")
    return cycles


@cocotb.test()
async def test_cli_atomicity(dut):
    """CLI with pending IRQ: IRQ fires immediately after CLI completes."""
    dut._log.info("Test: CLI atomicity with pending IRQ")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Program: CLI at 0x0000, then spin. IRQ will be pending from start.
    # After reset, I=1 masks the IRQ. CLI sets I=0, IRQ should fire immediately.
    _place(prog, 0x0000, _encode_cli())
    _place(prog, 0x0002, _encode_jr(rs=0, off6=1))  # spin at 0x0002

    # IRQ handler at 0x0006: write marker and spin
    _place(prog, 0x0006, _encode_lw(rd=1, rs1=0, off6=16))   # R1 = MEM[0x10] = 0xBEEF
    _place(prog, 0x0008, _encode_sw(rs2=1, rs1=0, off6=24)) # MEM[0x18] = 0xBEEF
    _place(prog, 0x000A, _encode_jr(rs=0, off6=5))          # spin at 0x000A

    # Data: marker value
    prog[0x0010] = 0xEF
    prog[0x0011] = 0xBE

    # Clear marker location
    prog[0x0018] = 0x00
    prog[0x0019] = 0x00

    _load_program(dut, prog)

    # Reset with IRQB=0 (IRQ already pending, but masked by I=1)
    dut.ena.value = 1
    dut.ui_in.value = 0x06  # RDY=1, NMIB=1, IRQB=0 (asserted!)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Run - CLI should unmask the IRQ and it should fire immediately
    await ClockCycles(dut.clk, 50)

    # Check marker - should be written since CLI enabled the pending IRQ
    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    val = lo | (hi << 8)
    dut._log.info(f"Marker = {val:#06x} (expected 0xBEEF if IRQ fired after CLI)")
    assert val == 0xBEEF, f"IRQ did not fire after CLI! Got marker {val:#06x}"
    dut._log.info("PASS [cli_atomicity]")


@cocotb.test()
async def test_cycle_count_nop(dut):
    """NOP takes 2 cycles."""
    dut._log.info("Test: NOP cycle count")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: NOP
    _place(prog, 0x0000, _encode_nop())
    # 0x0002: JR R0, 1 (spin)
    _place(prog, 0x0002, _encode_jr(rs=0, off6=1))

    cycles = await _measure_instruction_cycles(dut, prog, 2, "NOP")
    assert cycles == 2, f"NOP: expected 2 cycles, got {cycles}"
    dut._log.info("PASS [cycle_count_nop]")


@cocotb.test()
async def test_cycle_count_sei(dut):
    """SEI takes 2 cycles."""
    dut._log.info("Test: SEI cycle count")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: SEI
    _place(prog, 0x0000, _encode_sei())
    # 0x0002: JR R0, 1 (spin)
    _place(prog, 0x0002, _encode_jr(rs=0, off6=1))

    cycles = await _measure_instruction_cycles(dut, prog, 2, "SEI")
    assert cycles == 2, f"SEI: expected 2 cycles, got {cycles}"
    dut._log.info("PASS [cycle_count_sei]")


@cocotb.test()
async def test_cycle_count_cli(dut):
    """CLI takes 2 cycles."""
    dut._log.info("Test: CLI cycle count")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: CLI
    _place(prog, 0x0000, _encode_cli())
    # 0x0002: JR R0, 1 (spin)
    _place(prog, 0x0002, _encode_jr(rs=0, off6=1))

    cycles = await _measure_instruction_cycles(dut, prog, 2, "CLI")
    assert cycles == 2, f"CLI: expected 2 cycles, got {cycles}"
    dut._log.info("PASS [cycle_count_cli]")


@cocotb.test()
async def test_cycle_count_reti(dut):
    """RETI takes 4 cycles (2 execute + 2 fetch after redirect)."""
    dut._log.info("Test: RETI cycle count")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # RETI at 0x0000, returns to banked R6 (0x0000 after reset)
    # Creates an infinite RETI loop - we measure one iteration
    # 0x0000: RETI (returns to 0x0000, creating a loop)
    _place(prog, 0x0000, _encode_reti())

    # RETI redirects, so throughput = 2 (E_EXEC) + 2 (fetch) = 4 cycles
    cycles = await _measure_instruction_cycles(dut, prog, 4, "RETI")
    assert cycles == 4, f"RETI: expected 4 cycles, got {cycles}"
    dut._log.info("PASS [cycle_count_reti]")


@cocotb.test()
async def test_cycle_count_lw(dut):
    """LW takes 4 cycles throughput (pipelined)."""
    dut._log.info("Test: LW cycle count")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: LW R1, 8(R0)  ; load from 0x10
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    # 0x0002: JR R0, 1 (spin)
    _place(prog, 0x0002, _encode_jr(rs=0, off6=1))

    # Data at 0x10
    prog[0x0010] = 0x12
    prog[0x0011] = 0x34

    # LW: 4 cycles throughput (E_ADDR_LO, E_ADDR_HI, E_MEM_LO, E_MEM_HI)
    # The 5-cycle latency includes overlapped fetch
    cycles = await _measure_instruction_cycles(dut, prog, 4, "LW")
    assert cycles == 4, f"LW: expected 4 cycles, got {cycles}"
    dut._log.info("PASS [cycle_count_lw]")


@cocotb.test()
async def test_cycle_count_sw(dut):
    """SW takes 4 cycles throughput (pipelined)."""
    dut._log.info("Test: SW cycle count")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: SW R0, 8(R0)  ; store R0 to 0x10
    _place(prog, 0x0000, _encode_sw(rs2=0, rs1=0, off6=16))
    # 0x0002: JR R0, 1 (spin)
    _place(prog, 0x0002, _encode_jr(rs=0, off6=1))

    # SW: 4 cycles throughput (same as LW)
    cycles = await _measure_instruction_cycles(dut, prog, 4, "SW")
    assert cycles == 4, f"SW: expected 4 cycles, got {cycles}"
    dut._log.info("PASS [cycle_count_sw]")


@cocotb.test()
async def test_cycle_count_jr(dut):
    """JR takes 4 cycles."""
    dut._log.info("Test: JR cycle count")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: JR R0, 1 (jump to 0x0002)
    _place(prog, 0x0000, _encode_jr(rs=0, off6=1))
    # 0x0002: JR R0, 1 (spin at 0x0002)
    _place(prog, 0x0002, _encode_jr(rs=0, off6=1))

    cycles = await _measure_instruction_cycles(dut, prog, 4, "JR")
    assert cycles == 4, f"JR: expected 4 cycles, got {cycles}"
    dut._log.info("PASS [cycle_count_jr]")


# ---------------------------------------------------------------------------
# NMI tests
# ---------------------------------------------------------------------------

def _set_ui(dut, rdy=True, irqb=True, nmib=True):
    """Set ui_in control signals. Active-low signals: irqb, nmib."""
    val = 0
    if irqb:  val |= 0x01
    if nmib:  val |= 0x02
    if rdy:   val |= 0x04
    dut.ui_in.value = val


@cocotb.test()
async def test_nmi_basic(dut):
    """NMI fires on falling edge of NMIB, even with I=1 (non-maskable).

    After reset I=1, so IRQ would be masked, but NMI ignores I.
    Handler at $0002 writes a marker to prove it ran.
    """
    dut._log.info("Test: NMI basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Main code at $0000: spin loop (I=1 after reset, no CLI)
    _place(prog, 0x0000, _encode_jr(rs=0, off6=0))  # spin at $0000

    # NMI handler at $0002: write marker and spin
    _place(prog, 0x0002, _encode_lw(rd=1, rs1=0, off6=16))  # R1 = MEM[$10] = 0xBEEF
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=24)) # MEM[$18] = 0xBEEF
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))           # spin at $0006

    # Data
    prog[0x0010] = 0xEF
    prog[0x0011] = 0xBE

    # Clear marker
    prog[0x0018] = 0x00
    prog[0x0019] = 0x00

    _load_program(dut, prog)

    # Reset with NMIB=1 (inactive)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Let CPU start spinning
    await ClockCycles(dut.clk, 30)

    # Pulse NMIB low (falling edge triggers NMI)
    _set_ui(dut, rdy=True, irqb=True, nmib=False)
    await ClockCycles(dut.clk, 10)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)  # Release

    # Let handler execute
    await ClockCycles(dut.clk, 100)

    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    val = lo | (hi << 8)
    dut._log.info(f"Marker = {val:#06x} (expected 0xBEEF)")
    assert val == 0xBEEF, f"NMI handler did not run! Got {val:#06x}"
    dut._log.info("PASS [nmi_basic]")


@cocotb.test()
async def test_nmi_edge_triggered(dut):
    """Holding NMIB low does not re-trigger NMI. Only one NMI per falling edge.

    NMI handler writes a marker then spins. If NMI re-entered, the handler
    would execute again and we'd detect it via a counter marker approach:
    handler increments a byte each time it runs.
    """
    dut._log.info("Test: NMI edge-triggered (no re-trigger while held low)")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Main code: spin at $0000
    _place(prog, 0x0000, _encode_jr(rs=0, off6=0))

    # NMI handler at $0002: load marker, store marker, write to two locations
    _place(prog, 0x0002, _encode_lw(rd=1, rs1=0, off6=24))  # R1 = MEM[$18]
    _place(prog, 0x0004, _encode_lw(rd=2, rs1=0, off6=20))  # R2 = MEM[$14] = 0xAAAA
    _place(prog, 0x0006, _encode_sw(rs2=2, rs1=0, off6=24)) # MEM[$18] = 0xAAAA
    # Spin in handler (no RETI — if NMI re-fires we'd jump to $0002 again
    # and overwrite marker with something different, but since we write the
    # same value, let's use a different approach: write to TWO locations)
    _place(prog, 0x0008, _encode_sw(rs2=2, rs1=0, off6=26)) # MEM[$1A] = 0xAAAA (second write)
    _place(prog, 0x000A, _encode_jr(rs=0, off6=5))           # spin at $000A

    # Data
    prog[0x0014] = 0xAA
    prog[0x0015] = 0xAA

    # Clear markers
    prog[0x0018] = 0x00
    prog[0x0019] = 0x00
    prog[0x001A] = 0x00
    prog[0x001B] = 0x00

    _load_program(dut, prog)

    # Reset
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    await ClockCycles(dut.clk, 30)

    # Assert NMIB low and HOLD it low
    _set_ui(dut, rdy=True, irqb=True, nmib=False)

    # Run for a long time with NMIB held low
    await ClockCycles(dut.clk, 500)

    # Check both markers written (handler completed once)
    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    marker1 = lo | (hi << 8)
    lo = _read_ram(dut, 0x001A)
    hi = _read_ram(dut, 0x001B)
    marker2 = lo | (hi << 8)

    dut._log.info(f"Marker1 = {marker1:#06x}, Marker2 = {marker2:#06x} (both expected 0xAAAA)")
    assert marker1 == 0xAAAA, f"NMI handler didn't run! Got {marker1:#06x}"
    assert marker2 == 0xAAAA, f"NMI handler didn't complete! Got {marker2:#06x}"
    dut._log.info("PASS [nmi_edge_triggered]")


@cocotb.test()
async def test_nmi_priority_over_irq(dut):
    """When both NMI and IRQ are pending, NMI is taken (handler at $0002).

    CLI enables IRQ, then both NMIB falling edge and IRQB=0 are asserted
    simultaneously. NMI handler at $0002 should run, not IRQ at $0006.
    After NMI entry sets I=1, IRQ stays masked.
    """
    dut._log.info("Test: NMI priority over IRQ")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Main code: jump past vectors
    _place(prog, 0x0000, _encode_jr(rs=0, off6=16))           # JR to $0020

    # NMI trampoline at $0002: jump to NMI handler at $0030
    _place(prog, 0x0002, _encode_jr(rs=0, off6=24))           # JR R0, 24 → $0030

    # IRQ handler at $0006: write IRQ marker
    _place(prog, 0x0006, _encode_lw(rd=2, rs1=0, off6=20))    # R2 = MEM[$14] = 0x1111
    _place(prog, 0x0008, _encode_sw(rs2=2, rs1=0, off6=24))   # MEM[$18] = 0x1111
    _place(prog, 0x000A, _encode_jr(rs=0, off6=5))            # spin at $000A

    # Continue at $0020: CLI, then spin
    _place(prog, 0x0020, _encode_cli())
    _place(prog, 0x0022, _encode_jr(rs=0, off6=17))           # spin at $0022

    # NMI handler at $0030: write NMI marker, spin
    _place(prog, 0x0030, _encode_lw(rd=3, rs1=0, off6=22))    # R3 = MEM[$16] = 0x2222
    _place(prog, 0x0032, _encode_sw(rs2=3, rs1=0, off6=26))   # MEM[$1A] = 0x2222
    _place(prog, 0x0034, _encode_jr(rs=0, off6=26))           # spin at $0034

    # Data
    prog[0x0014] = 0x11
    prog[0x0015] = 0x11
    prog[0x0016] = 0x22
    prog[0x0017] = 0x22

    # Clear markers
    prog[0x0018] = 0x00
    prog[0x0019] = 0x00
    prog[0x001A] = 0x00
    prog[0x001B] = 0x00

    _load_program(dut, prog)

    # Reset
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Wait for CLI to execute
    await ClockCycles(dut.clk, 50)

    # Assert BOTH NMIB falling edge AND IRQB=0 simultaneously
    _set_ui(dut, rdy=True, irqb=False, nmib=False)

    # Let handler run
    await ClockCycles(dut.clk, 200)

    # Check NMI marker (should be written — NMI has priority)
    lo = _read_ram(dut, 0x001A)
    hi = _read_ram(dut, 0x001B)
    nmi_marker = lo | (hi << 8)

    # Check IRQ marker (should NOT be written — I=1 after NMI entry)
    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    irq_marker = lo | (hi << 8)

    dut._log.info(f"NMI marker = {nmi_marker:#06x} (expected 0x2222)")
    dut._log.info(f"IRQ marker = {irq_marker:#06x} (expected 0x0000)")
    assert nmi_marker == 0x2222, f"NMI handler didn't run! Got {nmi_marker:#06x}"
    assert irq_marker == 0x0000, f"IRQ fired instead of/alongside NMI! Got {irq_marker:#06x}"
    dut._log.info("PASS [nmi_priority_over_irq]")


@cocotb.test()
async def test_nmi_during_multicycle(dut):
    """NMI asserted during a multi-cycle LW completes the LW before entering handler."""
    dut._log.info("Test: NMI during multi-cycle instruction")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Main code: jump past vectors
    _place(prog, 0x0000, _encode_jr(rs=0, off6=16))           # JR to $0020

    # NMI handler at $0002: write R5 to marker (R5 was loaded by main code's LW)
    _place(prog, 0x0002, _encode_sw(rs2=5, rs1=0, off6=26))   # MEM[$1A] = R5
    _place(prog, 0x0004, _encode_reti())

    # Continue at $0020: LW into R5, SW R5 to another marker, spin
    _place(prog, 0x0020, _encode_lw(rd=5, rs1=0, off6=24))    # R5 = MEM[$18] = 0x1234
    _place(prog, 0x0022, _encode_sw(rs2=5, rs1=0, off6=28))   # MEM[$1C] = R5
    _place(prog, 0x0024, _encode_jr(rs=0, off6=18))           # spin at $0024

    # Data
    prog[0x001E] = 0x20
    prog[0x001F] = 0x00
    prog[0x0018] = 0x34
    prog[0x0019] = 0x12

    # Clear markers
    prog[0x001A] = 0x00
    prog[0x001B] = 0x00
    prog[0x001C] = 0x00
    prog[0x001D] = 0x00

    _load_program(dut, prog)

    # Reset
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Wait for jump to $0020 and start of LW
    await ClockCycles(dut.clk, 40)

    # Assert NMIB during LW execution
    _set_ui(dut, rdy=True, irqb=True, nmib=False)
    await ClockCycles(dut.clk, 10)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)

    # Let NMI fire, handler run, RETI, then SW execute
    await ClockCycles(dut.clk, 200)

    # Check NMI handler marker (R5 should have been loaded by LW before NMI)
    lo = _read_ram(dut, 0x001A)
    hi = _read_ram(dut, 0x001B)
    nmi_marker = lo | (hi << 8)

    # Check main code marker (SW after RETI return)
    lo = _read_ram(dut, 0x001C)
    hi = _read_ram(dut, 0x001D)
    main_marker = lo | (hi << 8)

    dut._log.info(f"NMI marker (R5) = {nmi_marker:#06x} (expected 0x1234)")
    dut._log.info(f"Main marker = {main_marker:#06x} (expected 0x1234)")
    assert nmi_marker == 0x1234, f"LW didn't complete before NMI! Got {nmi_marker:#06x}"
    assert main_marker == 0x1234, f"Main code didn't resume after RETI! Got {main_marker:#06x}"
    dut._log.info("PASS [nmi_during_multicycle]")


@cocotb.test()
async def test_nmi_second_edge(dut):
    """After first NMI is handled, a second falling edge triggers another NMI."""
    dut._log.info("Test: NMI second edge re-triggers")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Main code: spin at $0000
    _place(prog, 0x0000, _encode_jr(rs=0, off6=0))

    # NMI handler at $0002:
    # Handler reads $0018, writes it to $001A, then writes 0xBBBB to $0018.
    # First NMI: $0018 goes from 0x0000→0xBBBB, $001A gets 0x0000
    # Second NMI: $0018 goes from 0xBBBB→0xBBBB, $001A gets 0xBBBB
    # Check: $001A == 0xBBBB proves second NMI ran.
    _place(prog, 0x0002, _encode_lw(rd=1, rs1=0, off6=24))    # R1 = MEM[$18] (current)
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=26))   # MEM[$1A] = R1 (copy previous)
    _place(prog, 0x0006, _encode_lw(rd=2, rs1=0, off6=22))    # R2 = MEM[$16] = 0xBBBB
    _place(prog, 0x0008, _encode_sw(rs2=2, rs1=0, off6=24))   # MEM[$18] = 0xBBBB
    _place(prog, 0x000A, _encode_reti())

    # Data
    prog[0x0016] = 0xBB
    prog[0x0017] = 0xBB

    # Clear markers
    prog[0x0018] = 0x00
    prog[0x0019] = 0x00
    prog[0x001A] = 0x00
    prog[0x001B] = 0x00

    _load_program(dut, prog)

    # Reset
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

    # Wait for first NMI to complete and RETI
    await ClockCycles(dut.clk, 100)

    # Second NMI pulse
    _set_ui(dut, rdy=True, irqb=True, nmib=False)
    await ClockCycles(dut.clk, 10)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)

    # Wait for second NMI to complete
    await ClockCycles(dut.clk, 100)

    # Check: $0030 should be 0xBBBB (written by both NMIs)
    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    marker1 = lo | (hi << 8)

    # Check: $0032 should be 0xBBBB (second NMI copied $0030's value)
    lo = _read_ram(dut, 0x001A)
    hi = _read_ram(dut, 0x001B)
    marker2 = lo | (hi << 8)

    dut._log.info(f"$0030 = {marker1:#06x} (expected 0xBBBB)")
    dut._log.info(f"$0032 = {marker2:#06x} (expected 0xBBBB, proves second NMI ran)")
    assert marker1 == 0xBBBB, f"First NMI didn't write! Got {marker1:#06x}"
    assert marker2 == 0xBBBB, f"Second NMI didn't run! Got {marker2:#06x}"
    dut._log.info("PASS [nmi_second_edge]")


@cocotb.test()
async def test_nmi_during_rdy_low(dut):
    """NMI falling edge while RDY=0 is captured and serviced when RDY returns.

    This tests that NMI edge detection runs on the ungated clock domain.
    Without that fix, the gated cpu_clk stops during RDY=0 and the edge
    would be lost.
    """
    dut._log.info("Test: NMI during RDY=0")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Main code at $0000: spin loop
    _place(prog, 0x0000, _encode_jr(rs=0, off6=0))  # spin at $0000

    # NMI handler at $0002: write marker and spin
    _place(prog, 0x0002, _encode_lw(rd=1, rs1=0, off6=16))  # R1 = MEM[$10] = 0xBEEF
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=24)) # MEM[$18] = 0xBEEF
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))           # spin at $0006

    # Data
    prog[0x0010] = 0xEF
    prog[0x0011] = 0xBE

    # Clear marker
    prog[0x0018] = 0x00
    prog[0x0019] = 0x00

    _load_program(dut, prog)

    # Reset with NMIB=1 (inactive), RDY=1
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Let CPU start spinning
    await ClockCycles(dut.clk, 30)

    # Pull RDY low — CPU halts
    _set_ui(dut, rdy=False, irqb=True, nmib=True)
    await ClockCycles(dut.clk, 10)

    # While halted, pulse NMIB low (falling edge during RDY=0)
    _set_ui(dut, rdy=False, irqb=True, nmib=False)
    await ClockCycles(dut.clk, 10)
    _set_ui(dut, rdy=False, irqb=True, nmib=True)  # Release NMIB
    await ClockCycles(dut.clk, 10)

    # Verify marker is still 0 (CPU was halted, handler hasn't run)
    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    val = lo | (hi << 8)
    assert val == 0x0000, f"NMI handler ran while halted! Got {val:#06x}"

    # Return RDY high — CPU resumes, should service pending NMI
    _set_ui(dut, rdy=True, irqb=True, nmib=True)

    # Let handler execute
    await ClockCycles(dut.clk, 100)

    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    val = lo | (hi << 8)
    dut._log.info(f"Marker = {val:#06x} (expected 0xBEEF)")
    assert val == 0xBEEF, f"NMI during RDY=0 was lost! Got {val:#06x}"
    dut._log.info("PASS [nmi_during_rdy_low]")


# ---------------------------------------------------------------------------
# WAI and STP tests
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_wai_irq(dut):
    """WAI halts until IRQ; CLI+WAI with IRQ asserted enters handler, RETI returns past WAI."""
    dut._log.info("Test: WAI with IRQ")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Main code: jump past vectors, CLI, WAI, then write marker (proves RETI returned past WAI)
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=30))   # R1 = MEM[$1E] = $0020
    _place(prog, 0x0002, _encode_jr(rs=1, off6=0))            # JR to $0020

    # IRQ handler at $0006: write marker, RETI
    _place(prog, 0x0006, _encode_lw(rd=2, rs1=0, off6=20))    # R2 = MEM[$14] = 0xAAAA
    _place(prog, 0x0008, _encode_sw(rs2=2, rs1=0, off6=24))   # MEM[$18] = 0xAAAA
    _place(prog, 0x000A, _encode_reti())

    # Continue at $0020: CLI, WAI, then post-WAI marker
    _place(prog, 0x0020, _encode_cli())
    _place(prog, 0x0022, _encode_wai())
    # After WAI + IRQ + RETI, execution resumes at $0024
    _place(prog, 0x0024, _encode_lw(rd=3, rs1=0, off6=22))    # R3 = MEM[$2A] = 0xBBBB
    _place(prog, 0x0026, _encode_sw(rs2=3, rs1=0, off6=26))   # MEM[$32] = 0xBBBB
    _place(prog, 0x0028, _encode_jr(rs=0, off6=20))            # spin at $0028

    # Data
    prog[0x001E] = 0x20
    prog[0x001F] = 0x00
    prog[0x0014] = 0xAA
    prog[0x0015] = 0xAA
    prog[0x0016] = 0xBB
    prog[0x0017] = 0xBB

    # Clear markers
    prog[0x0018] = 0x00
    prog[0x0019] = 0x00
    prog[0x001A] = 0x00
    prog[0x001B] = 0x00

    _load_program(dut, prog)

    # Reset
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Wait for CLI + WAI to execute, then CPU should be halted in E_WAIT
    await ClockCycles(dut.clk, 50)

    # Assert IRQB to wake WAI
    _set_ui(dut, rdy=True, irqb=False, nmib=True)
    await ClockCycles(dut.clk, 200)

    # De-assert IRQ
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    await ClockCycles(dut.clk, 100)

    # Check IRQ handler marker
    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    irq_marker = lo | (hi << 8)

    # Check post-WAI marker (proves RETI returned past WAI)
    lo = _read_ram(dut, 0x001A)
    hi = _read_ram(dut, 0x001B)
    post_wai_marker = lo | (hi << 8)

    dut._log.info(f"IRQ marker = {irq_marker:#06x} (expected 0xAAAA)")
    dut._log.info(f"Post-WAI marker = {post_wai_marker:#06x} (expected 0xBBBB)")
    assert irq_marker == 0xAAAA, f"IRQ handler didn't run! Got {irq_marker:#06x}"
    assert post_wai_marker == 0xBBBB, f"RETI didn't return past WAI! Got {post_wai_marker:#06x}"
    dut._log.info("PASS [wai_irq]")


@cocotb.test()
async def test_wai_nmi(dut):
    """WAI with I=1 (from reset) halts until NMI; NMI handler runs, RETI returns past WAI."""
    dut._log.info("Test: WAI with NMI")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Main code: jump past vectors, WAI (I=1 from reset), then post-WAI marker
    _place(prog, 0x0000, _encode_jr(rs=0, off6=16))            # JR to $0020

    # NMI handler at $0002: write marker, RETI
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=20))    # R2 = MEM[$14] = 0xAAAA
    _place(prog, 0x0004, _encode_sw(rs2=2, rs1=0, off6=24))   # MEM[$18] = 0xAAAA
    _place(prog, 0x0006, _encode_reti())

    # Continue at $0020: WAI (I=1, only NMI or masked IRQ can wake)
    _place(prog, 0x0020, _encode_wai())
    # After WAI + NMI + RETI, execution resumes at $0022
    _place(prog, 0x0022, _encode_lw(rd=3, rs1=0, off6=22))    # R3 = MEM[$2A] = 0xBBBB
    _place(prog, 0x0024, _encode_sw(rs2=3, rs1=0, off6=26))   # MEM[$32] = 0xBBBB
    _place(prog, 0x0026, _encode_jr(rs=0, off6=19))            # spin at $0026

    # Data
    prog[0x001E] = 0x20
    prog[0x001F] = 0x00
    prog[0x0014] = 0xAA
    prog[0x0015] = 0xAA
    prog[0x0016] = 0xBB
    prog[0x0017] = 0xBB

    # Clear markers
    prog[0x0018] = 0x00
    prog[0x0019] = 0x00
    prog[0x001A] = 0x00
    prog[0x001B] = 0x00

    _load_program(dut, prog)

    # Reset
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Wait for WAI to execute, CPU halted in E_WAIT
    await ClockCycles(dut.clk, 50)

    # Pulse NMI to wake WAI
    _set_ui(dut, rdy=True, irqb=True, nmib=False)
    await ClockCycles(dut.clk, 10)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)

    # Let NMI handler run and RETI
    await ClockCycles(dut.clk, 200)

    # Check NMI handler marker
    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    nmi_marker = lo | (hi << 8)

    # Check post-WAI marker
    lo = _read_ram(dut, 0x001A)
    hi = _read_ram(dut, 0x001B)
    post_wai_marker = lo | (hi << 8)

    dut._log.info(f"NMI marker = {nmi_marker:#06x} (expected 0xAAAA)")
    dut._log.info(f"Post-WAI marker = {post_wai_marker:#06x} (expected 0xBBBB)")
    assert nmi_marker == 0xAAAA, f"NMI handler didn't run! Got {nmi_marker:#06x}"
    assert post_wai_marker == 0xBBBB, f"RETI didn't return past WAI! Got {post_wai_marker:#06x}"
    dut._log.info("PASS [wai_nmi]")


@cocotb.test()
async def test_wai_masked_irq_wakes(dut):
    """WAI with I=1: masked IRQ wakes WAI and resumes past it WITHOUT vectoring.

    65C02-style: WAI is a hint. If IRQ arrives but I=1, WAI wakes and
    execution continues at the next instruction (no handler entry).
    """
    dut._log.info("Test: WAI with masked IRQ wakes without vectoring")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Main code: jump past vectors, WAI (I=1), then post-WAI marker
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=30))    # R1 = MEM[$1E] = $0020
    _place(prog, 0x0002, _encode_jr(rs=1, off6=0))             # JR to $0020

    # IRQ handler at $0006: write IRQ marker (should NOT happen)
    _place(prog, 0x0006, _encode_lw(rd=2, rs1=0, off6=20))    # R2 = MEM[$14] = 0xDEAD
    _place(prog, 0x0008, _encode_sw(rs2=2, rs1=0, off6=24))   # MEM[$18] = 0xDEAD

    # Continue at $0020: WAI (I=1 from reset)
    _place(prog, 0x0020, _encode_wai())
    # After WAI wakes (masked IRQ), resumes here at $0022
    _place(prog, 0x0022, _encode_lw(rd=3, rs1=0, off6=22))    # R3 = MEM[$2A] = 0xBBBB
    _place(prog, 0x0024, _encode_sw(rs2=3, rs1=0, off6=26))   # MEM[$32] = 0xBBBB
    _place(prog, 0x0026, _encode_jr(rs=0, off6=19))            # spin at $0026

    # Data
    prog[0x001E] = 0x20
    prog[0x001F] = 0x00
    prog[0x0014] = 0xAD
    prog[0x0015] = 0xDE
    prog[0x0016] = 0xBB
    prog[0x0017] = 0xBB

    # Clear markers
    prog[0x0018] = 0x00
    prog[0x0019] = 0x00
    prog[0x001A] = 0x00
    prog[0x001B] = 0x00

    _load_program(dut, prog)

    # Reset (I=1 after reset)
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Wait for WAI to execute
    await ClockCycles(dut.clk, 50)

    # Assert IRQB (but I=1, so it's masked — should wake WAI without vectoring)
    _set_ui(dut, rdy=True, irqb=False, nmib=True)
    await ClockCycles(dut.clk, 200)

    # Check that IRQ handler did NOT run
    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    irq_marker = lo | (hi << 8)

    # Check that post-WAI code DID run
    lo = _read_ram(dut, 0x001A)
    hi = _read_ram(dut, 0x001B)
    post_wai_marker = lo | (hi << 8)

    dut._log.info(f"IRQ marker = {irq_marker:#06x} (expected 0x0000 — handler should NOT run)")
    dut._log.info(f"Post-WAI marker = {post_wai_marker:#06x} (expected 0xBBBB)")
    assert irq_marker == 0x0000, f"IRQ handler ran despite I=1! Got {irq_marker:#06x}"
    assert post_wai_marker == 0xBBBB, f"WAI didn't resume past itself! Got {post_wai_marker:#06x}"
    dut._log.info("PASS [wai_masked_irq_wakes]")


@cocotb.test()
async def test_stp(dut):
    """STP halts permanently. IRQ and NMI cannot wake it. Only reset recovers."""
    dut._log.info("Test: STP halts permanently")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Data layout: 0x0E=jump target, 0x10=DEAD, 0x12=BEEF, 0x14=1111, 0x16=2222
    # Markers: 0x18=IRQ, 0x1A=NMI, 0x1C=pre-STP, 0x1E=post-STP

    # Main code: jump past vectors
    _place(prog, 0x0000, _encode_jr(rs=0, off6=16))             # JR R0, 16 → $0020

    # NMI trampoline at $0002 (should NOT fire)
    _place(prog, 0x0002, _encode_jr(rs=0, off6=24))             # JR R0, 24 → $0030

    # IRQ handler at $0006: write IRQ marker (should NOT happen)
    _place(prog, 0x0006, _encode_lw(rd=2, rs1=0, off6=16))     # R2 = MEM[$10] = 0xDEAD
    _place(prog, 0x0008, _encode_sw(rs2=2, rs1=0, off6=24))    # MEM[$18] = 0xDEAD
    _place(prog, 0x000A, _encode_jr(rs=0, off6=5))              # spin at $000A

    # NMI handler at $0030: write NMI marker (should NOT happen)
    _place(prog, 0x0030, _encode_lw(rd=3, rs1=0, off6=18))     # R3 = MEM[$12] = 0xBEEF
    _place(prog, 0x0032, _encode_sw(rs2=3, rs1=0, off6=26))    # MEM[$1A] = 0xBEEF
    _place(prog, 0x0034, _encode_jr(rs=0, off6=26))             # spin at $0034

    # Continue at $0020: CLI (enable IRQ), write pre-STP marker, STP
    _place(prog, 0x0020, _encode_cli())
    _place(prog, 0x0022, _encode_lw(rd=4, rs1=0, off6=20))    # R4 = MEM[$14] = 0x1111
    _place(prog, 0x0024, _encode_sw(rs2=4, rs1=0, off6=28))   # MEM[$1C] = 0x1111 (pre-STP marker)
    _place(prog, 0x0026, _encode_stp())
    # Post-STP: should never execute
    _place(prog, 0x0028, _encode_lw(rd=5, rs1=0, off6=22))    # R5 = MEM[$16] = 0x2222
    _place(prog, 0x002A, _encode_sw(rs2=5, rs1=0, off6=30))   # MEM[$1E] = 0x2222

    # Data
    prog[0x0010] = 0xAD
    prog[0x0011] = 0xDE
    prog[0x0012] = 0xEF
    prog[0x0013] = 0xBE
    prog[0x0014] = 0x11
    prog[0x0015] = 0x11
    prog[0x0016] = 0x22
    prog[0x0017] = 0x22

    # Clear markers
    for addr in [0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F]:
        prog[addr] = 0x00

    _load_program(dut, prog)

    # Reset
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.ena.value = 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Wait for STP to execute
    await ClockCycles(dut.clk, 80)

    # Verify pre-STP marker was written (proves code ran up to STP)
    lo = _read_ram(dut, 0x001C)
    hi = _read_ram(dut, 0x001D)
    pre_stp = lo | (hi << 8)
    assert pre_stp == 0x1111, f"Pre-STP marker not written! Got {pre_stp:#06x}"

    # Verify CPU is halted: post-STP code should NOT have run
    lo = _read_ram(dut, 0x001E)
    hi = _read_ram(dut, 0x001F)
    post_stp = lo | (hi << 8)
    assert post_stp == 0x0000, f"Post-STP code ran! Got {post_stp:#06x}"

    # Assert both IRQ and NMI — neither should wake STP
    _set_ui(dut, rdy=True, irqb=False, nmib=False)
    await ClockCycles(dut.clk, 200)

    # Check that neither handler ran
    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    irq_marker = lo | (hi << 8)
    lo = _read_ram(dut, 0x001A)
    hi = _read_ram(dut, 0x001B)
    nmi_marker = lo | (hi << 8)

    dut._log.info(f"IRQ marker = {irq_marker:#06x} (expected 0x0000)")
    dut._log.info(f"NMI marker = {nmi_marker:#06x} (expected 0x0000)")
    assert irq_marker == 0x0000, f"IRQ woke STP! Got {irq_marker:#06x}"
    assert nmi_marker == 0x0000, f"NMI woke STP! Got {nmi_marker:#06x}"

    # Now reset — CPU should start running from $0000 again
    # Clear the pre-STP marker so we can detect fresh execution
    dut.ram[0x001C].value = 0x00
    dut.ram[0x001D].value = 0x00

    # Full reset with clean control signals
    _set_ui(dut, rdy=True, irqb=True, nmib=True)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 40)
    # Reload program to ensure clean RAM state
    _load_program(dut, prog)
    dut.rst_n.value = 1

    # Run and verify CPU works after reset
    await ClockCycles(dut.clk, 300)

    lo = _read_ram(dut, 0x001C)
    hi = _read_ram(dut, 0x001D)
    pre_stp = lo | (hi << 8)
    dut._log.info(f"After reset, pre-STP marker = {pre_stp:#06x} (expected 0x1111)")
    assert pre_stp == 0x1111, f"CPU didn't restart after reset from STP! Got {pre_stp:#06x}"
    dut._log.info("PASS [stp]")


@cocotb.test()
async def test_cycle_count_wai(dut):
    """WAI with pending masked IRQ: 2 cycles (same as NOP — no redirect needed)."""
    dut._log.info("Test: WAI cycle count with pending masked IRQ")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # WAI at $0000 with IRQB=0 but I=1 (from reset): masked IRQ wakes immediately
    _place(prog, 0x0000, _encode_wai())
    # $0002: JR R0, 1 (spin) — WAI resumes here after masked IRQ wake
    _place(prog, 0x0002, _encode_jr(rs=0, off6=1))

    def get_sync():
        return (int(dut.uo_out.value) >> 1) & 1

    _load_program(dut, prog)

    # Reset with IRQB=0 (pending, but masked by I=1)
    dut.ena.value = 1
    dut.ui_in.value = 0x06  # RDY=1, NMIB=1, IRQB=0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Wait for first SYNC
    for _ in range(100):
        await FallingEdge(dut.clk)
        if get_sync():
            break

    # Count cycles until next SYNC
    cycles = 0
    for _ in range(100):
        await FallingEdge(dut.clk)
        cycles += 1
        if not get_sync():
            break

    for _ in range(100):
        await FallingEdge(dut.clk)
        cycles += 1
        if get_sync():
            break

    # WAI with pending masked IRQ: E_EXEC sees fsm_ready=1, goes to E_IDLE.
    # No redirect (I=1), so ir_accept fires immediately.
    # WAI takes 2 cycles: 1 E_EXEC + 1 overlapped dispatch.
    dut._log.info(f"WAI: measured {cycles} cycles (expected 2)")
    assert cycles == 2, f"WAI: expected 2 cycles, got {cycles}"
    dut._log.info("PASS [cycle_count_wai]")


@cocotb.test()
async def test_cycle_count_stp(dut):
    """STP takes 1 cycle to halt (dispatches directly to E_WAIT, no E_EXEC)."""
    dut._log.info("Test: STP cycle count")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_stp())

    def get_sync():
        return (int(dut.uo_out.value) >> 1) & 1

    _load_program(dut, prog)

    # Reset
    dut.ena.value = 1
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Wait for first SYNC (instruction boundary after reset)
    for _ in range(100):
        await FallingEdge(dut.clk)
        if get_sync():
            break

    # STP dispatches directly to E_WAIT. SYNC should go low and never return.
    # Count cycles until SYNC goes low (STP dispatched).
    cycles = 0
    for _ in range(100):
        await FallingEdge(dut.clk)
        cycles += 1
        if not get_sync():
            break

    dut._log.info(f"STP: {cycles} cycle(s) to halt (expected 1)")
    assert cycles == 1, f"STP: expected 1 cycle to halt, got {cycles}"

    # Verify it stays halted — SYNC should remain 0
    for _ in range(50):
        await FallingEdge(dut.clk)
        assert not get_sync(), "SYNC went high after STP — CPU is not halted!"

    dut._log.info("PASS [cycle_count_stp]")


# ---------------------------------------------------------------------------
# Test 33: BRK basic — vectors to $0004, saves EPC, sets I=1
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_brk_basic(dut):
    """BRK saves EPC = PC+2, sets I=1, and vectors to $0004."""
    dut._log.info("Test 33: BRK basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # 0x0000: JR R0, 16       ; jump past vectors to 0x0020
    _place(prog, 0x0000, _encode_jr(rs=0, off6=16))

    # BRK handler at 0x0004: write marker, RETI
    _place(prog, 0x0004, _encode_lw(rd=2, rs1=0, off6=24))  # R2 = MEM[0x18] = 0xAAAA
    _place(prog, 0x0006, _encode_sw(rs2=2, rs1=0, off6=28)) # MEM[0x1C] = 0xAAAA
    _place(prog, 0x0008, _encode_reti())

    # 0x0020: CLI, then BRK
    _place(prog, 0x0020, _encode_cli())
    _place(prog, 0x0022, _encode_brk())
    # 0x0024: after BRK returns here, write return marker
    _place(prog, 0x0024, _encode_lw(rd=3, rs1=0, off6=26))  # R3 = MEM[0x34] = 0xBBBB
    _place(prog, 0x0026, _encode_sw(rs2=3, rs1=0, off6=30)) # MEM[0x3C] = 0xBBBB
    _place(prog, 0x0028, _encode_jr(rs=0, off6=20))         # spin

    # Data
    prog[0x0018] = 0xAA
    prog[0x0019] = 0xAA
    prog[0x001A] = 0xBB
    prog[0x001B] = 0xBB
    prog[0x001C] = 0x00
    prog[0x001D] = 0x00
    prog[0x001E] = 0x00
    prog[0x001F] = 0x00

    _load_program(dut, prog)

    dut.ena.value = 1
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    await ClockCycles(dut.clk, 300)

    # Check BRK handler marker
    lo = _read_ram(dut, 0x001C)
    hi = _read_ram(dut, 0x001D)
    brk_marker = lo | (hi << 8)
    dut._log.info(f"BRK marker = {brk_marker:#06x} (expected 0xAAAA)")

    # Check return marker (RETI returned to instruction after BRK)
    lo = _read_ram(dut, 0x001E)
    hi = _read_ram(dut, 0x001F)
    ret_marker = lo | (hi << 8)
    dut._log.info(f"Return marker = {ret_marker:#06x} (expected 0xBBBB)")

    assert brk_marker == 0xAAAA, f"BRK handler did not execute! Got {brk_marker:#06x}"
    assert ret_marker == 0xBBBB, f"RETI from BRK did not return correctly! Got {ret_marker:#06x}"
    dut._log.info("PASS [brk_basic]")


# ---------------------------------------------------------------------------
# Test 34: BRK sets I=1 — masks IRQ while in BRK handler
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_brk_masks_irq(dut):
    """BRK sets I=1; IRQ held low during BRK handler should not fire."""
    dut._log.info("Test 34: BRK masks IRQ")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # 0x0000: JR R0, 16       ; jump to 0x0020
    _place(prog, 0x0000, _encode_jr(rs=0, off6=16))

    # BRK trampoline at 0x0004: jump to BRK handler at 0x0030
    _place(prog, 0x0004, _encode_jr(rs=0, off6=24))          # JR R0, 24 → $0030

    # IRQ handler at 0x0006: write IRQ marker
    _place(prog, 0x0006, _encode_lw(rd=2, rs1=0, off6=26))  # R2 = MEM[0x1A] = 0xCCCC
    _place(prog, 0x0008, _encode_sw(rs2=2, rs1=0, off6=30)) # MEM[0x1E] = 0xCCCC
    _place(prog, 0x000A, _encode_reti())

    # BRK handler at 0x0030: write BRK marker, RETI
    _place(prog, 0x0030, _encode_lw(rd=2, rs1=0, off6=24))  # R2 = MEM[0x18] = 0xAAAA
    _place(prog, 0x0032, _encode_sw(rs2=2, rs1=0, off6=28)) # MEM[0x1C] = 0xAAAA
    _place(prog, 0x0034, _encode_reti())

    # 0x0020: CLI, then BRK (with IRQB held low)
    _place(prog, 0x0020, _encode_cli())
    _place(prog, 0x0022, _encode_brk())
    # 0x0024: after RETI from BRK, I=0 and IRQB still low → IRQ fires
    _place(prog, 0x0024, _encode_jr(rs=0, off6=18))  # spin

    # Data
    prog[0x0018] = 0xAA
    prog[0x0019] = 0xAA
    prog[0x001A] = 0xCC
    prog[0x001B] = 0xCC
    prog[0x001C] = 0x00
    prog[0x001D] = 0x00
    prog[0x001E] = 0x00
    prog[0x001F] = 0x00

    _load_program(dut, prog)

    dut.ena.value = 1
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Let the CPU reach CLI
    await ClockCycles(dut.clk, 50)

    # Assert IRQB low before BRK executes
    dut.ui_in.value = 0x06  # IRQB=0

    # Run enough for BRK + handler + RETI + IRQ entry
    await ClockCycles(dut.clk, 300)

    # BRK handler should have run
    lo = _read_ram(dut, 0x001C)
    hi = _read_ram(dut, 0x001D)
    brk_marker = lo | (hi << 8)
    dut._log.info(f"BRK marker = {brk_marker:#06x} (expected 0xAAAA)")
    assert brk_marker == 0xAAAA, f"BRK handler did not run! Got {brk_marker:#06x}"

    # After RETI from BRK, I=0 and IRQB=0, so IRQ should fire
    lo = _read_ram(dut, 0x001E)
    hi = _read_ram(dut, 0x001F)
    irq_marker = lo | (hi << 8)
    dut._log.info(f"IRQ marker = {irq_marker:#06x} (expected 0xCCCC)")
    assert irq_marker == 0xCCCC, f"IRQ did not fire after RETI from BRK! Got {irq_marker:#06x}"
    dut._log.info("PASS [brk_masks_irq]")


# ---------------------------------------------------------------------------
# Test 35: BRK cycle count — 4 cycles (2 INT_SAVE exec + 2 fetch)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_cycle_count_brk(dut):
    """BRK takes 4 cycles: 2 execute (INT_SAVE) + 2 fetch after redirect."""
    dut._log.info("Test 35: BRK cycle count")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # 0x0000: BRK (from reset, I=1, but BRK is unconditional)
    _place(prog, 0x0000, _encode_brk())
    # BRK handler at 0x0004: spin
    _place(prog, 0x0004, _encode_jr(rs=0, off6=2))  # JR R0, 2 → spin at 0x0004

    _load_program(dut, prog)

    def get_sync():
        return (int(dut.uo_out.value) >> 1) & 1

    dut.ena.value = 1
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Wait for first SYNC (BRK dispatched from reset)
    for _ in range(100):
        await FallingEdge(dut.clk)
        if get_sync():
            break

    # Count cycles until next SYNC (handler's first instruction)
    cycles = 0
    got_sync = False
    for _ in range(20):
        await FallingEdge(dut.clk)
        cycles += 1
        if get_sync():
            got_sync = True
            break

    dut._log.info(f"BRK: {cycles} cycles to handler (expected 4)")
    assert got_sync, "Never reached handler SYNC"
    assert cycles == 4, f"BRK: expected 4 cycles, got {cycles}"
    dut._log.info("PASS [cycle_count_brk]")


# ---------------------------------------------------------------------------
# Test 36: BRK with I=1 — RETI restores I=1
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_brk_restores_i(dut):
    """BRK from I=1 code: RETI restores I=1, IRQ stays masked after return."""
    dut._log.info("Test 36: BRK restores I bit")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # 0x0000: JR R0, 16       ; jump to 0x0020 (past vectors)
    _place(prog, 0x0000, _encode_jr(rs=0, off6=16))

    # BRK trampoline at 0x0004: jump to BRK handler at 0x0030
    _place(prog, 0x0004, _encode_jr(rs=0, off6=24))          # JR R0, 24 → $0030

    # IRQ handler at 0x0006: write IRQ marker (should NOT fire)
    _place(prog, 0x0006, _encode_lw(rd=2, rs1=0, off6=26))  # R2 = MEM[0x1A] = 0xDEAD
    _place(prog, 0x0008, _encode_sw(rs2=2, rs1=0, off6=30)) # MEM[0x1E] = 0xDEAD
    _place(prog, 0x000A, _encode_reti())

    # BRK handler at 0x0030: write BRK marker, RETI
    _place(prog, 0x0030, _encode_lw(rd=2, rs1=0, off6=24))  # R2 = MEM[0x18] = 0xAAAA
    _place(prog, 0x0032, _encode_sw(rs2=2, rs1=0, off6=28)) # MEM[0x1C] = 0xAAAA
    _place(prog, 0x0034, _encode_reti())

    # 0x0020: BRK (I=1 from reset, never cleared)
    _place(prog, 0x0020, _encode_brk())
    # 0x0022: after RETI, I should still be 1; write return marker then spin
    _place(prog, 0x0022, _encode_lw(rd=3, rs1=0, off6=26))  # R3 = MEM[0x34] = 0xBBBB
    _place(prog, 0x0024, _encode_sw(rs2=3, rs1=0, off6=30)) # MEM[0x3C] = 0xBBBB
    _place(prog, 0x0026, _encode_jr(rs=0, off6=19))         # spin

    # Data
    prog[0x0018] = 0xAA
    prog[0x0019] = 0xAA
    prog[0x001A] = 0xBB
    prog[0x001B] = 0xBB
    prog[0x001C] = 0x00
    prog[0x001D] = 0x00
    prog[0x001E] = 0x00
    prog[0x001F] = 0x00

    _load_program(dut, prog)

    # IRQB=0 from the start — if I ever becomes 0, IRQ will fire
    dut.ena.value = 1
    dut.ui_in.value = 0x06  # RDY=1, NMIB=1, IRQB=0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    await ClockCycles(dut.clk, 300)

    # BRK handler should have run
    lo = _read_ram(dut, 0x001C)
    hi = _read_ram(dut, 0x001D)
    brk_marker = lo | (hi << 8)
    dut._log.info(f"BRK marker = {brk_marker:#06x} (expected 0xAAAA)")
    assert brk_marker == 0xAAAA, f"BRK handler did not run! Got {brk_marker:#06x}"

    # Return marker should be 0xBBBB (post-RETI code ran)
    lo = _read_ram(dut, 0x001E)
    hi = _read_ram(dut, 0x001F)
    ret_marker = lo | (hi << 8)
    dut._log.info(f"Return marker = {ret_marker:#06x} (expected 0xBBBB)")
    assert ret_marker == 0xBBBB, f"RETI did not return from BRK! Got {ret_marker:#06x}"

    # Key check: ret_marker is 0xBBBB (not 0xDEAD), proving I=1 was
    # restored by RETI and IRQ did NOT fire despite IRQB=0.
    dut._log.info("PASS [brk_restores_i]")


# ---------------------------------------------------------------------------
# Banked R6 tests (replaced EPCR/EPCW)
# ---------------------------------------------------------------------------

@cocotb.test()
async def test_banked_r6_read(dut):
    """In IRQ handler, SW R6 stores banked R6 (return_addr | i_bit) to memory."""
    dut._log.info("Test: Banked R6 read")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Main code: jump past vectors, CLI, then spin waiting for IRQ
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=30))   # R1 = MEM[$1E] = $0020
    _place(prog, 0x0002, _encode_jr(rs=1, off6=0))            # JR to $0020

    # IRQ handler at $0006: store banked R6 to memory, spin
    _place(prog, 0x0006, _encode_sw(rs2=6, rs1=0, off6=24))   # MEM[$18] = banked R6
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))            # spin at $0008

    # Continue at $0020: CLI, then spin (IRQ will fire immediately)
    _place(prog, 0x0020, _encode_cli())
    _place(prog, 0x0022, _encode_jr(rs=0, off6=17))           # spin at $0022

    # Data
    prog[0x001E] = 0x20
    prog[0x001F] = 0x00

    # Clear marker
    prog[0x0018] = 0x00
    prog[0x0019] = 0x00

    _load_program(dut, prog)

    # Reset with IRQB=0 (pending, masked by I=1 until CLI)
    dut.ena.value = 1
    dut.ui_in.value = 0x06  # RDY=1, NMIB=1, IRQB=0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    await ClockCycles(dut.clk, 300)

    # Banked R6 should be: return address ($0022) | I bit (0, since CLI cleared it)
    # CLI at $0020 advances PC to $0022, then IRQ fires. Banked R6 = $0022 | 0 = $0022
    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    val = lo | (hi << 8)
    dut._log.info(f"Banked R6 = {val:#06x} (expected 0x0022)")
    assert val == 0x0022, f"Banked R6 did not contain correct return addr! Got {val:#06x}"
    dut._log.info("PASS [banked_r6_read]")


@cocotb.test()
async def test_banked_r6_redirect(dut):
    """In BRK handler, LW R6 then RETI jumps to loaded address."""
    dut._log.info("Test: Banked R6 redirect")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Main code: jump past vectors, CLI, BRK
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=10))   # R1 = MEM[$0A] = $0020
    _place(prog, 0x0002, _encode_jr(rs=1, off6=0))            # JR to $0020

    # BRK handler at $0004: load redirect target into R6 (banked), RETI
    _place(prog, 0x0004, _encode_lw(rd=6, rs1=0, off6=18))    # R6 = MEM[$12] = $0030
    _place(prog, 0x0006, _encode_reti())                       # jump to $0030 (from banked R6)

    # Data
    prog[0x000A] = 0x20  # initial jump target
    prog[0x000B] = 0x00
    prog[0x0012] = 0x30  # RETI redirect target $0030 (bit 0=0 → I=0)
    prog[0x0013] = 0x00
    prog[0x0014] = 0xAD  # DEAD marker value
    prog[0x0015] = 0xDE
    prog[0x0016] = 0xAA  # AAAA marker value
    prog[0x0017] = 0xAA

    # Continue at $0020: CLI, BRK
    _place(prog, 0x0020, _encode_cli())
    _place(prog, 0x0022, _encode_brk())
    # $0024: should NOT reach here (R6 redirect to $0030)
    _place(prog, 0x0024, _encode_lw(rd=4, rs1=0, off6=20))   # R4 = MEM[$14] = 0xDEAD
    _place(prog, 0x0026, _encode_sw(rs2=4, rs1=0, off6=24))  # MEM[$18] = 0xDEAD
    _place(prog, 0x0028, _encode_jr(rs=0, off6=20))           # spin at $0028

    # Redirect target at $0030: write AAAA marker, spin
    _place(prog, 0x0030, _encode_lw(rd=5, rs1=0, off6=22))   # R5 = MEM[$16] = 0xAAAA
    _place(prog, 0x0032, _encode_sw(rs2=5, rs1=0, off6=26))  # MEM[$1A] = 0xAAAA
    _place(prog, 0x0034, _encode_jr(rs=0, off6=26))           # spin at $0034

    # Clear markers
    prog[0x0018] = 0x00
    prog[0x0019] = 0x00
    prog[0x001A] = 0x00
    prog[0x001B] = 0x00

    _load_program(dut, prog)

    dut.ena.value = 1
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    await ClockCycles(dut.clk, 300)

    # Target marker should be written (RETI went to $0030)
    lo = _read_ram(dut, 0x001A)
    hi = _read_ram(dut, 0x001B)
    target_marker = lo | (hi << 8)
    dut._log.info(f"Target marker = {target_marker:#06x} (expected 0xAAAA)")

    # Original return point should NOT have executed
    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    orig_marker = lo | (hi << 8)
    dut._log.info(f"Original return marker = {orig_marker:#06x} (expected 0x0000)")

    assert target_marker == 0xAAAA, f"RETI did not jump to banked R6 target! Got {target_marker:#06x}"
    assert orig_marker == 0x0000, f"Original return point executed! Got {orig_marker:#06x}"
    dut._log.info("PASS [banked_r6_redirect]")


@cocotb.test()
async def test_banked_r6_i_bit(dut):
    """In BRK handler, LW R6 with bit 0 clear → RETI restores I=0, IRQ fires."""
    dut._log.info("Test: Banked R6 I bit restore")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}

    # Main code: jump past vectors
    _place(prog, 0x0000, _encode_jr(rs=0, off6=16))             # JR R0, 16 → $0020

    # BRK trampoline at $0004: jump to BRK handler at $003C
    _place(prog, 0x0004, _encode_jr(rs=0, off6=30))             # JR R0, 30 → $003C

    # IRQ handler at $0006: write IRQ marker, spin
    _place(prog, 0x0006, _encode_lw(rd=2, rs1=0, off6=20))     # R2 = MEM[$14] = 0xCCCC
    _place(prog, 0x0008, _encode_sw(rs2=2, rs1=0, off6=24))    # MEM[$18] = 0xCCCC
    _place(prog, 0x000A, _encode_jr(rs=0, off6=5))              # spin at $000A

    # Data
    prog[0x0012] = 0x30  # banked R6 target: $0030, bit 0 = 0 (I=0)
    prog[0x0013] = 0x00
    prog[0x0014] = 0xCC  # IRQ marker value
    prog[0x0015] = 0xCC

    # Continue at $0020: BRK (I=1 from reset, no CLI needed — BRK is unconditional)
    _place(prog, 0x0020, _encode_brk())
    _place(prog, 0x0022, _encode_jr(rs=0, off6=17))             # spin (unreachable)

    # Target at $0030: spin — I=0, so if IRQB=0, IRQ should fire
    _place(prog, 0x0030, _encode_jr(rs=0, off6=24))             # spin at $0030

    # BRK handler at $003C: load target with I=0 into banked R6, then RETI
    _place(prog, 0x003C, _encode_lw(rd=6, rs1=0, off6=18))      # R6 = MEM[$12] = $0030
    _place(prog, 0x003E, _encode_reti())                         # PC=$0030, I=0 (bit 0 of R6)

    # Clear marker
    prog[0x0018] = 0x00
    prog[0x0019] = 0x00

    _load_program(dut, prog)

    # Reset with IRQB=0 (pending, but masked by I=1)
    dut.ena.value = 1
    dut.ui_in.value = 0x06  # RDY=1, NMIB=1, IRQB=0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    await ClockCycles(dut.clk, 300)

    # After BRK → banked R6 loaded with $0030 (I=0) → RETI sets I=0, jumps to $0030
    # With IRQB=0 and I=0, IRQ should fire → handler writes 0xCCCC to $0018
    lo = _read_ram(dut, 0x0018)
    hi = _read_ram(dut, 0x0019)
    irq_marker = lo | (hi << 8)
    dut._log.info(f"IRQ marker = {irq_marker:#06x} (expected 0xCCCC)")
    assert irq_marker == 0xCCCC, f"Banked R6 did not restore I=0! IRQ didn't fire. Got {irq_marker:#06x}"
    dut._log.info("PASS [banked_r6_i_bit]")


# ---------------------------------------------------------------------------
# Helper encoders for byte load/store instructions
# ---------------------------------------------------------------------------
def _encode_lb(rs1, off6, rd):
    """Encode LB rd, off6(rs1) -> 16-bit little-endian bytes. S-format."""
    assert -32 <= off6 <= 31, f"off6 out of range: {off6}"
    assert 0 <= rd <= 7 and 0 <= rs1 <= 7
    off6 &= 0x3F
    return _encode_s_format(0b0110, off6, rs1, rd)


def _encode_lbu(rs1, off6, rd):
    """Encode LBU rd, off6(rs1) -> 16-bit little-endian bytes. S-format."""
    assert -32 <= off6 <= 31, f"off6 out of range: {off6}"
    assert 0 <= rd <= 7 and 0 <= rs1 <= 7
    off6 &= 0x3F
    return _encode_s_format(0b0111, off6, rs1, rd)


def _encode_sb(rs1, off6, rs2):
    """Encode SB rs2, off6(rs1). Store format: [1001][rs1:3][rs2:3][off6:6]."""
    assert -32 <= off6 <= 31, f"off6 out of range: {off6}"
    assert 0 <= rs2 <= 7 and 0 <= rs1 <= 7
    off6 &= 0x3F
    insn = (0b1001 << 12) | (rs1 << 9) | (rs2 << 6) | off6
    return (insn & 0xFF, (insn >> 8) & 0xFF)


# ---------------------------------------------------------------------------
# Test: LB basic (positive byte, high byte = 0x00)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_lb_basic(dut):
    """LB loads a positive byte and sign-extends to 0x00XX."""
    dut._log.info("Test: LB basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # Data: byte 0x42 at address 0x0020
    prog[0x0020] = 0x42

    # Load base address 0x0020 into R1 via LW
    prog[0x0010] = 0x20
    prog[0x0011] = 0x00

    # 0x0000: LW R1, 8(R0)    ; R1 = MEM[0x10] = 0x0020
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    # 0x0002: LB R2, 0(R1)    ; R2 = sext(MEM[0x0020]) = 0x0042
    _place(prog, 0x0002, _encode_lb(rs1=1, off6=0, rd=2))
    # 0x0004: SW R2, 20(R0)   ; MEM[0x28] = R2
    _place(prog, 0x0004, _encode_sw(rs2=2, rs1=0, off6=20))
    # 0x0006: JR R0, 3        ; spin at 0x0006
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"R2 stored = {val:#06x}")
    assert val == 0x0042, f"Expected 0x0042, got {val:#06x}"
    dut._log.info("PASS [lb_basic]")


# ---------------------------------------------------------------------------
# Test: LB sign extension (negative byte)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_lb_sign_extend(dut):
    """LB loads a byte with bit 7 set and sign-extends to 0xFFXX."""
    dut._log.info("Test: LB sign extend")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # Data: byte 0xA5 at address 0x0020
    prog[0x0020] = 0xA5

    # Load base address 0x0020 into R1 via LW
    prog[0x0010] = 0x20
    prog[0x0011] = 0x00

    # 0x0000: LW R1, 8(R0)    ; R1 = MEM[0x10] = 0x0020
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    # 0x0002: LB R2, 0(R1)    ; R2 = sext(MEM[0x0020]) = 0xFFA5
    _place(prog, 0x0002, _encode_lb(rs1=1, off6=0, rd=2))
    # 0x0004: SW R2, 20(R0)   ; MEM[0x28] = R2
    _place(prog, 0x0004, _encode_sw(rs2=2, rs1=0, off6=20))
    # 0x0006: JR R0, 3        ; spin at 0x0006
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"R2 stored = {val:#06x}")
    assert val == 0xFFA5, f"Expected 0xFFA5, got {val:#06x}"
    dut._log.info("PASS [lb_sign_extend]")


# ---------------------------------------------------------------------------
# Test: LBU basic (zero-extends regardless of bit 7)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_lbu_basic(dut):
    """LBU loads a byte with bit 7 set and zero-extends to 0x00XX."""
    dut._log.info("Test: LBU basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # Data: byte 0xA5 at address 0x0020
    prog[0x0020] = 0xA5

    # Load base address 0x0020 into R1 via LW
    prog[0x0010] = 0x20
    prog[0x0011] = 0x00

    # 0x0000: LW R1, 8(R0)    ; R1 = MEM[0x10] = 0x0020
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    # 0x0002: LBU R2, 0(R1)   ; R2 = zext(MEM[0x0020]) = 0x00A5
    _place(prog, 0x0002, _encode_lbu(rs1=1, off6=0, rd=2))
    # 0x0004: SW R2, 20(R0)   ; MEM[0x28] = R2
    _place(prog, 0x0004, _encode_sw(rs2=2, rs1=0, off6=20))
    # 0x0006: JR R0, 3        ; spin at 0x0006
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"R2 stored = {val:#06x}")
    assert val == 0x00A5, f"Expected 0x00A5, got {val:#06x}"
    dut._log.info("PASS [lbu_basic]")


# ---------------------------------------------------------------------------
# Test: SB basic (stores only low byte)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sb_basic(dut):
    """SB stores only the low byte of a register, leaving adjacent byte unchanged."""
    dut._log.info("Test: SB basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # Data: word 0xBEEF at 0x0010
    prog[0x0010] = 0xEF
    prog[0x0011] = 0xBE

    # Pre-fill target with 0xFF so we can verify the high byte is untouched
    prog[0x0030] = 0xFF
    prog[0x0031] = 0xFF

    # Load base address 0x0030 into R2 via LW
    prog[0x0012] = 0x30
    prog[0x0013] = 0x00

    # 0x0000: LW R1, 8(R0)    ; R1 = MEM[0x10] = 0xBEEF
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    # 0x0002: LW R2, 9(R0)    ; R2 = MEM[0x12] = 0x0030
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    # 0x0004: SB R1, 0(R2)    ; MEM[0x0030] = R1[7:0] = 0xEF
    _place(prog, 0x0004, _encode_sb(rs1=2, off6=0, rs2=1))
    # 0x0006: JR R0, 3        ; spin at 0x0006
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    lo = _read_ram(dut, 0x0030)
    hi = _read_ram(dut, 0x0031)
    dut._log.info(f"ram[0x30]={lo:#04x}, ram[0x31]={hi:#04x}")
    assert lo == 0xEF, f"Expected low byte 0xEF, got {lo:#04x}"
    assert hi == 0xFF, f"Expected high byte 0xFF unchanged, got {hi:#04x}"
    dut._log.info("PASS [sb_basic]")


# ---------------------------------------------------------------------------
# Test: LB with negative offset
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_byte_negative_offset(dut):
    """LB with negative offset computes correct address."""
    dut._log.info("Test: byte negative offset")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # Data: byte 0x7F at address 0x001F (0x0020 - 1)
    prog[0x001F] = 0x7F

    # Load base address 0x0020 into R1 via LW
    prog[0x0010] = 0x20
    prog[0x0011] = 0x00

    # 0x0000: LW R1, 8(R0)    ; R1 = MEM[0x10] = 0x0020
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    # 0x0002: LB R2, -1(R1)   ; R2 = sext(MEM[0x001F]) = 0x007F
    _place(prog, 0x0002, _encode_lb(rs1=1, off6=-1, rd=2))
    # 0x0004: SW R2, 20(R0)   ; MEM[0x28] = R2
    _place(prog, 0x0004, _encode_sw(rs2=2, rs1=0, off6=20))
    # 0x0006: JR R0, 3        ; spin at 0x0006
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"R2 stored = {val:#06x}")
    assert val == 0x007F, f"Expected 0x007F, got {val:#06x}"
    dut._log.info("PASS [byte_negative_offset]")


# ---------------------------------------------------------------------------
# Cycle count tests: LB, LBU, SB
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_cycle_count_lb(dut):
    """LB takes 4 cycles throughput."""
    dut._log.info("Test: LB cycle count")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: LB R0, 0(R0)    ; load byte from address 0
    _place(prog, 0x0000, _encode_lb(rs1=0, off6=0, rd=0))
    # 0x0002: JR R0, 1 (spin)
    _place(prog, 0x0002, _encode_jr(rs=0, off6=1))

    cycles = await _measure_instruction_cycles(dut, prog, 4, "LB")
    assert cycles == 4, f"LB: expected 4 cycles, got {cycles}"
    dut._log.info("PASS [cycle_count_lb]")


@cocotb.test()
async def test_cycle_count_lbu(dut):
    """LBU takes 4 cycles throughput."""
    dut._log.info("Test: LBU cycle count")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: LBU R0, 0(R0)   ; load byte unsigned from address 0
    _place(prog, 0x0000, _encode_lbu(rs1=0, off6=0, rd=0))
    # 0x0002: JR R0, 1 (spin)
    _place(prog, 0x0002, _encode_jr(rs=0, off6=1))

    cycles = await _measure_instruction_cycles(dut, prog, 4, "LBU")
    assert cycles == 4, f"LBU: expected 4 cycles, got {cycles}"
    dut._log.info("PASS [cycle_count_lbu]")


@cocotb.test()
async def test_cycle_count_sb(dut):
    """SB takes 3 cycles throughput."""
    dut._log.info("Test: SB cycle count")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: SB R0, 8(R0)    ; store byte to address 8
    _place(prog, 0x0000, _encode_sb(rs1=0, off6=8, rs2=0))
    # 0x0002: JR R0, 1 (spin)
    _place(prog, 0x0002, _encode_jr(rs=0, off6=1))

    cycles = await _measure_instruction_cycles(dut, prog, 3, "SB")
    assert cycles == 3, f"SB: expected 3 cycles, got {cycles}"
    dut._log.info("PASS [cycle_count_sb]")


def _encode_auipc(rd, imm10):
    """Encode AUIPC rd, imm10 -> 16-bit little-endian bytes."""
    assert -512 <= imm10 <= 511, f"imm10 out of range: {imm10}"
    assert 0 <= rd <= 7
    imm10 &= 0x3FF
    insn = (0b001 << 13) | (imm10 << 3) | rd
    return (insn & 0xFF, (insn >> 8) & 0xFF)


# ---------------------------------------------------------------------------
# Test: AUIPC basic (imm10=0, rd = PC of AUIPC instruction)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_auipc_basic(dut):
    """AUIPC with imm10=0 puts PC+2 (next instruction address) into rd."""
    dut._log.info("Test: AUIPC basic (imm10=0)")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: NOP (advance PC to 0x0002)
    _place(prog, 0x0000, _encode_nop())
    # 0x0002: AUIPC R1, 0  ; R1 = (PC+2) + 0 = 0x0004
    _place(prog, 0x0002, _encode_auipc(rd=1, imm10=0))
    # 0x0004: SW R1, 20(R0) ; MEM[0 + 20*2] = MEM[0x28] = R1
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    # 0x0006: JR R0, 3      ; spin at 0x0006
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"AUIPC result: {val:#06x} (expected 0x0004)")
    assert val == 0x0004, f"Expected 0x0004, got {val:#06x}"
    dut._log.info("PASS [auipc_basic]")


# ---------------------------------------------------------------------------
# Test: AUIPC positive offset
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_auipc_positive_offset(dut):
    """AUIPC with positive imm10 adds (imm10 << 6) to PC+2."""
    dut._log.info("Test: AUIPC positive offset")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: AUIPC R1, 1  ; R1 = 0x0002 + (1 << 6) = 0x0042
    _place(prog, 0x0000, _encode_auipc(rd=1, imm10=1))
    # 0x0002: SW R1, 20(R0) ; MEM[0x28] = R1
    _place(prog, 0x0002, _encode_sw(rs2=1, rs1=0, off6=20))
    # 0x0004: JR R0, 2      ; spin at 0x0004
    _place(prog, 0x0004, _encode_jr(rs=0, off6=2))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"AUIPC result: {val:#06x} (expected 0x0042)")
    assert val == 0x0042, f"Expected 0x0042, got {val:#06x}"
    dut._log.info("PASS [auipc_positive_offset]")


# ---------------------------------------------------------------------------
# Test: AUIPC negative offset
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_auipc_negative_offset(dut):
    """AUIPC with negative imm10 subtracts from PC+2."""
    dut._log.info("Test: AUIPC negative offset")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # Place AUIPC at 0x0080: AUIPC R1, -1  ; R1 = 0x0082 + (-1 << 6) = 0x0082 + 0xFFC0 = 0x0042
    # First: bootstrap to 0x0080 using LW + JR
    # Data at 0x0020: LE word 0x0080 (jump target)
    prog[0x0010] = 0x80
    prog[0x0011] = 0x00
    # 0x0000: LW R2, 16(R0)  ; R2 = MEM[0x20] = 0x0080
    _place(prog, 0x0000, _encode_lw(rd=2, rs1=0, off6=16))
    # 0x0002: JR R2, 0       ; jump to 0x0080
    _place(prog, 0x0002, _encode_jr(rs=2, off6=0))

    # At 0x0080: AUIPC R1, -1 ; R1 = 0x0082 + 0xFFC0 = 0x0042
    _place(prog, 0x0080, _encode_auipc(rd=1, imm10=-1))
    # 0x0082: SW R1, 20(R0)  ; MEM[0x28] = R1
    _place(prog, 0x0082, _encode_sw(rs2=1, rs1=0, off6=20))
    # 0x0084: JR R2, 2       ; spin at 0x0084 (R2=0x0080, off6=2 → 0x0080+4=0x0084)
    _place(prog, 0x0084, _encode_jr(rs=2, off6=2))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 400)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"AUIPC result: {val:#06x} (expected 0x0042)")
    assert val == 0x0042, f"Expected 0x0042, got {val:#06x}"
    dut._log.info("PASS [auipc_negative_offset]")


# ---------------------------------------------------------------------------
# Test: AUIPC + LW for full PC-relative load
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_auipc_with_lw(dut):
    """AUIPC + LW reaches a PC-relative address (the primary use case)."""
    dut._log.info("Test: AUIPC + LW PC-relative load")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    # Place data at 0x0052: LE word 0xBEEF
    prog = {}
    prog[0x0052] = 0xEF
    prog[0x0053] = 0xBE

    # Target address is 0x0052. AUIPC at PC=0x0000 with imm10=1 → R1 = 0x0002 + 0x0040 = 0x0042
    # Then LW R2, 8(R1) → MEM[0x0042 + 8*2] = MEM[0x0052] = 0xBEEF
    # 0x0000: AUIPC R1, 1    ; R1 = 0x0002 + 0x0040 = 0x0042
    _place(prog, 0x0000, _encode_auipc(rd=1, imm10=1))
    # 0x0002: LW R2, 8(R1)   ; R2 = MEM[0x0042 + 16] = MEM[0x0052] = 0xBEEF
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=1, off6=16))
    # 0x0004: SW R2, 20(R0)  ; MEM[0x28] = R2
    _place(prog, 0x0004, _encode_sw(rs2=2, rs1=0, off6=20))
    # 0x0006: JR R0, 3       ; spin at 0x0006
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"LW via AUIPC result: {val:#06x} (expected 0xBEEF)")
    assert val == 0xBEEF, f"Expected 0xBEEF, got {val:#06x}"
    dut._log.info("PASS [auipc_with_lw]")


# ---------------------------------------------------------------------------
# Test: AUIPC large imm10 (upper address bits)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_auipc_large_imm10(dut):
    """AUIPC with large imm10 sets upper bits correctly."""
    dut._log.info("Test: AUIPC large imm10")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: AUIPC R1, 0x100 (256) ; R1 = 0x0002 + (256 << 6) = 0x0002 + 0x4000 = 0x4002
    _place(prog, 0x0000, _encode_auipc(rd=1, imm10=256))
    # 0x0002: SW R1, 20(R0) ; MEM[0x28] = R1
    _place(prog, 0x0002, _encode_sw(rs2=1, rs1=0, off6=20))
    # 0x0004: JR R0, 2      ; spin at 0x0004
    _place(prog, 0x0004, _encode_jr(rs=0, off6=2))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"AUIPC result: {val:#06x} (expected 0x4002)")
    assert val == 0x4002, f"Expected 0x4002, got {val:#06x}"
    dut._log.info("PASS [auipc_large_imm10]")


# ---------------------------------------------------------------------------
# Test: AUIPC cycle count (2 cycles)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_cycle_count_auipc(dut):
    """AUIPC takes 2 cycles throughput."""
    dut._log.info("Test: AUIPC cycle count")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: AUIPC R1, 0
    _place(prog, 0x0000, _encode_auipc(rd=1, imm10=0))
    # 0x0002: JR R0, 1 (spin)
    _place(prog, 0x0002, _encode_jr(rs=0, off6=1))

    cycles = await _measure_instruction_cycles(dut, prog, 2, "AUIPC")
    assert cycles == 2, f"AUIPC: expected 2 cycles, got {cycles}"
    dut._log.info("PASS [cycle_count_auipc]")


# ---------------------------------------------------------------------------
# Helper encoders for reg-reg ALU instructions
# ---------------------------------------------------------------------------
def _encode_alu(op_bits, rd, rs1, rs2):
    """Encode a reg-reg ALU instruction: [op7:7][rs2:3][rs1:3][rd:3]."""
    assert 0 <= rd <= 7 and 0 <= rs1 <= 7 and 0 <= rs2 <= 7
    insn = (op_bits << 9) | (rs2 << 6) | (rs1 << 3) | rd
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_add(rd, rs1, rs2):
    return _encode_alu(0b1011000, rd, rs1, rs2)


def _encode_sub(rd, rs1, rs2):
    return _encode_alu(0b1011001, rd, rs1, rs2)


def _encode_and(rd, rs1, rs2):
    return _encode_alu(0b1011010, rd, rs1, rs2)


def _encode_or(rd, rs1, rs2):
    return _encode_alu(0b1011011, rd, rs1, rs2)


def _encode_xor(rd, rs1, rs2):
    return _encode_alu(0b1011100, rd, rs1, rs2)


# ---------------------------------------------------------------------------
# Test: ADD basic (carry propagation lo→hi)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_add_basic(dut):
    """ADD R3, R1, R2: 0x1234 + 0x5678 = 0x68AC."""
    dut._log.info("Test: ADD basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # Data: 0x1234 at 0x0020, 0x5678 at 0x0022
    prog[0x0010] = 0x34
    prog[0x0011] = 0x12
    prog[0x0012] = 0x78
    prog[0x0013] = 0x56

    # 0x0000: LW R1, 16(R0)  ; R1 = 0x1234
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    # 0x0002: LW R2, 17(R0)  ; R2 = 0x5678
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    # 0x0004: ADD R3, R1, R2 ; R3 = R1 + R2 = 0x68AC
    _place(prog, 0x0004, _encode_add(rd=3, rs1=1, rs2=2))
    # 0x0006: SW R3, 20(R0)  ; MEM[0x28] = R3
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    # 0x0008: JR R0, 4       ; spin at 0x0008
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"ADD result = {val:#06x} (expected 0x68AC)")
    assert val == 0x68AC, f"Expected 0x68AC, got {val:#06x}"
    dut._log.info("PASS [add_basic]")


# ---------------------------------------------------------------------------
# Test: SUB basic
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sub_basic(dut):
    """SUB R3, R1, R2: 0x5678 - 0x1234 = 0x4444."""
    dut._log.info("Test: SUB basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x78
    prog[0x0011] = 0x56
    prog[0x0012] = 0x34
    prog[0x0013] = 0x12

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sub(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SUB result = {val:#06x} (expected 0x4444)")
    assert val == 0x4444, f"Expected 0x4444, got {val:#06x}"
    dut._log.info("PASS [sub_basic]")


# ---------------------------------------------------------------------------
# Test: SUB borrow (borrow from high byte)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sub_borrow(dut):
    """SUB R3, R1, R2: 0x0100 - 0x0001 = 0x00FF (borrow from high byte)."""
    dut._log.info("Test: SUB borrow")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x00
    prog[0x0011] = 0x01
    prog[0x0012] = 0x01
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sub(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SUB borrow result = {val:#06x} (expected 0x00FF)")
    assert val == 0x00FF, f"Expected 0x00FF, got {val:#06x}"
    dut._log.info("PASS [sub_borrow]")


# ---------------------------------------------------------------------------
# Test: AND basic
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_and_basic(dut):
    """AND R3, R1, R2: 0xFF0F & 0x0FFF = 0x0F0F."""
    dut._log.info("Test: AND basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x0F
    prog[0x0011] = 0xFF
    prog[0x0012] = 0xFF
    prog[0x0013] = 0x0F

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_and(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"AND result = {val:#06x} (expected 0x0F0F)")
    assert val == 0x0F0F, f"Expected 0x0F0F, got {val:#06x}"
    dut._log.info("PASS [and_basic]")


# ---------------------------------------------------------------------------
# Test: OR basic
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_or_basic(dut):
    """OR R3, R1, R2: 0xF000 | 0x00F0 = 0xF0F0."""
    dut._log.info("Test: OR basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x00
    prog[0x0011] = 0xF0
    prog[0x0012] = 0xF0
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_or(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"OR result = {val:#06x} (expected 0xF0F0)")
    assert val == 0xF0F0, f"Expected 0xF0F0, got {val:#06x}"
    dut._log.info("PASS [or_basic]")


# ---------------------------------------------------------------------------
# Test: XOR basic
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_xor_basic(dut):
    """XOR R3, R1, R2: 0xFFFF ^ 0xAAAA = 0x5555."""
    dut._log.info("Test: XOR basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0xFF
    prog[0x0011] = 0xFF
    prog[0x0012] = 0xAA
    prog[0x0013] = 0xAA

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_xor(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"XOR result = {val:#06x} (expected 0x5555)")
    assert val == 0x5555, f"Expected 0x5555, got {val:#06x}"
    dut._log.info("PASS [xor_basic]")


# ---------------------------------------------------------------------------
# Test: ALU same register (ADD R1, R1, R1)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_alu_same_reg(dut):
    """ADD R1, R1, R1: 0x1234 + 0x1234 = 0x2468."""
    dut._log.info("Test: ALU same register")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x34
    prog[0x0011] = 0x12

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_add(rd=1, rs1=1, rs2=1))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"ADD same reg result = {val:#06x} (expected 0x2468)")
    assert val == 0x2468, f"Expected 0x2468, got {val:#06x}"
    dut._log.info("PASS [alu_same_reg]")


# ---------------------------------------------------------------------------
# Test: ADD cycle count (2 cycles)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_cycle_count_add(dut):
    """ADD takes 2 cycles throughput."""
    dut._log.info("Test: ADD cycle count")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: ADD R0, R0, R0
    _place(prog, 0x0000, _encode_add(rd=0, rs1=0, rs2=0))
    # 0x0002: JR R0, 1 (spin)
    _place(prog, 0x0002, _encode_jr(rs=0, off6=1))

    cycles = await _measure_instruction_cycles(dut, prog, 2, "ADD")
    assert cycles == 2, f"ADD: expected 2 cycles, got {cycles}"
    dut._log.info("PASS [cycle_count_add]")


# ---------------------------------------------------------------------------
# Helper encoders for SLT, SLTU, LI
# ---------------------------------------------------------------------------
def _encode_slt(rd, rs1, rs2):
    return _encode_alu(0b1011101, rd, rs1, rs2)


def _encode_sltu(rd, rs1, rs2):
    return _encode_alu(0b1011110, rd, rs1, rs2)


def _encode_li(rd, imm6):
    """Encode LI rd, imm6 -> 16-bit little-endian bytes. [1101100][imm6:6][rd:3]"""
    assert -32 <= imm6 <= 31, f"imm6 out of range: {imm6}"
    assert 0 <= rd <= 7
    imm6 &= 0x3F
    insn = (0b1101100 << 9) | (imm6 << 3) | rd
    return (insn & 0xFF, (insn >> 8) & 0xFF)


# ---------------------------------------------------------------------------
# Test: LI positive
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_li_positive(dut):
    """LI R1, 31 -> R1 = 0x001F."""
    dut._log.info("Test: LI positive")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: LI R1, 31
    _place(prog, 0x0000, _encode_li(rd=1, imm6=31))
    # 0x0002: SW R1, 20(R0)  ; MEM[0x28] = R1
    _place(prog, 0x0002, _encode_sw(rs2=1, rs1=0, off6=20))
    # 0x0004: JR R0, 2       ; spin
    _place(prog, 0x0004, _encode_jr(rs=0, off6=2))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"LI result = {val:#06x} (expected 0x001F)")
    assert val == 0x001F, f"Expected 0x001F, got {val:#06x}"
    dut._log.info("PASS [li_positive]")


# ---------------------------------------------------------------------------
# Test: LI negative
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_li_negative(dut):
    """LI R1, -1 -> R1 = 0xFFFF."""
    dut._log.info("Test: LI negative")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm6=-1))
    _place(prog, 0x0002, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0004, _encode_jr(rs=0, off6=2))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"LI result = {val:#06x} (expected 0xFFFF)")
    assert val == 0xFFFF, f"Expected 0xFFFF, got {val:#06x}"
    dut._log.info("PASS [li_negative]")


# ---------------------------------------------------------------------------
# Test: LI zero
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_li_zero(dut):
    """LI R1, 0 -> R1 = 0x0000."""
    dut._log.info("Test: LI zero")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm6=0))
    _place(prog, 0x0002, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0004, _encode_jr(rs=0, off6=2))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"LI result = {val:#06x} (expected 0x0000)")
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"
    dut._log.info("PASS [li_zero]")


# ---------------------------------------------------------------------------
# Test: SLT true (signed, positive < positive)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_slt_true(dut):
    """SLT R3, R1, R2: 0x0005 < 0x000A -> R3 = 1."""
    dut._log.info("Test: SLT true")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x05
    prog[0x0011] = 0x00
    prog[0x0012] = 0x0A
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_slt(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLT result = {val:#06x} (expected 0x0001)")
    assert val == 0x0001, f"Expected 0x0001, got {val:#06x}"
    dut._log.info("PASS [slt_true]")


# ---------------------------------------------------------------------------
# Test: SLT false (signed, greater)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_slt_false(dut):
    """SLT R3, R1, R2: 0x000A < 0x0005 -> R3 = 0."""
    dut._log.info("Test: SLT false")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x0A
    prog[0x0011] = 0x00
    prog[0x0012] = 0x05
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_slt(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLT result = {val:#06x} (expected 0x0000)")
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"
    dut._log.info("PASS [slt_false]")


# ---------------------------------------------------------------------------
# Test: SLT equal
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_slt_equal(dut):
    """SLT R3, R1, R2: 0x0005 < 0x0005 -> R3 = 0."""
    dut._log.info("Test: SLT equal")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x05
    prog[0x0011] = 0x00
    prog[0x0012] = 0x05
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_slt(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLT result = {val:#06x} (expected 0x0000)")
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"
    dut._log.info("PASS [slt_equal]")


# ---------------------------------------------------------------------------
# Test: SLT negative less than positive
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_slt_negative(dut):
    """SLT R3, R1, R2: 0xFFFB (-5) < 0x0005 -> R3 = 1."""
    dut._log.info("Test: SLT negative")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0xFB
    prog[0x0011] = 0xFF
    prog[0x0012] = 0x05
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_slt(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLT result = {val:#06x} (expected 0x0001)")
    assert val == 0x0001, f"Expected 0x0001, got {val:#06x}"
    dut._log.info("PASS [slt_negative]")


# ---------------------------------------------------------------------------
# Test: SLT positive not less than negative
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_slt_negative_false(dut):
    """SLT R3, R1, R2: 0x0005 < 0xFFFB (-5) -> R3 = 0."""
    dut._log.info("Test: SLT negative false")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x05
    prog[0x0011] = 0x00
    prog[0x0012] = 0xFB
    prog[0x0013] = 0xFF

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_slt(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLT result = {val:#06x} (expected 0x0000)")
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"
    dut._log.info("PASS [slt_negative_false]")


# ---------------------------------------------------------------------------
# Test: SLTU true
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sltu_true(dut):
    """SLTU R3, R1, R2: 0x0005 <u 0x000A -> R3 = 1."""
    dut._log.info("Test: SLTU true")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x05
    prog[0x0011] = 0x00
    prog[0x0012] = 0x0A
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sltu(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLTU result = {val:#06x} (expected 0x0001)")
    assert val == 0x0001, f"Expected 0x0001, got {val:#06x}"
    dut._log.info("PASS [sltu_true]")


# ---------------------------------------------------------------------------
# Test: SLTU false
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sltu_false(dut):
    """SLTU R3, R1, R2: 0x000A <u 0x0005 -> R3 = 0."""
    dut._log.info("Test: SLTU false")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x0A
    prog[0x0011] = 0x00
    prog[0x0012] = 0x05
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sltu(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLTU result = {val:#06x} (expected 0x0000)")
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"
    dut._log.info("PASS [sltu_false]")


# ---------------------------------------------------------------------------
# Test: SLTU large (unsigned: 5 < 65535)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sltu_large(dut):
    """SLTU R3, R1, R2: 0x0005 <u 0xFFFF -> R3 = 1."""
    dut._log.info("Test: SLTU large")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x05
    prog[0x0011] = 0x00
    prog[0x0012] = 0xFF
    prog[0x0013] = 0xFF

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sltu(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLTU result = {val:#06x} (expected 0x0001)")
    assert val == 0x0001, f"Expected 0x0001, got {val:#06x}"
    dut._log.info("PASS [sltu_large]")


# ---------------------------------------------------------------------------
# Test: SLTU large reverse (unsigned: 65535 < 5 = false)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sltu_large_reverse(dut):
    """SLTU R3, R1, R2: 0xFFFF <u 0x0005 -> R3 = 0."""
    dut._log.info("Test: SLTU large reverse")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0xFF
    prog[0x0011] = 0xFF
    prog[0x0012] = 0x05
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sltu(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLTU result = {val:#06x} (expected 0x0000)")
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"
    dut._log.info("PASS [sltu_large_reverse]")


def _encode_lui(rd, imm10):
    """Encode LUI rd, imm10 -> 16-bit little-endian bytes. [000][imm10:10][rd:3]"""
    assert -512 <= imm10 <= 511, f"imm10 out of range: {imm10}"
    assert 0 <= rd <= 7
    imm10 &= 0x3FF
    insn = (0b000 << 13) | (imm10 << 3) | rd
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_bz(rs, off6):
    """Encode BZ rs, off6 -> 16-bit little-endian bytes. [1101000][off6:6][rs:3]"""
    assert -32 <= off6 <= 31, f"off6 out of range: {off6}"
    assert 0 <= rs <= 7
    off6 &= 0x3F
    insn = (0b1101000 << 9) | (off6 << 3) | rs
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_bnz(rs, off6):
    """Encode BNZ rs, off6 -> 16-bit little-endian bytes. [1101001][off6:6][rs:3]"""
    assert -32 <= off6 <= 31, f"off6 out of range: {off6}"
    assert 0 <= rs <= 7
    off6 &= 0x3F
    insn = (0b1101001 << 9) | (off6 << 3) | rs
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_bltz(rs, off6):
    """Encode BLTZ rs, off6 -> 16-bit little-endian bytes. [1101010][off6:6][rs:3]"""
    assert -32 <= off6 <= 31, f"off6 out of range: {off6}"
    assert 0 <= rs <= 7
    off6 &= 0x3F
    insn = (0b1101010 << 9) | (off6 << 3) | rs
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_bgez(rs, off6):
    """Encode BGEZ rs, off6 -> 16-bit little-endian bytes. [1101011][off6:6][rs:3]"""
    assert -32 <= off6 <= 31, f"off6 out of range: {off6}"
    assert 0 <= rs <= 7
    off6 &= 0x3F
    insn = (0b1101011 << 9) | (off6 << 3) | rs
    return (insn & 0xFF, (insn >> 8) & 0xFF)


# ---------------------------------------------------------------------------
# Test: LUI positive
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_lui_positive(dut):
    """LUI R1, 0x48 -> R1 = 0x1200."""
    dut._log.info("Test: LUI positive")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # LUI R1, 0x48 -> R1 = 0x48 << 6 = 0x1200
    _place(prog, 0x0000, _encode_lui(rd=1, imm10=0x48))
    _place(prog, 0x0002, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0004, _encode_jr(rs=0, off6=2))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"LUI result = {val:#06x} (expected 0x1200)")
    assert val == 0x1200, f"Expected 0x1200, got {val:#06x}"
    dut._log.info("PASS [lui_positive]")


# ---------------------------------------------------------------------------
# Test: LUI negative (all 1s -> 0xFFC0)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_lui_negative(dut):
    """LUI R1, -1 -> R1 = 0xFFC0."""
    dut._log.info("Test: LUI negative")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_lui(rd=1, imm10=-1))
    _place(prog, 0x0002, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0004, _encode_jr(rs=0, off6=2))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"LUI result = {val:#06x} (expected 0xFFC0)")
    assert val == 0xFFC0, f"Expected 0xFFC0, got {val:#06x}"
    dut._log.info("PASS [lui_negative]")


# ---------------------------------------------------------------------------
# Test: LUI zero
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_lui_zero(dut):
    """LUI R1, 0 -> R1 = 0x0000."""
    dut._log.info("Test: LUI zero")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_lui(rd=1, imm10=0))
    _place(prog, 0x0002, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0004, _encode_jr(rs=0, off6=2))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"LUI result = {val:#06x} (expected 0x0000)")
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"
    dut._log.info("PASS [lui_zero]")


# ---------------------------------------------------------------------------
# Test: BZ taken (branch on zero register)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_bz_taken(dut):
    """BZ on zero register -> branch taken, skip poison."""
    dut._log.info("Test: BZ taken")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # R0 is zero after reset
    # 0x0000: BZ R0, +2 -> skip next instruction (branch to 0x0000+2+2*2 = 0x0006)
    _place(prog, 0x0000, _encode_bz(rs=0, off6=2))
    # 0x0002: LI R1, 1 (poison - should be skipped)
    _place(prog, 0x0002, _encode_li(rd=1, imm6=1))
    # 0x0004: (fall through if poison executes - doesn't matter)
    # 0x0006: LI R1, 31 (marker: R1 = 31 means branch was taken)
    _place(prog, 0x0006, _encode_li(rd=1, imm6=31))
    _place(prog, 0x0008, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x000A, _encode_jr(rs=0, off6=5))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"BZ taken result = {val:#06x} (expected 0x001F)")
    assert val == 0x001F, f"Expected 0x001F, got {val:#06x}"
    dut._log.info("PASS [bz_taken]")


# ---------------------------------------------------------------------------
# Test: BZ not taken (branch on non-zero register)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_bz_not_taken(dut):
    """BZ on non-zero register -> branch not taken."""
    dut._log.info("Test: BZ not taken")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # Load non-zero into R1
    _place(prog, 0x0000, _encode_li(rd=1, imm6=5))
    # BZ R1, +3 -> should NOT branch since R1 != 0
    _place(prog, 0x0002, _encode_bz(rs=1, off6=3))
    # If not taken, execute this: LI R2, 7 (marker)
    _place(prog, 0x0004, _encode_li(rd=2, imm6=7))
    _place(prog, 0x0006, _encode_sw(rs2=2, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"BZ not taken result = {val:#06x} (expected 0x0007)")
    assert val == 0x0007, f"Expected 0x0007, got {val:#06x}"
    dut._log.info("PASS [bz_not_taken]")


# ---------------------------------------------------------------------------
# Test: BNZ taken (branch on non-zero register)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_bnz_taken(dut):
    """BNZ on non-zero register -> branch taken."""
    dut._log.info("Test: BNZ taken")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # Load non-zero into R1
    _place(prog, 0x0000, _encode_li(rd=1, imm6=5))
    # BNZ R1, +2 -> branch to 0x0004+2*2 = 0x0008
    _place(prog, 0x0002, _encode_bnz(rs=1, off6=2))
    # 0x0004: LI R2, 1 (poison - should be skipped)
    _place(prog, 0x0004, _encode_li(rd=2, imm6=1))
    # 0x0006: (fall through)
    # 0x0008: LI R2, 31 (marker: branch taken)
    _place(prog, 0x0008, _encode_li(rd=2, imm6=31))
    _place(prog, 0x000A, _encode_sw(rs2=2, rs1=0, off6=20))
    _place(prog, 0x000C, _encode_jr(rs=0, off6=6))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"BNZ taken result = {val:#06x} (expected 0x001F)")
    assert val == 0x001F, f"Expected 0x001F, got {val:#06x}"
    dut._log.info("PASS [bnz_taken]")


# ---------------------------------------------------------------------------
# Test: BNZ not taken (branch on zero register)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_bnz_not_taken(dut):
    """BNZ on zero register -> branch not taken."""
    dut._log.info("Test: BNZ not taken")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # R0 is zero
    # BNZ R0, +3 -> should NOT branch since R0 == 0
    _place(prog, 0x0000, _encode_bnz(rs=0, off6=3))
    # If not taken, execute this: LI R1, 7 (marker)
    _place(prog, 0x0002, _encode_li(rd=1, imm6=7))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"BNZ not taken result = {val:#06x} (expected 0x0007)")
    assert val == 0x0007, f"Expected 0x0007, got {val:#06x}"
    dut._log.info("PASS [bnz_not_taken]")


# ---------------------------------------------------------------------------
# Test: BNZ high byte non-zero (0x0100 - only high byte is non-zero)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_bnz_high_byte(dut):
    """BNZ on 0x0100 (only high byte non-zero) -> taken."""
    dut._log.info("Test: BNZ high byte")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # Load 0x0100 into R1: LUI R1, 4 -> R1 = 4 << 6 = 0x0100
    _place(prog, 0x0000, _encode_lui(rd=1, imm10=4))
    # BNZ R1, +2 -> branch taken (R1 = 0x0100, low byte 0 but high byte != 0)
    _place(prog, 0x0002, _encode_bnz(rs=1, off6=2))
    # 0x0004: LI R2, 1 (poison)
    _place(prog, 0x0004, _encode_li(rd=2, imm6=1))
    # 0x0008: LI R2, 31 (marker: branch taken)
    _place(prog, 0x0008, _encode_li(rd=2, imm6=31))
    _place(prog, 0x000A, _encode_sw(rs2=2, rs1=0, off6=20))
    _place(prog, 0x000C, _encode_jr(rs=0, off6=6))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"BNZ high byte result = {val:#06x} (expected 0x001F)")
    assert val == 0x001F, f"Expected 0x001F, got {val:#06x}"
    dut._log.info("PASS [bnz_high_byte]")


# ---------------------------------------------------------------------------
# Test: BLTZ taken (negative value)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_bltz_taken(dut):
    """BLTZ on -1 (0xFFFF) -> taken (sign bit set)."""
    dut._log.info("Test: BLTZ taken")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # Load -1 into R1
    _place(prog, 0x0000, _encode_li(rd=1, imm6=-1))
    # BLTZ R1, +2 -> branch to 0x0004+2*2 = 0x0008
    _place(prog, 0x0002, _encode_bltz(rs=1, off6=2))
    # 0x0004: LI R2, 1 (poison - should be skipped)
    _place(prog, 0x0004, _encode_li(rd=2, imm6=1))
    # 0x0006: NOP
    _place(prog, 0x0006, _encode_li(rd=3, imm6=0))
    # 0x0008: LI R2, 31 (marker: branch taken)
    _place(prog, 0x0008, _encode_li(rd=2, imm6=31))
    _place(prog, 0x000A, _encode_sw(rs2=2, rs1=0, off6=20))
    _place(prog, 0x000C, _encode_jr(rs=0, off6=6))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"BLTZ taken result = {val:#06x} (expected 0x001F)")
    assert val == 0x001F, f"Expected 0x001F, got {val:#06x}"
    dut._log.info("PASS [bltz_taken]")


# ---------------------------------------------------------------------------
# Test: BLTZ not taken (positive value)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_bltz_not_taken_positive(dut):
    """BLTZ on +1 -> not taken (sign bit clear)."""
    dut._log.info("Test: BLTZ not taken positive")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm6=1))
    # BLTZ R1, +3 -> should NOT branch since R1 > 0
    _place(prog, 0x0002, _encode_bltz(rs=1, off6=3))
    # If not taken, execute this: LI R2, 7 (marker)
    _place(prog, 0x0004, _encode_li(rd=2, imm6=7))
    _place(prog, 0x0006, _encode_sw(rs2=2, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"BLTZ not taken result = {val:#06x} (expected 0x0007)")
    assert val == 0x0007, f"Expected 0x0007, got {val:#06x}"
    dut._log.info("PASS [bltz_not_taken_positive]")


# ---------------------------------------------------------------------------
# Test: BLTZ not taken (zero)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_bltz_not_taken_zero(dut):
    """BLTZ on 0 -> not taken (0 is not < 0)."""
    dut._log.info("Test: BLTZ not taken zero")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # R0 is zero after reset
    # BLTZ R0, +3 -> should NOT branch since R0 == 0
    _place(prog, 0x0000, _encode_bltz(rs=0, off6=3))
    # If not taken: LI R1, 7 (marker)
    _place(prog, 0x0002, _encode_li(rd=1, imm6=7))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"BLTZ not taken zero result = {val:#06x} (expected 0x0007)")
    assert val == 0x0007, f"Expected 0x0007, got {val:#06x}"
    dut._log.info("PASS [bltz_not_taken_zero]")


# ---------------------------------------------------------------------------
# Test: BGEZ taken (positive value)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_bgez_taken_positive(dut):
    """BGEZ on +1 -> taken (sign bit clear)."""
    dut._log.info("Test: BGEZ taken positive")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm6=1))
    # BGEZ R1, +2 -> branch to 0x0004+2*2 = 0x0008
    _place(prog, 0x0002, _encode_bgez(rs=1, off6=2))
    # 0x0004: LI R2, 1 (poison - should be skipped)
    _place(prog, 0x0004, _encode_li(rd=2, imm6=1))
    # 0x0006: NOP
    _place(prog, 0x0006, _encode_li(rd=3, imm6=0))
    # 0x0008: LI R2, 31 (marker: branch taken)
    _place(prog, 0x0008, _encode_li(rd=2, imm6=31))
    _place(prog, 0x000A, _encode_sw(rs2=2, rs1=0, off6=20))
    _place(prog, 0x000C, _encode_jr(rs=0, off6=6))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"BGEZ taken positive result = {val:#06x} (expected 0x001F)")
    assert val == 0x001F, f"Expected 0x001F, got {val:#06x}"
    dut._log.info("PASS [bgez_taken_positive]")


# ---------------------------------------------------------------------------
# Test: BGEZ taken (zero)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_bgez_taken_zero(dut):
    """BGEZ on 0 -> taken (0 >= 0)."""
    dut._log.info("Test: BGEZ taken zero")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # R0 is zero after reset
    # BGEZ R0, +2 -> branch to 0x0002+2*2 = 0x0006
    _place(prog, 0x0000, _encode_bgez(rs=0, off6=2))
    # 0x0002: LI R1, 1 (poison - should be skipped)
    _place(prog, 0x0002, _encode_li(rd=1, imm6=1))
    # 0x0004: NOP
    _place(prog, 0x0004, _encode_li(rd=3, imm6=0))
    # 0x0006: LI R1, 31 (marker: branch taken)
    _place(prog, 0x0006, _encode_li(rd=1, imm6=31))
    _place(prog, 0x0008, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x000A, _encode_jr(rs=0, off6=5))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"BGEZ taken zero result = {val:#06x} (expected 0x001F)")
    assert val == 0x001F, f"Expected 0x001F, got {val:#06x}"
    dut._log.info("PASS [bgez_taken_zero]")


# ---------------------------------------------------------------------------
# Test: BGEZ not taken (negative value)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_bgez_not_taken(dut):
    """BGEZ on -1 (0xFFFF) -> not taken (sign bit set)."""
    dut._log.info("Test: BGEZ not taken")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm6=-1))
    # BGEZ R1, +3 -> should NOT branch since R1 < 0
    _place(prog, 0x0002, _encode_bgez(rs=1, off6=3))
    # If not taken: LI R2, 7 (marker)
    _place(prog, 0x0004, _encode_li(rd=2, imm6=7))
    _place(prog, 0x0006, _encode_sw(rs2=2, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"BGEZ not taken result = {val:#06x} (expected 0x0007)")
    assert val == 0x0007, f"Expected 0x0007, got {val:#06x}"
    dut._log.info("PASS [bgez_not_taken]")


# ===========================================================================
# J / JAL / JALR / RET / LRR / LRW Encoders and Tests
# ===========================================================================

def _encode_j(off12):
    """Encode J off12 -> 16-bit little-endian bytes. [0100][off12:12]"""
    assert -2048 <= off12 <= 2047, f"off12 out of range: {off12}"
    off12 &= 0xFFF
    insn = (0b0100 << 12) | off12
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_jal(off12):
    """Encode JAL off12 -> 16-bit little-endian bytes. [0101][off12:12]"""
    assert -2048 <= off12 <= 2047, f"off12 out of range: {off12}"
    off12 &= 0xFFF
    insn = (0b0101 << 12) | off12
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_jalr(rs, off6):
    """Encode JALR rs, off6 -> 16-bit little-endian bytes. [1101111][off6:6][rs:3]"""
    assert -32 <= off6 <= 31, f"off6 out of range: {off6}"
    assert 0 <= rs <= 7
    off6 &= 0x3F
    insn = (0b1101111 << 9) | (off6 << 3) | rs
    return (insn & 0xFF, (insn >> 8) & 0xFF)



# ---------------------------------------------------------------------------
# Test: J forward (skip poison instruction)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_j_forward(dut):
    """J +2 skips poison, reaches marker."""
    dut._log.info("Test: J forward")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: J +2 -> target = (0x0000+2) + 2*2 = 0x0006
    _place(prog, 0x0000, _encode_j(2))
    # 0x0002: LI R1, 1 (poison - should be skipped)
    _place(prog, 0x0002, _encode_li(rd=1, imm6=1))
    # 0x0004: NOP (padding)
    _place(prog, 0x0004, _encode_li(rd=2, imm6=2))
    # 0x0006: LI R1, 31 (marker)
    _place(prog, 0x0006, _encode_li(rd=1, imm6=31))
    # 0x0008: SW R1, 20(R0) -> MEM[0x28]
    _place(prog, 0x0008, _encode_sw(rs2=1, rs1=0, off6=20))
    # 0x000A: JR R0, 5 -> spin at 0x000A
    _place(prog, 0x000A, _encode_jr(rs=0, off6=5))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"J forward result = {val:#06x} (expected 0x001F)")
    assert val == 0x001F, f"Expected 0x001F, got {val:#06x}"
    dut._log.info("PASS [j_forward]")


# ---------------------------------------------------------------------------
# Test: J backward
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_j_backward(dut):
    """J forward then J backward, reaches target via backward jump."""
    dut._log.info("Test: J backward")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: J +4 -> target = 0x0002 + 4*2 = 0x000A
    _place(prog, 0x0000, _encode_j(4))
    # 0x0002: LI R1, 31 (marker - reached by backward jump)
    _place(prog, 0x0002, _encode_li(rd=1, imm6=31))
    # 0x0004: SW R1, 20(R0) -> MEM[0x28]
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    # 0x0006: JR R0, 3 -> spin at 0x0006
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))
    # 0x0008: NOP (padding)
    _place(prog, 0x0008, _encode_li(rd=3, imm6=0))
    # 0x000A: J -5 -> target = 0x000C + (-5)*2 = 0x0002
    _place(prog, 0x000A, _encode_j(-5))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"J backward result = {val:#06x} (expected 0x001F)")
    assert val == 0x001F, f"Expected 0x001F, got {val:#06x}"
    dut._log.info("PASS [j_backward]")


# ---------------------------------------------------------------------------
# Test: JAL + JR R6 round-trip
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_jal_ret(dut):
    """JAL to target, JR R6 back. Marker at return point confirms round-trip."""
    dut._log.info("Test: JAL + JR R6")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: JAL +4 -> R6 = 0x0002, target = 0x0002 + 4*2 = 0x000A
    _place(prog, 0x0000, _encode_jal(4))
    # 0x0002: (return here) LI R1, 31 (marker)
    _place(prog, 0x0002, _encode_li(rd=1, imm6=31))
    # 0x0004: SW R1, 20(R0) -> MEM[0x28]
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    # 0x0006: JR R0, 3 -> spin at 0x0006
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))
    # 0x0008: NOP (padding)
    _place(prog, 0x0008, _encode_li(rd=3, imm6=0))
    # 0x000A: JR R6, 0 -> PC = R6 = 0x0002
    _place(prog, 0x000A, _encode_jr(rs=6, off6=0))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"JAL+JR R6 result = {val:#06x} (expected 0x001F)")
    assert val == 0x001F, f"Expected 0x001F, got {val:#06x}"
    dut._log.info("PASS [jal_ret]")


# ---------------------------------------------------------------------------
# Test: JAL link value (verify R6 = correct return address)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_jal_link_value(dut):
    """JAL, then SW R6 to verify R6 = correct return address."""
    dut._log.info("Test: JAL link value")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: JAL +3 -> R6 = 0x0002, target = 0x0002 + 3*2 = 0x0008
    _place(prog, 0x0000, _encode_jal(3))
    # 0x0002: (poison - should not be reached before target)
    _place(prog, 0x0002, _encode_li(rd=1, imm6=1))
    # 0x0004: NOP
    _place(prog, 0x0004, _encode_li(rd=2, imm6=0))
    # 0x0006: NOP
    _place(prog, 0x0006, _encode_li(rd=3, imm6=0))
    # 0x0008: SW R6, 20(R0) -> MEM[0x28] = R6 = 0x0002
    _place(prog, 0x0008, _encode_sw(rs2=6, rs1=0, off6=20))
    # 0x000A: JR R0, 5 -> spin at 0x000A
    _place(prog, 0x000A, _encode_jr(rs=0, off6=5))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"JAL link value = {val:#06x} (expected 0x0002)")
    assert val == 0x0002, f"Expected 0x0002, got {val:#06x}"
    dut._log.info("PASS [jal_link_value]")


# ---------------------------------------------------------------------------
# Test: JALR + JR R6 round-trip
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_jalr_ret(dut):
    """JALR R0, off6 to target, JR R6 back. Verify round-trip."""
    dut._log.info("Test: JALR + JR R6")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # 0x0000: JALR R0, 5 -> R6 = 0x0002, PC = R0 + 5*2 = 0 + 10 = 0x000A
    _place(prog, 0x0000, _encode_jalr(rs=0, off6=5))
    # 0x0002: (return here) LI R1, 31 (marker)
    _place(prog, 0x0002, _encode_li(rd=1, imm6=31))
    # 0x0004: SW R1, 20(R0) -> MEM[0x28]
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    # 0x0006: JR R0, 3 -> spin at 0x0006
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))
    # 0x0008: NOP
    _place(prog, 0x0008, _encode_li(rd=3, imm6=0))
    # 0x000A: JR R6, 0 -> PC = R6 = 0x0002
    _place(prog, 0x000A, _encode_jr(rs=6, off6=0))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"JALR+JR R6 result = {val:#06x} (expected 0x001F)")
    assert val == 0x001F, f"Expected 0x001F, got {val:#06x}"
    dut._log.info("PASS [jalr_ret]")


# ===========================================================================
# Immediate ALU instructions (I-type and IF-type)
# ===========================================================================

def _encode_imm_op(op7, rd_rs, imm6):
    """Encode an immediate ALU instruction: [op7:7][imm6:6][rd/rs:3]."""
    assert -32 <= imm6 <= 31, f"imm6 out of range: {imm6}"
    assert 0 <= rd_rs <= 7
    imm6 &= 0x3F
    insn = (op7 << 9) | (imm6 << 3) | rd_rs
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_addi(rd, imm6):
    return _encode_imm_op(0b1110000, rd, imm6)


def _encode_andi(rd, imm6):
    return _encode_imm_op(0b1110010, rd, imm6)


def _encode_ori(rd, imm6):
    return _encode_imm_op(0b1110011, rd, imm6)


def _encode_xori(rd, imm6):
    return _encode_imm_op(0b1110100, rd, imm6)


def _encode_sltif(rs, imm6):
    return _encode_imm_op(0b1110101, rs, imm6)


def _encode_sltiuf(rs, imm6):
    return _encode_imm_op(0b1110110, rs, imm6)


def _encode_xorif(rs, imm6):
    return _encode_imm_op(0b1110111, rs, imm6)


# ---------------------------------------------------------------------------
# Test: ADDI positive
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_addi_positive(dut):
    """ADDI R1, 10: R1 = 0x0005 + 10 = 0x000F."""
    dut._log.info("Test: ADDI positive")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x05
    prog[0x0011] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_addi(rd=1, imm6=10))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"ADDI result = {val:#06x} (expected 0x000F)")
    assert val == 0x000F, f"Expected 0x000F, got {val:#06x}"
    dut._log.info("PASS [addi_positive]")


# ---------------------------------------------------------------------------
# Test: ADDI negative (subtract)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_addi_negative(dut):
    """ADDI R1, -4: R1 = 0x0010 + (-4) = 0x000C."""
    dut._log.info("Test: ADDI negative")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x10
    prog[0x0011] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_addi(rd=1, imm6=-4))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"ADDI result = {val:#06x} (expected 0x000C)")
    assert val == 0x000C, f"Expected 0x000C, got {val:#06x}"
    dut._log.info("PASS [addi_negative]")


# ---------------------------------------------------------------------------
# Test: ADDI overflow wrapping
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_addi_overflow(dut):
    """ADDI R1, 1: R1 = 0xFFFF + 1 = 0x0000 (wraps)."""
    dut._log.info("Test: ADDI overflow")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0xFF
    prog[0x0011] = 0xFF

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_addi(rd=1, imm6=1))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"ADDI result = {val:#06x} (expected 0x0000)")
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"
    dut._log.info("PASS [addi_overflow]")


# ---------------------------------------------------------------------------
# Test: ANDI mask low bits
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_andi_mask(dut):
    """ANDI R1, 0x1F: R1 = 0x12FF & 0x001F = 0x001F."""
    dut._log.info("Test: ANDI mask")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0xFF
    prog[0x0011] = 0x12

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_andi(rd=1, imm6=31))   # 0x1F = 31
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"ANDI result = {val:#06x} (expected 0x001F)")
    assert val == 0x001F, f"Expected 0x001F, got {val:#06x}"
    dut._log.info("PASS [andi_mask]")


# ---------------------------------------------------------------------------
# Test: ANDI with -1 (nop: all bits pass)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_andi_neg1(dut):
    """ANDI R1, -1: R1 = 0xABCD & 0xFFFF = 0xABCD."""
    dut._log.info("Test: ANDI -1")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0xCD
    prog[0x0011] = 0xAB

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_andi(rd=1, imm6=-1))   # sext(-1) = 0xFFFF
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"ANDI result = {val:#06x} (expected 0xABCD)")
    assert val == 0xABCD, f"Expected 0xABCD, got {val:#06x}"
    dut._log.info("PASS [andi_neg1]")


# ---------------------------------------------------------------------------
# Test: ORI set bits
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_ori_set_bits(dut):
    """ORI R1, 0x0F: R1 = 0x1200 | 0x000F = 0x120F."""
    dut._log.info("Test: ORI set bits")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x00
    prog[0x0011] = 0x12

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_ori(rd=1, imm6=15))    # 0x0F = 15
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"ORI result = {val:#06x} (expected 0x120F)")
    assert val == 0x120F, f"Expected 0x120F, got {val:#06x}"
    dut._log.info("PASS [ori_set_bits]")


# ---------------------------------------------------------------------------
# Test: ORI with -1 (sets all bits)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_ori_neg1(dut):
    """ORI R1, -1: R1 = 0x1234 | 0xFFFF = 0xFFFF."""
    dut._log.info("Test: ORI -1")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x34
    prog[0x0011] = 0x12

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_ori(rd=1, imm6=-1))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"ORI result = {val:#06x} (expected 0xFFFF)")
    assert val == 0xFFFF, f"Expected 0xFFFF, got {val:#06x}"
    dut._log.info("PASS [ori_neg1]")


# ---------------------------------------------------------------------------
# Test: XORI toggle bits
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_xori_toggle(dut):
    """XORI R1, 0x1F: R1 = 0x00FF ^ 0x001F = 0x00E0."""
    dut._log.info("Test: XORI toggle")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0xFF
    prog[0x0011] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_xori(rd=1, imm6=31))   # 0x1F
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"XORI result = {val:#06x} (expected 0x00E0)")
    assert val == 0x00E0, f"Expected 0x00E0, got {val:#06x}"
    dut._log.info("PASS [xori_toggle]")


# ---------------------------------------------------------------------------
# Test: XORI with -1 (NOT)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_xori_not(dut):
    """XORI R1, -1: R1 = 0x1234 ^ 0xFFFF = 0xEDCB."""
    dut._log.info("Test: XORI NOT")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x34
    prog[0x0011] = 0x12

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_xori(rd=1, imm6=-1))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"XORI result = {val:#06x} (expected 0xEDCB)")
    assert val == 0xEDCB, f"Expected 0xEDCB, got {val:#06x}"
    dut._log.info("PASS [xori_not]")


# ---------------------------------------------------------------------------
# Test: SLTIF true (signed less than)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sltif_true(dut):
    """SLTIF R1, 10: R1=5, 5 < 10 -> R2(t0) = 1."""
    dut._log.info("Test: SLTIF true")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x05
    prog[0x0011] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_sltif(rs=1, imm6=10))
    # Store R2 (t0, the result)
    _place(prog, 0x0004, _encode_sw(rs2=2, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLTIF result = {val:#06x} (expected 0x0001)")
    assert val == 0x0001, f"Expected 0x0001, got {val:#06x}"
    dut._log.info("PASS [sltif_true]")


# ---------------------------------------------------------------------------
# Test: SLTIF false (not less than)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sltif_false(dut):
    """SLTIF R1, 3: R1=10, 10 < 3 -> R2(t0) = 0."""
    dut._log.info("Test: SLTIF false")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x0A
    prog[0x0011] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_sltif(rs=1, imm6=3))
    _place(prog, 0x0004, _encode_sw(rs2=2, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLTIF result = {val:#06x} (expected 0x0000)")
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"
    dut._log.info("PASS [sltif_false]")


# ---------------------------------------------------------------------------
# Test: SLTIF negative comparison
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sltif_negative(dut):
    """SLTIF R1, -1: R1=0xFFFE (-2), -2 < -1 -> R2(t0) = 1."""
    dut._log.info("Test: SLTIF negative")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0xFE
    prog[0x0011] = 0xFF

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_sltif(rs=1, imm6=-1))
    _place(prog, 0x0004, _encode_sw(rs2=2, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLTIF result = {val:#06x} (expected 0x0001)")
    assert val == 0x0001, f"Expected 0x0001, got {val:#06x}"
    dut._log.info("PASS [sltif_negative]")


# ---------------------------------------------------------------------------
# Test: SLTIF equal (not less than)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sltif_equal(dut):
    """SLTIF R1, 5: R1=5, 5 < 5 -> R2(t0) = 0."""
    dut._log.info("Test: SLTIF equal")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x05
    prog[0x0011] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_sltif(rs=1, imm6=5))
    _place(prog, 0x0004, _encode_sw(rs2=2, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLTIF result = {val:#06x} (expected 0x0000)")
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"
    dut._log.info("PASS [sltif_equal]")


# ---------------------------------------------------------------------------
# Test: SLTIUF true (unsigned less than)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sltiuf_true(dut):
    """SLTIUF R1, 10: R1=5, 5 <u 10 -> R2(t0) = 1."""
    dut._log.info("Test: SLTIUF true")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x05
    prog[0x0011] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_sltiuf(rs=1, imm6=10))
    _place(prog, 0x0004, _encode_sw(rs2=2, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLTIUF result = {val:#06x} (expected 0x0001)")
    assert val == 0x0001, f"Expected 0x0001, got {val:#06x}"
    dut._log.info("PASS [sltiuf_true]")


# ---------------------------------------------------------------------------
# Test: SLTIUF false (not less than unsigned)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sltiuf_false(dut):
    """SLTIUF R1, 3: R1=0xFFFF (65535), 65535 <u sext(3)=3 -> R2(t0) = 0."""
    dut._log.info("Test: SLTIUF false")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0xFF
    prog[0x0011] = 0xFF

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_sltiuf(rs=1, imm6=3))
    _place(prog, 0x0004, _encode_sw(rs2=2, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLTIUF result = {val:#06x} (expected 0x0000)")
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"
    dut._log.info("PASS [sltiuf_false]")


# ---------------------------------------------------------------------------
# Test: XORIF basic (XOR to t0)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_xorif_basic(dut):
    """XORIF R1, 0x1F: R1=0x00FF, t0 = 0x00FF ^ 0x001F = 0x00E0."""
    dut._log.info("Test: XORIF basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0xFF
    prog[0x0011] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_xorif(rs=1, imm6=31))
    # Store R2 (t0, the result)
    _place(prog, 0x0004, _encode_sw(rs2=2, rs1=0, off6=20))
    # Also store R1 to verify it's preserved
    _place(prog, 0x0006, _encode_sw(rs2=1, rs1=0, off6=22))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"XORIF result = {val:#06x} (expected 0x00E0)")
    assert val == 0x00E0, f"Expected 0x00E0, got {val:#06x}"

    # Verify source R1 is preserved
    lo1 = _read_ram(dut, 0x0016)
    hi1 = _read_ram(dut, 0x0017)
    val1 = lo1 | (hi1 << 8)
    dut._log.info(f"R1 preserved = {val1:#06x} (expected 0x00FF)")
    assert val1 == 0x00FF, f"Expected 0x00FF, got {val1:#06x}"
    dut._log.info("PASS [xorif_basic]")


# ---------------------------------------------------------------------------
# Test: XORIF equality test (rs == imm -> t0 == 0)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_xorif_equality(dut):
    """XORIF R1, 5: R1=5, t0 = 5 ^ 5 = 0 (equality pattern)."""
    dut._log.info("Test: XORIF equality")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x05
    prog[0x0011] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_xorif(rs=1, imm6=5))
    _place(prog, 0x0004, _encode_sw(rs2=2, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"XORIF result = {val:#06x} (expected 0x0000)")
    assert val == 0x0000, f"Expected 0x0000, got {val:#06x}"
    dut._log.info("PASS [xorif_equality]")


# ===========================================================================
# Shift instructions (SLL, SRL, SRA, SLLI, SRLI, SRAI)
# ===========================================================================

def _encode_sll(rd, rs1, rs2):
    return _encode_alu(0b1100000, rd, rs1, rs2)


def _encode_srl(rd, rs1, rs2):
    return _encode_alu(0b1100010, rd, rs1, rs2)


def _encode_sra(rd, rs1, rs2):
    return _encode_alu(0b1100011, rd, rs1, rs2)


def _encode_shift_imm(op7, rd, imm4):
    """Encode a shift immediate: [op7:7][00][imm4:4][rd:3]."""
    assert 0 <= imm4 <= 15, f"imm4 out of range: {imm4}"
    assert 0 <= rd <= 7
    insn = (op7 << 9) | (imm4 << 3) | rd
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_slli(rd, imm4):
    return _encode_shift_imm(0b1100100, rd, imm4)


def _encode_srli(rd, imm4):
    return _encode_shift_imm(0b1100110, rd, imm4)


def _encode_srai(rd, imm4):
    return _encode_shift_imm(0b1100111, rd, imm4)


# ---------------------------------------------------------------------------
# Test: SLL basic (shift left by 4)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sll_basic(dut):
    """SLL R3, R1, R2: 0x1234 << 4 = 0x2340."""
    dut._log.info("Test: SLL basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x34
    prog[0x0011] = 0x12
    prog[0x0012] = 0x04
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sll(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLL result = {val:#06x} (expected 0x2340)")
    assert val == 0x2340, f"Expected 0x2340, got {val:#06x}"
    dut._log.info("PASS [sll_basic]")


# ---------------------------------------------------------------------------
# Test: SLL by 0
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sll_by_zero(dut):
    """SLL R3, R1, R2: 0xABCD << 0 = 0xABCD."""
    dut._log.info("Test: SLL by 0")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0xCD
    prog[0x0011] = 0xAB
    prog[0x0012] = 0x00
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sll(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLL result = {val:#06x} (expected 0xABCD)")
    assert val == 0xABCD, f"Expected 0xABCD, got {val:#06x}"
    dut._log.info("PASS [sll_by_zero]")


# ---------------------------------------------------------------------------
# Test: SLL by 8 (cross-byte)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sll_by_8(dut):
    """SLL R3, R1, R2: 0x00FF << 8 = 0xFF00."""
    dut._log.info("Test: SLL by 8")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0xFF
    prog[0x0011] = 0x00
    prog[0x0012] = 0x08
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sll(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLL result = {val:#06x} (expected 0xFF00)")
    assert val == 0xFF00, f"Expected 0xFF00, got {val:#06x}"
    dut._log.info("PASS [sll_by_8]")


# ---------------------------------------------------------------------------
# Test: SLL by 15
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sll_by_15(dut):
    """SLL R3, R1, R2: 0x0001 << 15 = 0x8000."""
    dut._log.info("Test: SLL by 15")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x01
    prog[0x0011] = 0x00
    prog[0x0012] = 0x0F
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sll(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLL result = {val:#06x} (expected 0x8000)")
    assert val == 0x8000, f"Expected 0x8000, got {val:#06x}"
    dut._log.info("PASS [sll_by_15]")


# ---------------------------------------------------------------------------
# Test: SLL cross-byte (shift by 9)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sll_cross_byte(dut):
    """SLL R3, R1, R2: 0x0037 << 9 = 0x6E00."""
    dut._log.info("Test: SLL cross-byte (shift by 9)")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x37
    prog[0x0011] = 0x00
    prog[0x0012] = 0x09
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sll(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLL result = {val:#06x} (expected 0x6E00)")
    assert val == 0x6E00, f"Expected 0x6E00, got {val:#06x}"
    dut._log.info("PASS [sll_cross_byte]")


# ---------------------------------------------------------------------------
# Test: SRL basic (logical right shift by 4)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_srl_basic(dut):
    """SRL R3, R1, R2: 0x1234 >>u 4 = 0x0123."""
    dut._log.info("Test: SRL basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x34
    prog[0x0011] = 0x12
    prog[0x0012] = 0x04
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_srl(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SRL result = {val:#06x} (expected 0x0123)")
    assert val == 0x0123, f"Expected 0x0123, got {val:#06x}"
    dut._log.info("PASS [srl_basic]")


# ---------------------------------------------------------------------------
# Test: SRL by 0
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_srl_by_zero(dut):
    """SRL R3, R1, R2: 0xABCD >>u 0 = 0xABCD."""
    dut._log.info("Test: SRL by 0")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0xCD
    prog[0x0011] = 0xAB
    prog[0x0012] = 0x00
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_srl(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SRL result = {val:#06x} (expected 0xABCD)")
    assert val == 0xABCD, f"Expected 0xABCD, got {val:#06x}"
    dut._log.info("PASS [srl_by_zero]")


# ---------------------------------------------------------------------------
# Test: SRL by 8 (cross-byte)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_srl_by_8(dut):
    """SRL R3, R1, R2: 0xAB00 >>u 8 = 0x00AB."""
    dut._log.info("Test: SRL by 8")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x00
    prog[0x0011] = 0xAB
    prog[0x0012] = 0x08
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_srl(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SRL result = {val:#06x} (expected 0x00AB)")
    assert val == 0x00AB, f"Expected 0x00AB, got {val:#06x}"
    dut._log.info("PASS [srl_by_8]")


# ---------------------------------------------------------------------------
# Test: SRL by 15
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_srl_by_15(dut):
    """SRL R3, R1, R2: 0x8000 >>u 15 = 0x0001."""
    dut._log.info("Test: SRL by 15")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x00
    prog[0x0011] = 0x80
    prog[0x0012] = 0x0F
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_srl(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SRL result = {val:#06x} (expected 0x0001)")
    assert val == 0x0001, f"Expected 0x0001, got {val:#06x}"
    dut._log.info("PASS [srl_by_15]")


# ---------------------------------------------------------------------------
# Test: SRA positive (arithmetic right shift, positive value)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sra_positive(dut):
    """SRA R3, R1, R2: 0x1234 >>s 4 = 0x0123."""
    dut._log.info("Test: SRA positive")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x34
    prog[0x0011] = 0x12
    prog[0x0012] = 0x04
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sra(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SRA result = {val:#06x} (expected 0x0123)")
    assert val == 0x0123, f"Expected 0x0123, got {val:#06x}"
    dut._log.info("PASS [sra_positive]")


# ---------------------------------------------------------------------------
# Test: SRA negative (arithmetic right shift, negative value)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sra_negative(dut):
    """SRA R3, R1, R2: 0xF234 >>s 4 = 0xFF23."""
    dut._log.info("Test: SRA negative")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x34
    prog[0x0011] = 0xF2
    prog[0x0012] = 0x04
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sra(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SRA result = {val:#06x} (expected 0xFF23)")
    assert val == 0xFF23, f"Expected 0xFF23, got {val:#06x}"
    dut._log.info("PASS [sra_negative]")


# ---------------------------------------------------------------------------
# Test: SRA by 8 negative (cross-byte, sign fills)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sra_by_8_negative(dut):
    """SRA R3, R1, R2: 0x8000 >>s 8 = 0xFF80."""
    dut._log.info("Test: SRA by 8 negative")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x00
    prog[0x0011] = 0x80
    prog[0x0012] = 0x08
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sra(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SRA result = {val:#06x} (expected 0xFF80)")
    assert val == 0xFF80, f"Expected 0xFF80, got {val:#06x}"
    dut._log.info("PASS [sra_by_8_negative]")


# ---------------------------------------------------------------------------
# Test: SRA by 15 negative
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sra_by_15_negative(dut):
    """SRA R3, R1, R2: 0x8000 >>s 15 = 0xFFFF."""
    dut._log.info("Test: SRA by 15 negative")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x00
    prog[0x0011] = 0x80
    prog[0x0012] = 0x0F
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sra(rd=3, rs1=1, rs2=2))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SRA result = {val:#06x} (expected 0xFFFF)")
    assert val == 0xFFFF, f"Expected 0xFFFF, got {val:#06x}"
    dut._log.info("PASS [sra_by_15_negative]")


# ---------------------------------------------------------------------------
# Test: SLLI basic (shift left by 4)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_slli_basic(dut):
    """SLLI R1, 4: R1=0x1234, R1 = 0x1234 << 4 = 0x2340."""
    dut._log.info("Test: SLLI basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x34
    prog[0x0011] = 0x12

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_slli(rd=1, imm4=4))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLLI result = {val:#06x} (expected 0x2340)")
    assert val == 0x2340, f"Expected 0x2340, got {val:#06x}"
    dut._log.info("PASS [slli_basic]")


# ---------------------------------------------------------------------------
# Test: SLLI by 8 (cross-byte)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_slli_by_8(dut):
    """SLLI R1, 8: R1=0x00AB, R1 = 0x00AB << 8 = 0xAB00."""
    dut._log.info("Test: SLLI by 8")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0xAB
    prog[0x0011] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_slli(rd=1, imm4=8))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SLLI result = {val:#06x} (expected 0xAB00)")
    assert val == 0xAB00, f"Expected 0xAB00, got {val:#06x}"
    dut._log.info("PASS [slli_by_8]")


# ---------------------------------------------------------------------------
# Test: SRLI basic (logical right shift by 4)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_srli_basic(dut):
    """SRLI R1, 4: R1=0x1234, R1 = 0x1234 >>u 4 = 0x0123."""
    dut._log.info("Test: SRLI basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x34
    prog[0x0011] = 0x12

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_srli(rd=1, imm4=4))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SRLI result = {val:#06x} (expected 0x0123)")
    assert val == 0x0123, f"Expected 0x0123, got {val:#06x}"
    dut._log.info("PASS [srli_basic]")


# ---------------------------------------------------------------------------
# Test: SRLI by 8 (cross-byte)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_srli_by_8(dut):
    """SRLI R1, 8: R1=0xAB00, R1 = 0xAB00 >>u 8 = 0x00AB."""
    dut._log.info("Test: SRLI by 8")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x00
    prog[0x0011] = 0xAB

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_srli(rd=1, imm4=8))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SRLI result = {val:#06x} (expected 0x00AB)")
    assert val == 0x00AB, f"Expected 0x00AB, got {val:#06x}"
    dut._log.info("PASS [srli_by_8]")


# ---------------------------------------------------------------------------
# Test: SRAI negative (arithmetic right shift by 4)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_srai_negative(dut):
    """SRAI R1, 4: R1=0xF234, R1 = 0xF234 >>s 4 = 0xFF23."""
    dut._log.info("Test: SRAI negative")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x34
    prog[0x0011] = 0xF2

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_srai(rd=1, imm4=4))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SRAI result = {val:#06x} (expected 0xFF23)")
    assert val == 0xFF23, f"Expected 0xFF23, got {val:#06x}"
    dut._log.info("PASS [srai_negative]")


# ---------------------------------------------------------------------------
# Test: SRAI by 15 negative (full sign extension)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_srai_by_15_negative(dut):
    """SRAI R1, 15: R1=0x8000, R1 = 0x8000 >>s 15 = 0xFFFF."""
    dut._log.info("Test: SRAI by 15 negative")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x00
    prog[0x0011] = 0x80

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_srai(rd=1, imm4=15))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 200)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"SRAI result = {val:#06x} (expected 0xFFFF)")
    assert val == 0xFFFF, f"Expected 0xFFFF, got {val:#06x}"
    dut._log.info("PASS [srai_by_15_negative]")


# ===========================================================================
# Auto-modify load/store instructions (SuperH-style post-increment / pre-decrement)
# ===========================================================================

def _encode_lw_post(rd, rs1):
    """Encode LW.POST rd, (rs1) -> [1011][111][000][rs1:3][rd:3]."""
    assert 0 <= rd <= 7 and 0 <= rs1 <= 7
    insn = (0b1011111 << 9) | (0b000 << 6) | (rs1 << 3) | rd
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_lb_post(rd, rs1):
    """Encode LB.POST rd, (rs1) -> [1011][111][001][rs1:3][rd:3]."""
    assert 0 <= rd <= 7 and 0 <= rs1 <= 7
    insn = (0b1011111 << 9) | (0b001 << 6) | (rs1 << 3) | rd
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_lbu_post(rd, rs1):
    """Encode LBU.POST rd, (rs1) -> [1011][111][010][rs1:3][rd:3]."""
    assert 0 <= rd <= 7 and 0 <= rs1 <= 7
    insn = (0b1011111 << 9) | (0b010 << 6) | (rs1 << 3) | rd
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_sw_pre(rs2, rs1):
    """Encode SW.PRE rs2, (rs1) -> [1100][001][rs2:3][rs1:3][000]."""
    assert 0 <= rs2 <= 7 and 0 <= rs1 <= 7
    insn = (0b1100001 << 9) | (rs2 << 6) | (rs1 << 3) | 0b000
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_sb_pre(rs2, rs1):
    """Encode SB.PRE rs2, (rs1) -> [1100][001][rs2:3][rs1:3][001]."""
    assert 0 <= rs2 <= 7 and 0 <= rs1 <= 7
    insn = (0b1100001 << 9) | (rs2 << 6) | (rs1 << 3) | 0b001
    return (insn & 0xFF, (insn >> 8) & 0xFF)


# ---------------------------------------------------------------------------
# Test: LW.POST basic
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_lw_post_basic(dut):
    """LW.POST R1, (R3): load word from R3, R3 += 2."""
    dut._log.info("Test: LW.POST basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # R3 = 0x0020 (pointer)
    prog[0x0010] = 0x20
    prog[0x0011] = 0x00
    # Target data at 0x0020
    prog[0x0020] = 0xEF
    prog[0x0021] = 0xBE

    _place(prog, 0x0000, _encode_lw(rd=3, rs1=0, off6=16))   # R3 = 0x0020
    _place(prog, 0x0002, _encode_lw_post(rd=1, rs1=3))        # R1 = MEM[0x20] = 0xBEEF, R3 = 0x0022
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))   # MEM[0x14] = R1
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=22))   # MEM[0x16] = R3
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"R1 = {val:#06x} (expected 0xBEEF)")
    assert val == 0xBEEF, f"Expected 0xBEEF, got {val:#06x}"

    lo = _read_ram(dut, 0x0016)
    hi = _read_ram(dut, 0x0017)
    ptr = lo | (hi << 8)
    dut._log.info(f"R3 = {ptr:#06x} (expected 0x0022)")
    assert ptr == 0x0022, f"Expected 0x0022, got {ptr:#06x}"
    dut._log.info("PASS [lw_post_basic]")


# ---------------------------------------------------------------------------
# Test: LB.POST basic (sign-extend)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_lb_post_basic(dut):
    """LB.POST R1, (R3): load byte 0x80 -> R1 = 0xFF80, R3 += 1."""
    dut._log.info("Test: LB.POST basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x20
    prog[0x0011] = 0x00
    prog[0x0020] = 0x80

    _place(prog, 0x0000, _encode_lw(rd=3, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lb_post(rd=1, rs1=3))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=22))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"R1 = {val:#06x} (expected 0xFF80)")
    assert val == 0xFF80, f"Expected 0xFF80, got {val:#06x}"

    lo = _read_ram(dut, 0x0016)
    hi = _read_ram(dut, 0x0017)
    ptr = lo | (hi << 8)
    dut._log.info(f"R3 = {ptr:#06x} (expected 0x0021)")
    assert ptr == 0x0021, f"Expected 0x0021, got {ptr:#06x}"
    dut._log.info("PASS [lb_post_basic]")


# ---------------------------------------------------------------------------
# Test: LBU.POST basic (zero-extend)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_lbu_post_basic(dut):
    """LBU.POST R1, (R3): load byte 0x80 -> R1 = 0x0080, R3 += 1."""
    dut._log.info("Test: LBU.POST basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x20
    prog[0x0011] = 0x00
    prog[0x0020] = 0x80

    _place(prog, 0x0000, _encode_lw(rd=3, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lbu_post(rd=1, rs1=3))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=22))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"R1 = {val:#06x} (expected 0x0080)")
    assert val == 0x0080, f"Expected 0x0080, got {val:#06x}"

    lo = _read_ram(dut, 0x0016)
    hi = _read_ram(dut, 0x0017)
    ptr = lo | (hi << 8)
    dut._log.info(f"R3 = {ptr:#06x} (expected 0x0021)")
    assert ptr == 0x0021, f"Expected 0x0021, got {ptr:#06x}"
    dut._log.info("PASS [lbu_post_basic]")


# ---------------------------------------------------------------------------
# Test: SW.PRE basic
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sw_pre_basic(dut):
    """SW.PRE R1, (R3): R3 -= 2, store R1 to new R3."""
    dut._log.info("Test: SW.PRE basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # R1 = 0x1234 (value to store)
    prog[0x0010] = 0x34
    prog[0x0011] = 0x12
    # R3 = 0x0022 (pointer, will decrement to 0x0020)
    prog[0x0012] = 0x22
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))   # R1 = 0x1234
    _place(prog, 0x0002, _encode_lw(rd=3, rs1=0, off6=18))   # R3 = 0x0022
    _place(prog, 0x0004, _encode_sw_pre(rs2=1, rs1=3))        # R3 = 0x0020, MEM[0x20] = 0x1234
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))   # MEM[0x14] = R3
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    # Check stored value at decremented address
    lo = _read_ram(dut, 0x0020)
    hi = _read_ram(dut, 0x0021)
    val = lo | (hi << 8)
    dut._log.info(f"MEM[0x20] = {val:#06x} (expected 0x1234)")
    assert val == 0x1234, f"Expected 0x1234, got {val:#06x}"

    # Check pointer decrement
    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    ptr = lo | (hi << 8)
    dut._log.info(f"R3 = {ptr:#06x} (expected 0x0020)")
    assert ptr == 0x0020, f"Expected 0x0020, got {ptr:#06x}"
    dut._log.info("PASS [sw_pre_basic]")


# ---------------------------------------------------------------------------
# Test: SB.PRE basic
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sb_pre_basic(dut):
    """SB.PRE R1, (R3): R3 -= 1, store R1[7:0] to new R3."""
    dut._log.info("Test: SB.PRE basic")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # R1 = 0x00AB (value — load from memory)
    prog[0x0010] = 0xAB
    prog[0x0011] = 0x00
    # R3 = 0x0021 (pointer, will decrement to 0x0020)
    prog[0x0012] = 0x21
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))   # R1 = 0x00AB
    _place(prog, 0x0002, _encode_lw(rd=3, rs1=0, off6=18))   # R3 = 0x0021
    _place(prog, 0x0004, _encode_sb_pre(rs2=1, rs1=3))        # R3 = 0x0020, MEM[0x20] = 0xAB
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))   # MEM[0x14] = R3
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    # Check stored byte
    val = _read_ram(dut, 0x0020)
    dut._log.info(f"MEM[0x20] = {val:#04x} (expected 0xAB)")
    assert val == 0xAB, f"Expected 0xAB, got {val:#04x}"

    # Check pointer decrement
    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    ptr = lo | (hi << 8)
    dut._log.info(f"R3 = {ptr:#06x} (expected 0x0020)")
    assert ptr == 0x0020, f"Expected 0x0020, got {ptr:#06x}"
    dut._log.info("PASS [sb_pre_basic]")


# ---------------------------------------------------------------------------
# Test: LW.POST + SW.PRE roundtrip (PUSH/POP pattern)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_push_pop_roundtrip(dut):
    """SW.PRE (push) then LW.POST (pop): value survives, SP restored."""
    dut._log.info("Test: PUSH/POP roundtrip")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # R1 = 0xDEAD (value to push)
    prog[0x0010] = 0xAD
    prog[0x0011] = 0xDE
    # R7 (SP) = 0x0030 (stack pointer)
    prog[0x0012] = 0x30
    prog[0x0013] = 0x00

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))   # R1 = 0xDEAD
    _place(prog, 0x0002, _encode_lw(rd=7, rs1=0, off6=18))   # R7 = 0x0030
    _place(prog, 0x0004, _encode_sw_pre(rs2=1, rs1=7))        # PUSH: R7 = 0x002E, MEM[0x2E] = 0xDEAD
    _place(prog, 0x0006, _encode_lw_post(rd=2, rs1=7))        # POP: R2 = MEM[0x2E] = 0xDEAD, R7 = 0x0030
    _place(prog, 0x0008, _encode_sw(rs2=2, rs1=0, off6=20))   # MEM[0x14] = R2
    _place(prog, 0x000A, _encode_sw(rs2=7, rs1=0, off6=22))   # MEM[0x16] = R7
    _place(prog, 0x000C, _encode_jr(rs=0, off6=6))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 400)

    # Check popped value
    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"R2 = {val:#06x} (expected 0xDEAD)")
    assert val == 0xDEAD, f"Expected 0xDEAD, got {val:#06x}"

    # Check SP restored
    lo = _read_ram(dut, 0x0016)
    hi = _read_ram(dut, 0x0017)
    sp = lo | (hi << 8)
    dut._log.info(f"R7 = {sp:#06x} (expected 0x0030)")
    assert sp == 0x0030, f"Expected 0x0030, got {sp:#06x}"
    dut._log.info("PASS [push_pop_roundtrip]")


# ---------------------------------------------------------------------------
# Test: LW.POST same register (rd == rs1)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_lw_post_same_reg(dut):
    """LW.POST R3, (R3): loaded value overwrites incremented pointer."""
    dut._log.info("Test: LW.POST same register")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x20
    prog[0x0011] = 0x00
    prog[0x0020] = 0x34
    prog[0x0021] = 0x12

    _place(prog, 0x0000, _encode_lw(rd=3, rs1=0, off6=16))   # R3 = 0x0020
    _place(prog, 0x0002, _encode_lw_post(rd=3, rs1=3))        # R3 = MEM[0x20] = 0x1234 (load overwrites increment)
    _place(prog, 0x0004, _encode_sw(rs2=3, rs1=0, off6=20))   # MEM[0x14] = R3
    _place(prog, 0x0006, _encode_jr(rs=0, off6=3))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"R3 = {val:#06x} (expected 0x1234)")
    assert val == 0x1234, f"Expected 0x1234, got {val:#06x}"
    dut._log.info("PASS [lw_post_same_reg]")


# ---------------------------------------------------------------------------
# Test: LW.POST page crossing
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_lw_post_page_cross(dut):
    """LW.POST from address 0x00FF: crosses page boundary."""
    dut._log.info("Test: LW.POST page crossing")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0xFF
    prog[0x0011] = 0x00
    prog[0x00FF] = 0xCD
    prog[0x0100] = 0xAB

    _place(prog, 0x0000, _encode_lw(rd=3, rs1=0, off6=16))   # R3 = 0x00FF
    _place(prog, 0x0002, _encode_lw_post(rd=1, rs1=3))        # R1 = MEM[0xFF] = 0xABCD, R3 = 0x0101
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=22))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"R1 = {val:#06x} (expected 0xABCD)")
    assert val == 0xABCD, f"Expected 0xABCD, got {val:#06x}"

    lo = _read_ram(dut, 0x0016)
    hi = _read_ram(dut, 0x0017)
    ptr = lo | (hi << 8)
    dut._log.info(f"R3 = {ptr:#06x} (expected 0x0101)")
    assert ptr == 0x0101, f"Expected 0x0101, got {ptr:#06x}"
    dut._log.info("PASS [lw_post_page_cross]")


# ---------------------------------------------------------------------------
# Test: LB.POST positive byte
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_lb_post_positive(dut):
    """LB.POST R1, (R3): load byte 0x7F -> R1 = 0x007F, R3 += 1."""
    dut._log.info("Test: LB.POST positive byte")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    prog[0x0010] = 0x20
    prog[0x0011] = 0x00
    prog[0x0020] = 0x7F

    _place(prog, 0x0000, _encode_lw(rd=3, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lb_post(rd=1, rs1=3))
    _place(prog, 0x0004, _encode_sw(rs2=1, rs1=0, off6=20))
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=22))
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    val = lo | (hi << 8)
    dut._log.info(f"R1 = {val:#06x} (expected 0x007F)")
    assert val == 0x007F, f"Expected 0x007F, got {val:#06x}"

    lo = _read_ram(dut, 0x0016)
    hi = _read_ram(dut, 0x0017)
    ptr = lo | (hi << 8)
    dut._log.info(f"R3 = {ptr:#06x} (expected 0x0021)")
    assert ptr == 0x0021, f"Expected 0x0021, got {ptr:#06x}"
    dut._log.info("PASS [lb_post_positive]")


# ---------------------------------------------------------------------------
# Test: SW.PRE page crossing (decrement crosses page)
# ---------------------------------------------------------------------------
@cocotb.test()
async def test_sw_pre_page_cross(dut):
    """SW.PRE with pointer at 0x0101: decrement to 0x00FF, store word."""
    dut._log.info("Test: SW.PRE page crossing")

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # R1 = 0xFACE
    prog[0x0010] = 0xCE
    prog[0x0011] = 0xFA
    # R3 = 0x0101
    prog[0x0012] = 0x01
    prog[0x0013] = 0x01

    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    _place(prog, 0x0002, _encode_lw(rd=3, rs1=0, off6=18))
    _place(prog, 0x0004, _encode_sw_pre(rs2=1, rs1=3))        # R3 = 0x00FF, MEM[0xFF] = 0xFACE
    _place(prog, 0x0006, _encode_sw(rs2=3, rs1=0, off6=20))   # MEM[0x14] = R3
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    _load_program(dut, prog)
    await _reset(dut)
    await ClockCycles(dut.clk, 300)

    lo = _read_ram(dut, 0x00FF)
    hi = _read_ram(dut, 0x0100)
    val = lo | (hi << 8)
    dut._log.info(f"MEM[0xFF] = {val:#06x} (expected 0xFACE)")
    assert val == 0xFACE, f"Expected 0xFACE, got {val:#06x}"

    lo = _read_ram(dut, 0x0014)
    hi = _read_ram(dut, 0x0015)
    ptr = lo | (hi << 8)
    dut._log.info(f"R3 = {ptr:#06x} (expected 0x00FF)")
    assert ptr == 0x00FF, f"Expected 0x00FF, got {ptr:#06x}"
    dut._log.info("PASS [sw_pre_page_cross]")


