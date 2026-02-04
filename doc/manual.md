# RISCY-V02 User Manual

RISCY-V02 is a 16-bit RISC processor that is a pin-compatible drop-in replacement for the WDC 65C02. It uses the same 8-bit multiplexed bus protocol, same control signals, and fits in the same Tiny Tapeout 1x2 tile. Different ISA, same socket.

## Current Status

The processor currently implements a minimal Turing-complete instruction subset: **LW**, **SW**, and **JR**. All other opcodes are treated as NOPs (2-cycle no-ops that advance the PC).

## Comparison with Arlet 6502

Both designs target the IHP sg13g2 130nm process on a 1x2 Tiny Tapeout tile.

| Metric | RISCY-V02 | Arlet 6502 |
|---|---|---|
| Best clock period | 12 ns | 16 ns |
| fMax (all corners) | 83.3 MHz | 62.5 MHz |
| Utilization | 38.2% | 48.4% |
| Transistor count (synth) | 10,236 | 13,082 |

RISCY-V02 is 33% faster and uses ~23% fewer transistors with room to grow.

## Bus Protocol

RISCY-V02 uses the same TT mux/demux bus protocol as the Arlet 6502 wrapper. The active clock edge alternates between address output and data transfer using a dual-edge mux select signal.

**mux_sel = 0 (address phase):**
- `uo_out[7:0]` = AB[7:0]
- `uio_out[7:0]` = AB[15:8] (all output)

**mux_sel = 1 (data phase):**
- `uo_out[0]` = RWB (1 = read, 0 = write)
- `uo_out[1]` = SYNC (1 = at instruction boundary)
- `uo_out[7:2]` = 0
- `uio[7:0]` = D[7:0] (bidirectional; output during writes, input during reads)

**Control inputs:**
- `ui_in[2]` = RDY (active-high ready signal)

## Architecture

- **8 general-purpose registers**: R0–R7, each 16 bits wide (3-bit encoding)
- **16-bit program counter** (not directly accessible)
- **16-bit address space**, byte-addressable, little-endian
- **Fixed 16-bit instructions**, fetched low byte first
- **2-stage pipeline**: Fetch and Execute with speculative fetch and redirect

### Reset

On reset, PC is set to $0000 and execution begins. There is no vector fetch; code is placed directly at address $0000.

### Register Naming Convention

| Register | Name | Suggested Purpose |
|---|---|---|
| R0 | a0 | Argument / return value 0 |
| R1 | a1 | Argument / return value 1 |
| R2 | t0 | Temporary 0 |
| R3 | t1 | Temporary 1 |
| R4 | s0 | Saved register 0 |
| R5 | s1 | Saved register 1 |
| R6 | fp | Frame pointer (or s2) |
| R7 | sp | Stack pointer |

## Instruction Set

All instructions are 16 bits. Opcodes occupy the upper bits and determine the interpretation of all remaining bits.

### LW — Load Word

```
[1000][rs1:3][off6:6][rd:3]
```

`rd = MEM[rs1 + sext(off6) * 2]`

Loads a 16-bit word from memory. The 6-bit signed offset is scaled by 2, giving a range of ±64 bytes from the base register. The memory address must be word-aligned (bit 0 = 0). The low byte is read first, then the high byte.

**Cycle count:** 5 (2 base + 1 address + 2 bytes read)

### SW — Store Word

```
[1010][rs1:3][off6:6][rs2:3]
```

`MEM[rs1 + sext(off6) * 2] = rs2`

Stores a 16-bit word to memory. The offset encoding is identical to LW. The low byte is written first, then the high byte.

**Cycle count:** 5 (2 base + 1 address + 2 bytes written)

### JR — Jump Register

```
[1011100][off6:6][rs:3]
```

`PC = rs + sext(off6) * 2`

Unconditional jump to the address computed from a register plus a scaled signed offset. The 6-bit offset is scaled by 2, giving a range of ±64 bytes from the register value.

**Cycle count:** 4 (2 fetch + 2 address computation in execute)

### All Other Opcodes

Any instruction not matching LW, SW, or JR is executed as a NOP: the PC advances past the instruction in 2 cycles with no other effect.

## Instruction Encoding Reference

```
Bits 15..12  Instruction   Format
──────────────────────────────────────────────
1000         LW            [rs1:3][off6:6][rd:3]
1010         SW            [rs1:3][off6:6][rs2:3]

Bits 15..9   Instruction   Format
──────────────────────────────────────────────
1011100      JR            [off6:6][rs:3]

All other    NOP           (ignored)
```

## Pipeline and Timing

The processor uses a 2-stage pipeline (Fetch and Execute) that overlap where possible. Most instructions take **2 cycles**. Loads and stores add **1 cycle for address computation** plus **1 cycle per byte** transferred.

### Cycle Counts

| Instruction | Cycles | Notes |
|---|---|---|
| Most instructions | 2 | Base cost (fetch only) |
| LW | 5 | 2 fetch + 2 address + 1 byte read |
| SW | 5 | 2 fetch + 2 address + 1 byte written |
| JR | 4 | 2 fetch + 2 address computation |

**Throughput note:** In pipelined execution, LW/SW achieve 4-cycle throughput because address computation overlaps with the next instruction's fetch. JR flushes the speculative fetch and redirects to the computed target.

### Control Flow Handling

JR is processed by the execute stage, which computes the target address using the ALU. While execute computes the target, fetch speculatively continues from the sequential PC. When execute completes the JR, it redirects fetch to the correct address, discarding the speculative fetch. This simplifies the architecture by keeping all register access in execute.

## RDY and SYNC Signals

RISCY-V02 provides W65C02S-compatible RDY and SYNC signals for wait-state insertion, DMA, and single-step debugging.

### RDY (Ready Input)

`ui_in[2]` is the active-high ready signal. When RDY is low, the processor halts:

- All CPU state freezes atomically (PC, registers, pipeline state, ALU carry)
- Bus outputs remain stable (address and data held constant)
- Bus protocol timing continues (mux_sel keeps toggling)
- Processor resumes on the next clock edge after RDY returns high

RDY halts the processor on both read and write cycles, matching W65C02S behavior.

### SYNC (Instruction Boundary Output)

`uo_out[1]` during the data phase indicates an instruction boundary:

- SYNC = 1 one cycle after execute accepts a new instruction
- SYNC = 0 during multi-cycle operations or when no instruction was accepted

When SYNC goes high, the previous instruction has retired and a new instruction has started execution. This corresponds to the 6502 semantics where SYNC indicates opcode fetch.

### Single-Step Protocol

To single-step at instruction boundaries:

1. CPU runs normally with RDY high
2. Monitor SYNC during data phases — when SYNC = 1, an instruction boundary is reached
3. Pull RDY low to halt at that boundary
4. Examine bus state while halted (address shows current fetch address)
5. Pulse RDY high for one clock cycle — CPU advances one instruction
6. SYNC goes high again at the next boundary; pull RDY low to halt
7. Repeat from step 4

### Wait-State Protocol

For slow memory or DMA:

1. External logic decodes address during address phase (mux_sel = 0)
2. If access requires wait states, pull RDY low before the clock edge
3. Memory completes access and drives data
4. Pull RDY high — processor continues on next clock edge

## Input Timing

All inputs (`ui_in`, `uio_in` during reads) have a 4ns setup requirement before the capturing clock edge. This applies to:

- **RDY** (`ui_in[2]`): must be stable 4ns before posedge clk
- **Data bus** (`uio_in`): must be stable 4ns before negedge clk (data phase capture)

Outputs are valid 4ns after their launching clock edge, providing 4ns of margin for external combinational logic in feedback paths.
