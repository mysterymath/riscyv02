# RISCY-V02 User Manual

RISCY-V02 is a 16-bit RISC processor that is a pin-compatible drop-in replacement for the WDC 65C02. It uses the same 8-bit multiplexed bus protocol, same control signals, and fits in the same Tiny Tapeout 1x2 tile. Different ISA, same socket.

## Current Status

The processor currently implements: **LW**, **SW**, **JR**, **RETI**, **SEI**, and **CLI**. IRQ interrupt handling is supported. All other opcodes are treated as NOPs (2-cycle no-ops that advance the PC).

## Comparison with Arlet 6502

Both designs target the IHP sg13g2 130nm process on a 1x2 Tiny Tapeout tile. The clock speed is pinned to match the 6502 (~62 MHz), simulating 1970s DRAM constraints where raw clock speed improvements don't matter. The comparison focuses on IPC and transistor efficiency.

| Metric | RISCY-V02 | Arlet 6502 |
|---|---|---|
| Clock period | 17 ns | 16 ns |
| fMax (slow corner) | 58.8 MHz | 62.5 MHz |
| Utilization | 42.3% | 48.4% |
| Transistor count (synth) | 10,902 | 13,082 |

RISCY-V02 uses ~17% fewer transistors with room to grow as more instructions are added.

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
- `ui_in[0]` = IRQB (active-low interrupt request)
- `ui_in[2]` = RDY (active-high ready signal)

## Architecture

- **8 general-purpose registers**: R0–R7, each 16 bits wide (3-bit encoding)
- **16-bit program counter** (not directly accessible)
- **16-bit address space**, byte-addressable, little-endian
- **Fixed 16-bit instructions**, fetched low byte first
- **2-stage pipeline**: Fetch and Execute with speculative fetch and redirect

### Reset

On reset:
- PC is set to $0000 and execution begins
- I (interrupt disable) is set to 1 — interrupts are disabled
- All registers are cleared to zero

There is no vector fetch; code is placed directly at address $0000. Software must execute CLI to enable interrupts.

### Interrupts

RISCY-V02 supports maskable IRQ interrupts via the IRQB input (`ui_in[0]`).

**Interrupt entry (when IRQB=0 and I=0):**
1. Complete the current instruction
2. Save EPC = (next_PC | I) — return address with I bit in bit 0
3. Set I = 1 — disable further interrupts
4. Jump to $0004 (IRQ vector)

**Interrupt return (RETI instruction):**
1. Restore I = EPC[0]
2. Jump to EPC & $FFFE

The IRQ vector at $0004 typically contains a trampoline (LW + JR) to reach the actual handler:

```
$0000: <reset code>
$0002: ...
$0004: LW t0, 5(R0)    ; Load handler address from $000A
$0006: JR t0, 0        ; Jump to handler
$0008: <handler_addr>  ; 16-bit address of IRQ handler
```

**Interrupt latency:** 2 cycles from instruction completion to first handler instruction fetch.

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

### RETI — Return from Interrupt

```
[1111111010000001]
```

`I = EPC[0]; PC = EPC & $FFFE`

Restores the interrupt enable state from the saved EPC and returns to the interrupted code. The I bit is restored from EPC bit 0, and PC is set to EPC with bit 0 cleared (ensuring word alignment).

**Cycle count:** 2 (fetch + redirect)

### SEI — Set Interrupt Disable

```
[1111111010000010]
```

`I = 1`

Disables interrupts by setting the I bit. While I=1, IRQB assertions are ignored.

**Cycle count:** 2

### CLI — Clear Interrupt Disable

```
[1111111010000011]
```

`I = 0`

Enables interrupts by clearing the I bit. After CLI, a pending IRQ (IRQB=0) will be taken at the next instruction boundary.

**Cycle count:** 2

### All Other Opcodes

Any instruction not matching the above is executed as a NOP: the PC advances past the instruction in 2 cycles with no other effect.

## Instruction Encoding Reference

```
Bits 15..12  Instruction   Format
──────────────────────────────────────────────
1000         LW            [rs1:3][off6:6][rd:3]
1010         SW            [rs1:3][off6:6][rs2:3]

Bits 15..9   Instruction   Format
──────────────────────────────────────────────
1011100      JR            [off6:6][rs:3]

Full 16-bit  Instruction
──────────────────────────────────────────────
1111111010000001  RETI
1111111010000010  SEI
1111111010000011  CLI

All other    NOP           (ignored)
```

## Pipeline and Timing

The processor uses a 2-stage pipeline (Fetch and Execute) that overlap where possible. Most instructions take **2 cycles**. Loads and stores add **1 cycle for address computation** plus **1 cycle per byte** transferred.

### Cycle Counts (Throughput)

Throughput is measured from one instruction boundary (SYNC) to the next:

| Instruction | Cycles | Notes |
|---|---|---|
| NOP/SEI/CLI | 2 | 1 execute + 1 overlapped fetch |
| LW/SW | 4 | 4 execute (address computation overlaps with fetch) |
| JR | 4 | 2 execute + 2 fetch after redirect |
| RETI | 3 | 1 execute + 2 fetch after redirect |
| IRQ entry | 2 | Redirect at instruction boundary |

Instructions that redirect (JR, RETI) flush the speculative fetch and must wait for new instruction bytes. Non-redirecting instructions benefit from fetch/execute overlap.

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
