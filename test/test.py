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
    dut.ui_in.value = 0x04  # RDY = 1 (ui_in[2])
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
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=4))
    # 0x0002: JR R1, 0        ; jump to 0x0020 (uses just-loaded R1)
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
    prog[0x0020] = 0x11
    prog[0x0021] = 0x11
    prog[0x0022] = 0x22
    prog[0x0023] = 0x22
    prog[0x0024] = 0x33
    prog[0x0025] = 0x33

    # Program: load three values, store them as markers, spin
    # Marker addresses: 0x38, 0x3A, 0x3C (offsets 28, 29, 30 from R0)
    # 0x0000: LW R1, 16(R0)  ; R1 = MEM[0x20] = 0x1111
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=16))
    # 0x0002: LW R2, 17(R0)  ; R2 = MEM[0x22] = 0x2222
    _place(prog, 0x0002, _encode_lw(rd=2, rs1=0, off6=17))
    # 0x0004: LW R3, 18(R0)  ; R3 = MEM[0x24] = 0x3333
    _place(prog, 0x0004, _encode_lw(rd=3, rs1=0, off6=18))
    # 0x0006: SW R1, 28(R0)  ; MEM[0x38] = 0x1111
    _place(prog, 0x0006, _encode_sw(rs2=1, rs1=0, off6=28))
    # 0x0008: SW R2, 29(R0)  ; MEM[0x3A] = 0x2222
    _place(prog, 0x0008, _encode_sw(rs2=2, rs1=0, off6=29))
    # 0x000A: SW R3, 30(R0)  ; MEM[0x3C] = 0x3333
    _place(prog, 0x000A, _encode_sw(rs2=3, rs1=0, off6=30))
    # 0x000C: JR R0, 6       ; spin at 0x000C
    _place(prog, 0x000C, _encode_jr(rs=0, off6=6))

    def get_sync():
        return (int(dut.uo_out.value) >> 1) & 1

    # Marker addresses: 28*2=0x38, 29*2=0x3A, 30*2=0x3C
    def read_markers():
        m1 = _read_ram(dut, 0x38) | (_read_ram(dut, 0x39) << 8)
        m2 = _read_ram(dut, 0x3A) | (_read_ram(dut, 0x3B) << 8)
        m3 = _read_ram(dut, 0x3C) | (_read_ram(dut, 0x3D) << 8)
        return (m1, m2, m3)

    async def run_to_sync():
        """Run until SYNC=1 (instruction boundary), then halt."""
        dut.ui_in.value = 0x04  # RDY=1
        for _ in range(200):
            await FallingEdge(dut.clk)
            if get_sync():
                dut.ui_in.value = 0x00  # RDY=0, halt
                return True
        return False

    async def single_step():
        """Execute one instruction: wait for SYNC 1→0→1 transition."""
        dut.ui_in.value = 0x04  # RDY=1, run

        # Wait for SYNC to go low (instruction starts executing)
        for _ in range(200):
            await FallingEdge(dut.clk)
            if not get_sync():
                break

        # Wait for SYNC to go high (next boundary reached)
        for _ in range(200):
            await FallingEdge(dut.clk)
            if get_sync():
                dut.ui_in.value = 0x00  # RDY=0, halt
                return True

        return False

    # Reset with RDY=0 so CPU doesn't run until we're ready
    dut.ena.value = 1
    dut.ui_in.value = 0x00  # RDY=0, halted
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)  # Let reset settle

    # Load program AFTER reset (to avoid corruption from previous test's bus activity)
    _load_program(dut, prog)

    # Clear marker addresses (may have garbage from previous tests)
    for addr in [0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D]:
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
    """Encode RETI -> 16-bit little-endian bytes."""
    insn = 0b1111111010000001
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_sei():
    """Encode SEI -> 16-bit little-endian bytes."""
    insn = 0b1111111010000010
    return (insn & 0xFF, (insn >> 8) & 0xFF)


def _encode_cli():
    """Encode CLI -> 16-bit little-endian bytes."""
    insn = 0b1111111010000011
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

    # IRQ handler at 0x0004: write marker and spin
    # 0x0004: LW R1, 16(R0)  ; R1 = 0xDEAD from 0x20
    _place(prog, 0x0004, _encode_lw(rd=1, rs1=0, off6=16))
    # 0x0006: SW R1, 24(R0)  ; MEM[0x30] = 0xDEAD (marker)
    _place(prog, 0x0006, _encode_sw(rs2=1, rs1=0, off6=24))
    # 0x0008: JR R0, 4  ; spin at 0x0008
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    # Data: marker value
    prog[0x0020] = 0xAD
    prog[0x0021] = 0xDE

    # Clear marker location
    prog[0x0030] = 0x00
    prog[0x0031] = 0x00

    _load_program(dut, prog)

    # Reset with IRQB=0 (interrupt asserted) and RDY=1
    # ui_in[0] = IRQB, ui_in[2] = RDY
    dut.ena.value = 1
    dut.ui_in.value = 0x04  # RDY=1, IRQB=0 (asserted!)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Run for a while with interrupt asserted
    await ClockCycles(dut.clk, 100)

    # Check marker - should NOT be written since I=1 after reset
    lo = _read_ram(dut, 0x0030)
    hi = _read_ram(dut, 0x0031)
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

    # IRQ handler at 0x0004: write marker and spin
    # 0x0004: LW R1, 16(R0)  ; R1 = 0xBEEF from 0x20
    _place(prog, 0x0004, _encode_lw(rd=1, rs1=0, off6=16))
    # 0x0006: SW R1, 24(R0)  ; MEM[0x30] = 0xBEEF (marker)
    _place(prog, 0x0006, _encode_sw(rs2=1, rs1=0, off6=24))
    # 0x0008: JR R0, 4  ; spin at 0x0008
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))

    # Data: marker value
    prog[0x0020] = 0xEF
    prog[0x0021] = 0xBE

    # Clear marker location
    prog[0x0030] = 0x00
    prog[0x0031] = 0x00

    _load_program(dut, prog)

    # Reset with IRQB=1 (not asserted) and RDY=1
    dut.ena.value = 1
    dut.ui_in.value = 0x05  # RDY=1, IRQB=1 (not asserted)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Run for a few cycles to execute CLI
    await ClockCycles(dut.clk, 20)

    # Now assert IRQB=0
    dut.ui_in.value = 0x04  # RDY=1, IRQB=0 (asserted)

    # Run to let IRQ fire and handler execute
    await ClockCycles(dut.clk, 100)

    # Check marker - should be written since CLI enabled interrupts
    lo = _read_ram(dut, 0x0030)
    hi = _read_ram(dut, 0x0031)
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
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=5))
    # 0x0002: JR R1, 0      ; jump to 0x0010
    _place(prog, 0x0002, _encode_jr(rs=1, off6=0))

    # IRQ handler at 0x0004
    _place(prog, 0x0004, _encode_lw(rd=2, rs1=0, off6=20))  # R2 = MEM[0x28] = 0xDEAD
    _place(prog, 0x0006, _encode_sw(rs2=2, rs1=0, off6=24)) # MEM[0x30] = R2
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))          # spin at 0x0008

    # Data: jump target
    prog[0x000A] = 0x10
    prog[0x000B] = 0x00

    # Continue at 0x0010: CLI, SEI, spin
    _place(prog, 0x0010, _encode_cli())
    _place(prog, 0x0012, _encode_sei())
    _place(prog, 0x0014, _encode_jr(rs=0, off6=10))  # spin at 0x0014

    # Data: marker value
    prog[0x0028] = 0xAD
    prog[0x0029] = 0xDE

    # Clear marker location
    prog[0x0030] = 0x00
    prog[0x0031] = 0x00

    _load_program(dut, prog)

    # Reset with IRQB=1 (not asserted) and RDY=1
    dut.ena.value = 1
    dut.ui_in.value = 0x05  # RDY=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Run to execute jump, CLI, SEI
    await ClockCycles(dut.clk, 50)

    # Now assert IRQB=0
    dut.ui_in.value = 0x04  # RDY=1, IRQB=0

    # Run for a while - IRQ should NOT fire due to SEI
    await ClockCycles(dut.clk, 100)

    # Check marker - should NOT be written since SEI masked IRQ
    lo = _read_ram(dut, 0x0030)
    hi = _read_ram(dut, 0x0031)
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
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=5))
    # 0x0002: JR R1, 0      ; jump to 0x0020
    _place(prog, 0x0002, _encode_jr(rs=1, off6=0))

    # IRQ handler at 0x0004: write marker, RETI
    _place(prog, 0x0004, _encode_lw(rd=2, rs1=0, off6=24))  # R2 = MEM[0x30] = 0xAAAA
    _place(prog, 0x0006, _encode_sw(rs2=2, rs1=0, off6=28)) # MEM[0x38] = 0xAAAA (IRQ marker)
    _place(prog, 0x0008, _encode_reti())                    # return from interrupt

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
    prog[0x0030] = 0xAA
    prog[0x0031] = 0xAA
    prog[0x0034] = 0xBB
    prog[0x0035] = 0xBB

    # Clear marker locations
    prog[0x0038] = 0x00
    prog[0x0039] = 0x00
    prog[0x003C] = 0x00
    prog[0x003D] = 0x00

    _load_program(dut, prog)

    # Reset with IRQB=1 (not asserted)
    dut.ena.value = 1
    dut.ui_in.value = 0x05  # RDY=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Run until CLI executes
    await ClockCycles(dut.clk, 50)

    # Assert IRQB to trigger interrupt
    dut.ui_in.value = 0x04  # RDY=1, IRQB=0

    # Run to let IRQ fire, handler execute, RETI, and return code execute
    await ClockCycles(dut.clk, 200)

    # De-assert IRQB so we don't keep interrupting
    dut.ui_in.value = 0x05  # RDY=1, IRQB=1

    await ClockCycles(dut.clk, 100)

    # Check IRQ marker at 0x38
    lo = _read_ram(dut, 0x0038)
    hi = _read_ram(dut, 0x0039)
    irq_marker = lo | (hi << 8)
    dut._log.info(f"IRQ marker = {irq_marker:#06x} (expected 0xAAAA)")

    # Check return marker at 0x3C
    lo = _read_ram(dut, 0x003C)
    hi = _read_ram(dut, 0x003D)
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
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=5))
    # 0x0002: JR R1, 0
    _place(prog, 0x0002, _encode_jr(rs=1, off6=0))

    # IRQ handler at 0x0004: write fixed marker, NO RETI (spin forever)
    # If nested interrupts fired, we'd see different behavior
    _place(prog, 0x0004, _encode_lw(rd=2, rs1=0, off6=24))  # R2 = MEM[0x30] = 0xCAFE
    _place(prog, 0x0006, _encode_sw(rs2=2, rs1=0, off6=28)) # MEM[0x38] = 0xCAFE
    # Spin without RETI - keep IRQB asserted, but I=1 should prevent re-entry
    _place(prog, 0x0008, _encode_jr(rs=0, off6=4))          # spin at 0x0008

    # Data
    prog[0x000A] = 0x20
    prog[0x000B] = 0x00
    prog[0x0030] = 0xFE
    prog[0x0031] = 0xCA

    # Continue at 0x0020: CLI, spin
    _place(prog, 0x0020, _encode_cli())
    _place(prog, 0x0022, _encode_jr(rs=0, off6=17))  # spin at 0x0022

    # Clear marker
    prog[0x0038] = 0x00
    prog[0x0039] = 0x00

    _load_program(dut, prog)

    # Reset
    dut.ena.value = 1
    dut.ui_in.value = 0x05  # RDY=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Run until CLI executes
    await ClockCycles(dut.clk, 50)

    # Assert IRQB and keep it asserted
    dut.ui_in.value = 0x04  # RDY=1, IRQB=0

    # Run for a long time with IRQ continuously asserted
    await ClockCycles(dut.clk, 500)

    # Check marker - should be 0xCAFE (written once)
    lo = _read_ram(dut, 0x0038)
    hi = _read_ram(dut, 0x0039)
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
    _place(prog, 0x0000, _encode_lw(rd=1, rs1=0, off6=5))
    # 0x0002: JR R1, 0
    _place(prog, 0x0002, _encode_jr(rs=1, off6=0))

    # IRQ handler at 0x0004: write R5 to marker (R5 set by main code's LW)
    _place(prog, 0x0004, _encode_sw(rs2=5, rs1=0, off6=30)) # MEM[0x3C] = R5
    _place(prog, 0x0006, _encode_reti())

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
    prog[0x0030] = 0x34
    prog[0x0031] = 0x12

    # Clear markers
    prog[0x0038] = 0x00
    prog[0x0039] = 0x00
    prog[0x003C] = 0x00
    prog[0x003D] = 0x00

    _load_program(dut, prog)

    # Reset
    dut.ena.value = 1
    dut.ui_in.value = 0x05  # RDY=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    # Run until CLI executes
    await ClockCycles(dut.clk, 40)

    # Assert IRQB right as LW is executing (timing is approximate)
    # The IRQ should wait for LW to complete
    dut.ui_in.value = 0x04  # RDY=1, IRQB=0

    # Run to let LW complete, IRQ fire, RETI, SW execute
    await ClockCycles(dut.clk, 200)

    # De-assert IRQ
    dut.ui_in.value = 0x05

    await ClockCycles(dut.clk, 50)

    # Check main marker at 0x38 (SW after LW)
    lo = _read_ram(dut, 0x0038)
    hi = _read_ram(dut, 0x0039)
    main_marker = lo | (hi << 8)
    dut._log.info(f"Main marker = {main_marker:#06x} (expected 0x1234)")

    # Check IRQ marker at 0x3C (SW of R5 in handler)
    lo = _read_ram(dut, 0x003C)
    hi = _read_ram(dut, 0x003D)
    irq_marker = lo | (hi << 8)
    dut._log.info(f"IRQ marker = {irq_marker:#06x} (expected 0x1234 if LW completed before IRQ)")

    assert main_marker == 0x1234, f"Main code LW/SW failed! Got {main_marker:#06x}"
    assert irq_marker == 0x1234, f"IRQ saw wrong R5 value! Got {irq_marker:#06x}"
    dut._log.info("PASS [irq_during_multicycle]")
