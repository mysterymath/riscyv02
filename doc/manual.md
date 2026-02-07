# RISCY-V02 User Manual

RISCY-V02 is a 16-bit RISC processor that is a pin-compatible drop-in replacement for the WDC 65C02. It uses the same 8-bit multiplexed bus protocol, same control signals, and fits in the same Tiny Tapeout 1x2 tile. Different ISA, same socket.

## Current Status

The processor currently implements: **LW**, **SW**, **LB**, **LBU**, **SB**, **JR**, **AUIPC**, **RETI**, **SEI**, **CLI**, **BRK**, **WAI**, **STP**, **EPCR**, and **EPCW**. IRQ and NMI interrupt handling is supported. All other opcodes are treated as NOPs (2-cycle no-ops that advance the PC).

## Comparison with Arlet 6502

Both designs target the IHP sg13g2 130nm process on a 1x2 Tiny Tapeout tile. The clock speed is pinned to match the 6502 (~62 MHz), simulating 1970s DRAM constraints where raw clock speed improvements don't matter. The comparison focuses on IPC and transistor efficiency.

| Metric | RISCY-V02 | Arlet 6502 |
|---|---|---|
| Clock period | 18 ns | 16 ns |
| fMax (slow corner) | 55.6 MHz | 62.5 MHz |
| Utilization | 45.9% | 48.4% |
| Transistor count (synth) | 12,172 | 13,082 |

RISCY-V02 is now below the 6502 in transistor count, with room to grow as more instructions are added.

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
- `ui_in[0]` = IRQB (active-low interrupt request, level-sensitive)
- `ui_in[1]` = NMIB (active-low non-maskable interrupt, edge-triggered)
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

RISCY-V02 supports maskable IRQ and non-maskable NMI interrupts.

**Vector table** (4-byte spacing for two-instruction trampolines):

| Vector | Address | Trigger |
|---|---|---|
| RESET | $0000 | RESB rising edge |
| IRQ | $0004 | IRQB low, level-sensitive, masked by I=1 |
| NMI | $0008 | NMIB falling edge, non-maskable |
| BRK | $000C | BRK instruction, unconditional |

Each vector has room for a two-instruction trampoline (LW + JR) to reach the actual handler.

**IRQ entry (when IRQB=0 and I=0):**
1. Complete the current instruction
2. Save EPC = (next_PC | I) — return address with I bit in bit 0
3. Set I = 1 — disable further interrupts
4. Jump to $0004

**NMI entry (on NMIB falling edge, regardless of I):**
1. Complete the current instruction
2. Save EPC = (next_PC | I) — overwrites any previous EPC
3. Set I = 1 — disable IRQs
4. Jump to $0008

**BRK entry (unconditional, regardless of I):**
1. Save EPC = (PC+2 | I) — return address with I bit in bit 0
2. Set I = 1 — disable IRQs
3. Jump to $000C

NMI is edge-triggered: only one NMI fires per falling edge. Holding NMIB low does not re-trigger. NMIB must return high and fall again for a new NMI. NMI has priority over IRQ; if both are pending simultaneously, NMI is taken first, and the subsequent I=1 masks the IRQ.

**Warning:** RETI from an NMI handler is undefined behavior. NMI overwrites EPC unconditionally, so if an NMI interrupts an IRQ handler before it saves EPC, the IRQ's return address is lost. NMI handlers typically reset, halt, or spin.

**Interrupt return (RETI instruction):**
1. Restore I = EPC[0]
2. Jump to EPC & $FFFE

**Interrupt latency:** 2 cycles from instruction completion to first handler instruction fetch. NMI edge detection is combinational — if the falling edge arrives on the same cycle that the FSM is ready, the NMI is taken immediately with no additional detection delay.

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

### LB — Load Byte (Sign-Extend)

```
[0110][rs1:3][off6:6][rd:3]
```

`rd = sext(MEM[rs1 + sext(off6)])`

Loads a single byte from memory and sign-extends it to 16 bits. The 6-bit signed offset is unscaled (range ±32 bytes from the base register). If bit 7 of the loaded byte is set, the high byte of rd is filled with 0xFF; otherwise 0x00.

**Cycle count:** 4 (2 address + 1 byte read + 1 extension)

### LBU — Load Byte (Zero-Extend)

```
[0111][rs1:3][off6:6][rd:3]
```

`rd = zext(MEM[rs1 + sext(off6)])`

Loads a single byte from memory and zero-extends it to 16 bits. The high byte of rd is always 0x00. Encoding and offset handling are identical to LB.

**Cycle count:** 4 (2 address + 1 byte read + 1 extension)

### SB — Store Byte

```
[1001][rs1:3][off6:6][rs2:3]
```

`MEM[rs1 + sext(off6)] = rs2[7:0]`

Stores the low byte of rs2 to memory. Only one byte is written; adjacent bytes are unaffected. The 6-bit signed offset is unscaled (range ±32 bytes).

**Cycle count:** 3 (2 address + 1 byte written)

### JR — Jump Register

```
[1011100][off6:6][rs:3]
```

`PC = rs + sext(off6) * 2`

Unconditional jump to the address computed from a register plus a scaled signed offset. The 6-bit offset is scaled by 2, giving a range of ±64 bytes from the register value.

**Cycle count:** 4 (2 fetch + 2 address computation in execute)

### AUIPC — Add Upper Immediate to PC

```
[001][imm10:10][rd:3]
```

`rd = PC + (sext(imm10) << 6)`

Adds a sign-extended 10-bit immediate, shifted left by 6, to the program counter of the AUIPC instruction. The result is written to rd. This provides a PC-relative base address that pairs with LW/SW/JR's 6-bit offset for full 16-bit PC-relative addressing: AUIPC provides the upper 10 bits (shifted left by 6), and the subsequent load/store/jump provides the lower 6 bits.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

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

### BRK — Software Interrupt

```
[1111111010000100]
```

`EPC = (PC+2 | I); I = 1; PC = $000C`

Triggers a software interrupt. Saves the return address with I bit to EPC, disables interrupts, and vectors to the BRK handler at $000C. Useful for system calls. BRK is unconditional — it fires regardless of the I bit. RETI restores the previous I state.

**Warning:** BRK overwrites EPC like any interrupt entry. If an NMI interrupts a BRK handler before it saves EPC, the return address is lost.

**Cycle count:** 3 (1 execute + 2 fetch after redirect)

### WAI — Wait for Interrupt

```
[1111111010000101]
```

Halts execution until an interrupt signal arrives. The PC is advanced past WAI before halting, so the return address always points to the next instruction.

- **NMI:** Taken immediately (vectors to $0008). RETI returns past WAI.
- **IRQ with I=0:** Taken (vectors to $0004). RETI returns past WAI.
- **IRQ with I=1:** WAI wakes and resumes at the next instruction without entering a handler (65C02-style hint behavior).

If an interrupt is already pending when WAI executes, it is serviced immediately without entering the wait state.

**Cycle count:** 2 (if interrupt already pending, same as NOP); otherwise halted until wake

### STP — Stop

```
[1111111010000110]
```

Halts the processor permanently. No interrupt (IRQ or NMI) can wake it. Only a hardware reset recovers execution. Both WAI and STP halt via internal clock gating — the CPU clock stops entirely, reducing dynamic power to zero.

**Cycle count:** 1 (execute then halt)

### EPCR — Read EPC

```
[1111111001110][rd:3]
```

`rd = EPC`

Reads the Exception Program Counter (including the I bit in bit 0) into a general-purpose register. Used in interrupt handlers to save EPC before re-enabling interrupts for nested interrupt support.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### EPCW — Write EPC

```
[1111111001111][rs:3]
```

`EPC = rs`

Writes a general-purpose register to the Exception Program Counter. Bit 0 of the source register sets the I bit that will be restored by the next RETI. Used to restore EPC after handling a nested interrupt.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### All Other Opcodes

Any instruction not matching the above is executed as a NOP: the PC advances past the instruction in 2 cycles with no other effect.

## Instruction Encoding Reference

```
Bits 15..13  Instruction   Format
──────────────────────────────────────────────
001          AUIPC         [imm10:10][rd:3]

Bits 15..12  Instruction   Format
──────────────────────────────────────────────
0110         LB            [rs1:3][off6:6][rd:3]
0111         LBU           [rs1:3][off6:6][rd:3]
1000         LW            [rs1:3][off6:6][rd:3]
1001         SB            [rs1:3][off6:6][rs2:3]
1010         SW            [rs1:3][off6:6][rs2:3]

Bits 15..9   Instruction   Format
──────────────────────────────────────────────
1011100      JR            [off6:6][rs:3]

Bits 15..3   Instruction   Format
──────────────────────────────────────────────
1111111001110    EPCR          [rd:3]
1111111001111    EPCW          [rs:3]

Full 16-bit  Instruction
──────────────────────────────────────────────
1111111010000001  RETI
1111111010000010  SEI
1111111010000011  CLI
1111111010000100  BRK
1111111010000101  WAI
1111111010000110  STP

All other    NOP           (ignored)
```

## Pipeline and Timing

The processor uses a 2-stage pipeline (Fetch and Execute) that overlap where possible. Most instructions take **2 cycles**. Loads and stores add **1 cycle for address computation** plus **1 cycle per byte** transferred.

### Cycle Counts (Throughput)

Throughput is measured from one instruction boundary (SYNC) to the next:

| Instruction | Cycles | Notes |
|---|---|---|
| NOP/SEI/CLI/AUIPC/EPCR/EPCW | 2 | 1 execute + 1 overlapped fetch |
| LB/LBU | 4 | 2 address + 1 byte read + 1 extension |
| SB | 3 | 2 address + 1 byte written (overlapped fetch) |
| LW/SW | 4 | 4 execute (address computation overlaps with fetch) |
| JR | 4 | 2 execute + 2 fetch after redirect |
| RETI | 3 | 1 execute + 2 fetch after redirect |
| BRK | 3 | 1 execute + 2 fetch after redirect |
| WAI (wake) | 2 | 1 execute + 1 overlapped fetch (if interrupt pending) |
| WAI (halt) | — | Halted until interrupt arrives |
| STP | 1 | Dispatch directly to halt (no execute cycle) |
| IRQ entry | 2 | Redirect at instruction boundary |
| NMI entry | 2 | Redirect at instruction boundary |

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
