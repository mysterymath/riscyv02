# SPDX-FileCopyrightText: © 2024 mysterymath
# SPDX-License-Identifier: Apache-2.0
#
# Cycle count tests: verify instruction throughput.

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge
from test_helpers import *


@cocotb.test()
async def test_cycle_count_nop(dut):
    """NOP (ADDI R0, 0) takes 2 cycles."""
    prog = {}
    _place(prog, 0x0000, _encode_nop())
    _place(prog, 0x0002, _spin(0x0002))
    await _measure_instruction_cycles(dut, prog, 2, "NOP")


@cocotb.test()
async def test_cycle_count_lw(dut):
    """LW takes 4 cycles throughput."""
    prog = {}
    _place(prog, 0x0000, _encode_lw(rs=7, imm=0x30))
    _place(prog, 0x0002, _spin(0x0002))
    await _measure_instruction_cycles(dut, prog, 4, "LW")


@cocotb.test()
async def test_cycle_count_sw(dut):
    """SW takes 4 cycles throughput."""
    prog = {}
    _place(prog, 0x0000, _encode_sw(rs=7, imm=0x30))
    _place(prog, 0x0002, _spin(0x0002))
    await _measure_instruction_cycles(dut, prog, 4, "SW")


@cocotb.test()
async def test_cycle_count_jr(dut):
    """JR takes 4 cycles."""
    prog = {}
    # JR R7, 0 → PC = 0+0 = 0x0000 (spin at self)
    _place(prog, 0x0000, _encode_jr(rs=7, imm=0))
    await _measure_instruction_cycles(dut, prog, 4, "JR")


@cocotb.test()
async def test_cycle_count_sei(dut):
    """SEI takes 2 cycles."""
    prog = {}
    _place(prog, 0x0000, _encode_sei())
    _place(prog, 0x0002, _spin(0x0002))
    await _measure_instruction_cycles(dut, prog, 2, "SEI")


@cocotb.test()
async def test_cycle_count_cli(dut):
    """CLI takes 2 cycles."""
    prog = {}
    _place(prog, 0x0000, _encode_cli())
    _place(prog, 0x0002, _spin(0x0002))
    # Must use IRQB=1 to avoid IRQ firing after CLI clears I
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    _load_program(dut, prog)
    dut.ena.value = 1
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1
    # Measure from first SYNC to next
    def get_sync():
        return (int(dut.uo_out.value) >> 1) & 1
    for _ in range(200):
        await FallingEdge(dut.clk)
        if get_sync():
            break
    cycles = 0
    for _ in range(200):
        await FallingEdge(dut.clk)
        cycles += 1
        if not get_sync():
            break
    for _ in range(200):
        await FallingEdge(dut.clk)
        cycles += 1
        if get_sync():
            break
    dut._log.info(f"CLI: {cycles} cycles (expected 2)")
    assert cycles == 2, f"CLI: expected 2 cycles, got {cycles}"


@cocotb.test()
async def test_cycle_count_li(dut):
    """LI takes 2 cycles (no memory phase, fetch overlaps execute)."""
    prog = {}
    _place(prog, 0x0000, _encode_li(rd=1, imm=42))
    _place(prog, 0x0002, _spin(0x0002))
    await _measure_instruction_cycles(dut, prog, 2, "LI")


@cocotb.test()
async def test_cycle_count_add(dut):
    """ADD takes 2 cycles (no memory phase)."""
    prog = {}
    _place(prog, 0x0000, _encode_add(rd=1, rs1=2, rs2=3))
    _place(prog, 0x0002, _spin(0x0002))
    await _measure_instruction_cycles(dut, prog, 2, "ADD")


@cocotb.test()
async def test_cycle_count_lb(dut):
    """LB takes 3 cycles (byte load completes at E_MEM_LO)."""
    prog = {}
    prog[0x0030] = 0x42
    _place(prog, 0x0000, _encode_lb(rs=7, imm=0x30))
    _place(prog, 0x0002, _spin(0x0002))
    await _measure_instruction_cycles(dut, prog, 3, "LB")


@cocotb.test()
async def test_cycle_count_sb(dut):
    """SB takes 3 cycles (1 memory byte)."""
    prog = {}
    _place(prog, 0x0000, _encode_sb(rs=7, imm=0x30))
    _place(prog, 0x0002, _spin(0x0002))
    await _measure_instruction_cycles(dut, prog, 3, "SB")


@cocotb.test()
async def test_cycle_count_addi(dut):
    """ADDI takes 2 cycles (no memory phase)."""
    prog = {}
    _place(prog, 0x0000, _encode_addi(rd=1, imm=5))
    _place(prog, 0x0002, _spin(0x0002))
    await _measure_instruction_cycles(dut, prog, 2, "ADDI")


@cocotb.test()
async def test_cycle_count_auipc(dut):
    """AUIPC takes 2 cycles (no memory phase)."""
    prog = {}
    _place(prog, 0x0000, _encode_auipc(rd=1, imm7=1))
    _place(prog, 0x0002, _spin(0x0002))
    await _measure_instruction_cycles(dut, prog, 2, "AUIPC")


@cocotb.test()
async def test_cycle_count_branch_taken(dut):
    """BZ taken takes 4 cycles."""
    prog = {}
    # R0 = 0 from reset, BZ R0 is always taken
    _place(prog, 0x0000, _encode_bz(rs=0, imm=1))  # jump to 0x0004+2=0x0006
    _place(prog, 0x0006, _spin(0x0006))
    await _measure_instruction_cycles(dut, prog, 4, "BZ taken")


@cocotb.test()
async def test_cycle_count_reti(dut):
    """RETI takes 4 cycles."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    # RETI at 0x0000 returns to EPC (0x0000 after reset) -> infinite loop
    _place(prog, 0x0000, _encode_reti())

    def get_sync():
        return (int(dut.uo_out.value) >> 1) & 1

    _load_program(dut, prog)
    # Custom reset with IRQB=1 so RETI restoring I=0 doesn't trigger IRQ
    dut.ena.value = 1
    dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    for _ in range(200):
        await FallingEdge(dut.clk)
        if get_sync():
            break
    cycles = 0
    for _ in range(200):
        await FallingEdge(dut.clk)
        cycles += 1
        if not get_sync():
            break
    for _ in range(200):
        await FallingEdge(dut.clk)
        cycles += 1
        if get_sync():
            break

    dut._log.info(f"RETI: {cycles} cycles (expected 4)")
    assert cycles == 4, f"RETI: expected 4 cycles, got {cycles}"


@cocotb.test()
async def test_cycle_count_wai(dut):
    """WAI with pending masked IRQ takes 3 cycles (wakes immediately)."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_wai())
    _place(prog, 0x0002, _spin(0x0002))

    def get_sync():
        return (int(dut.uo_out.value) >> 1) & 1

    _load_program(dut, prog)
    # Reset with IRQB=0 (pending but masked by I=1)
    dut.ena.value = 1
    dut.ui_in.value = 0x06  # RDY=1, NMIB=1, IRQB=0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    for _ in range(100):
        await FallingEdge(dut.clk)
        if get_sync():
            break

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

    dut._log.info(f"WAI: {cycles} cycles (expected 3)")
    assert cycles == 3, f"WAI: expected 3 cycles, got {cycles}"


@cocotb.test()
async def test_cycle_count_stp(dut):
    """STP takes 1 cycle to halt."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_stp())

    def get_sync():
        return (int(dut.uo_out.value) >> 1) & 1

    _load_program(dut, prog)
    dut.ena.value = 1
    dut.ui_in.value = 0x07
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    for _ in range(100):
        await FallingEdge(dut.clk)
        if get_sync():
            break

    cycles = 0
    for _ in range(100):
        await FallingEdge(dut.clk)
        cycles += 1
        if not get_sync():
            break

    dut._log.info(f"STP: {cycles} cycle(s) to halt (expected 1)")
    assert cycles == 1, f"STP: expected 1 cycle, got {cycles}"

    for _ in range(50):
        await FallingEdge(dut.clk)
        assert not get_sync(), "SYNC high after STP -- CPU not halted!"


@cocotb.test()
async def test_cycle_count_brk(dut):
    """BRK takes 4 cycles."""
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    prog = {}
    _place(prog, 0x0000, _encode_brk())
    # BRK handler at 0x0004: spin
    _place(prog, 0x0004, _spin(0x0004))

    def get_sync():
        return (int(dut.uo_out.value) >> 1) & 1

    _load_program(dut, prog)
    dut.ena.value = 1
    dut.ui_in.value = 0x07
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 20)
    dut.rst_n.value = 1

    for _ in range(100):
        await FallingEdge(dut.clk)
        if get_sync():
            break

    cycles = 0
    for _ in range(20):
        await FallingEdge(dut.clk)
        cycles += 1
        if get_sync():
            break

    dut._log.info(f"BRK: {cycles} cycles (expected 4)")
    assert cycles == 4, f"BRK: expected 4 cycles, got {cycles}"


@cocotb.test()
async def test_cycle_count_lbu(dut):
    """LBU takes 3 cycles (byte load completes at E_MEM_LO)."""
    prog = {}
    _place(prog, 0x0000, _encode_lbu(rs=7, imm=0))
    _place(prog, 0x0002, _spin(0x0002))
    await _measure_instruction_cycles(dut, prog, 3, "LBU")
