# SPDX-FileCopyrightText: © 2024 mysterymath
# SPDX-License-Identifier: Apache-2.0
#
# Differential fuzz testing for RISCY-V02.
#
# Runs random programs on both the RTL and a behavioral emulator,
# comparing all output pins at every clock edge.

import itertools
import os
import random
import time
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, FallingEdge

from riscyv02_sim import RISCYV02Sim


TRACE_DEPTH = 500  # Number of entries to keep in the circular buffer


def _safe_int(value):
    """Convert cocotb LogicArray to int, treating X/Z as 0."""
    try:
        return int(value)
    except ValueError:
        return 0


def _gen_inputs(rng, n_cycles):
    """Generate randomized control inputs for each cycle.

    Returns list of ui_in values. Bits: [2]=RDY, [1]=NMIB, [0]=IRQB.
    IRQB/NMIB are active-low.
    """
    inputs = []
    irqb = 1        # inactive
    nmib = 1        # inactive
    rdy = 1         # active

    irq_counter = 0     # cycles remaining for IRQ assertion
    rdy_counter = 0     # cycles remaining for RDY deassert
    nmi_cooldown = 0    # cooldown between NMI assertions

    for _ in range(n_cycles):
        # IRQB: ~3% toggle rate, stays asserted 5-20 cycles
        if irq_counter > 0:
            irq_counter -= 1
            if irq_counter == 0:
                irqb = 1
        elif rng.random() < 0.03:
            irqb = 0
            irq_counter = rng.randint(5, 20)

        # NMIB: ~0.5% assert with 30-cycle cooldown
        if nmi_cooldown > 0:
            nmi_cooldown -= 1
            nmib = 1
        elif rng.random() < 0.005:
            nmib = 0
            nmi_cooldown = 30
        else:
            nmib = 1

        # RDY: ~2% deassert, hold 1-5 cycles
        if rdy_counter > 0:
            rdy_counter -= 1
            if rdy_counter == 0:
                rdy = 1
        elif rng.random() < 0.02:
            rdy = 0
            rdy_counter = rng.randint(1, 5)

        inputs.append((rdy << 2) | (nmib << 1) | irqb)

    return inputs


def _snap_sim(sim, include_regs=False):
    """Capture SIM state for the trace buffer."""
    s = (
        f"SIM[addr=0x{sim.current_addr:04X} sync={sim.current_sync}"
        f" rwb={sim.current_rwb} idx={sim._bus_idx}/{len(sim._bus_seq)}"
        f" ipt={sim._interrupt_point} pc=0x{sim.pc:04X}]"
        f" nmi[pend={sim.nmi_pending} ack={sim.nmi_ack}]"
    )
    if include_regs:
        s += f" regs={['%04X' % r for r in sim.regs]}"
    return s


def _dump_trace(dut, trace_buf):
    """Dump the circular trace buffer on mismatch."""
    dut._log.error("=== Trace buffer (last %d cycles) ===", len(trace_buf))
    for entry in trace_buf:
        dut._log.error(entry)
    dut._log.error("=== End trace ===")


@cocotb.test()
async def test_fuzz(dut):
    seed = int(os.environ.get('FUZZ_SEED', '0'))
    n_cycles = int(os.environ.get('FUZZ_CYCLES', '500'))
    n_iters = int(os.environ.get('FUZZ_ITERS', '200'))
    skip_cycles = int(os.environ.get('FUZZ_SKIP', '0'))

    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    total_mismatches = 0
    total_failures = 0
    start_time = time.monotonic()
    counter = itertools.count() if n_iters == 0 else range(n_iters)

    for iteration in counter:
        iter_seed = seed + iteration
        rng = random.Random(iter_seed)

        # Generate random 64K RAM
        ram = bytearray(rng.getrandbits(8) for _ in range(65536))

        # Create emulator
        sim = RISCYV02Sim(bytearray(ram))

        # Load RAM into testbench
        for i in range(65536):
            dut.ram[i].value = ram[i]

        # Reset
        dut.ena.value = 1
        dut.ui_in.value = 0x07  # RDY=1, NMIB=1, IRQB=1
        dut.rst_n.value = 0
        await ClockCycles(dut.clk, 20)
        await FallingEdge(dut.clk)
        dut.rst_n.value = 1
        await FallingEdge(dut.clk)  # Let first post-reset negedge settle

        # Generate input sequence
        inputs = _gen_inputs(rng, n_cycles)

        mismatches = 0
        max_mismatches = 5
        trace_buf = deque(maxlen=TRACE_DEPTH)
        trace_dumped = False

        for cycle in range(n_cycles):
            dut.ui_in.value = inputs[cycle]

            await RisingEdge(dut.clk)

            if cycle >= skip_cycles:
                sim_uo, sim_uio, sim_oe = sim.posedge_outputs()
                rtl_uo = _safe_int(dut.uo_out.value)
                rtl_uio = _safe_int(dut.uio_out.value)
                rtl_oe = _safe_int(dut.uio_oe.value)

                rtl_ab = (rtl_uio << 8) | rtl_uo
                sim_ab = (sim_uio << 8) | sim_uo
                trace_buf.append(
                    f"c{cycle} posedge: RTL_AB=0x{rtl_ab:04X}"
                    f" SIM_AB=0x{sim_ab:04X}")

                mismatch_this_edge = False
                for sig, rv, sv in [("uio_oe", rtl_oe, sim_oe),
                                    ("uo_out(AB_lo)", rtl_uo, sim_uo),
                                    ("uio_out(AB_hi)", rtl_uio, sim_uio)]:
                    if rv != sv:
                        if not trace_dumped:
                            _dump_trace(dut, trace_buf)
                            trace_dumped = True
                        dut._log.error(
                            f"MISMATCH c{cycle} posedge {sig}:"
                            f" RTL=0x{rv:02X} SIM=0x{sv:02X}"
                            f"  pc=0x{sim.pc:04X} i_bit={sim.i_bit}"
                            f" idx={sim._bus_idx}/{len(sim._bus_seq)}")
                        mismatches += 1
                        mismatch_this_edge = True

            await FallingEdge(dut.clk)

            if cycle >= skip_cycles:
                sim_uo, sim_uio, sim_oe = sim.negedge_outputs()
                rtl_uo = _safe_int(dut.uo_out.value)
                rtl_oe = _safe_int(dut.uio_oe.value)

                rtl_rwb = rtl_uo & 1
                rtl_sync = (rtl_uo >> 1) & 1
                sim_rwb = sim_uo & 1
                sim_sync = (sim_uo >> 1) & 1

                for sig, rv, sv in [("uio_oe", rtl_oe, sim_oe),
                                    ("uo_out({SYNC,RWB})", rtl_uo, sim_uo)]:
                    if rv != sv:
                        if not trace_dumped:
                            _dump_trace(dut, trace_buf)
                            trace_dumped = True
                        dut._log.error(
                            f"MISMATCH c{cycle} negedge {sig}:"
                            f" RTL=0x{rv:02X} SIM=0x{sv:02X}"
                            f"  pc=0x{sim.pc:04X} i_bit={sim.i_bit}"
                            f" idx={sim._bus_idx}/{len(sim._bus_seq)}")
                        mismatches += 1

                if sim_oe == 0xFF and rtl_oe == 0xFF:
                    rtl_uio = _safe_int(dut.uio_out.value)
                    if rtl_uio != sim_uio:
                        if not trace_dumped:
                            _dump_trace(dut, trace_buf)
                            trace_dumped = True
                        dut._log.error(
                            f"MISMATCH c{cycle} negedge uio_out(DO):"
                            f" RTL=0x{rtl_uio:02X} SIM=0x{sim_uio:02X}"
                            f"  pc=0x{sim.pc:04X} i_bit={sim.i_bit}"
                            f" idx={sim._bus_idx}/{len(sim._bus_seq)}")
                        mismatches += 1

            # Advance emulator AFTER comparison
            irqb = inputs[cycle] & 1
            nmib = (inputs[cycle] >> 1) & 1
            rdy_bit = (inputs[cycle] >> 2) & 1

            trace_buf.append(
                f"c{cycle} pre-tick:"
                f" {_snap_sim(sim)}"
                f" in[irqb={irqb} nmib={nmib} rdy={rdy_bit}]")

            sim.tick(bool(irqb), bool(nmib), bool(rdy_bit))

            post = f"c{cycle} post-tick: {_snap_sim(sim)}"
            if sim._bus_idx == 1:  # Just dispatched
                post += f" | {sim.last_dispatch}"
            trace_buf.append(post)

            if mismatches >= max_mismatches:
                dut._log.error(f"Iteration {iteration} (seed {iter_seed}):"
                               f" {mismatches} mismatches, stopping early")
                break

        if mismatches > 0:
            total_mismatches += mismatches
            total_failures += 1
            dut._log.error(f"Iteration {iteration} (seed {iter_seed}):"
                           f" {mismatches} mismatches in {cycle + 1} cycles")
        elif n_iters > 0:
            dut._log.info(f"Iteration {iteration} (seed {iter_seed}):"
                          f" PASS ({n_cycles} cycles)")

        # Progress summary every 1000 iterations (always, for long runs)
        done = iteration + 1
        if done % 1000 == 0:
            elapsed = time.monotonic() - start_time
            rate = done / elapsed if elapsed > 0 else 0
            dut._log.info(
                f"Progress: {done} seeds tested, {total_failures} failures,"
                f" {rate:.1f} seeds/sec")

    if n_iters > 0:
        assert total_mismatches == 0, f"Total mismatches: {total_mismatches}"
