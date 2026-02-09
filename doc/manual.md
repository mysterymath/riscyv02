# RISCY-V02 User Manual

RISCY-V02 is a 16-bit RISC processor that is a pin-compatible drop-in replacement for the WDC 65C02. It uses the same 8-bit multiplexed bus protocol, same control signals, and fits in the same Tiny Tapeout 1x2 tile. Different ISA, same socket.

## Current Status

The processor currently implements: **LW**, **SW**, **LB**, **LBU**, **SB**, **JR**, **JALR**, **J**, **JAL**, **AUIPC**, **LUI**, **LI**, **BZ**, **BNZ**, **BLTZ**, **BGEZ**, **ADD**, **SUB**, **AND**, **OR**, **XOR**, **SLT**, **SLTU**, **SLL**, **SRL**, **SRA**, **ADDI**, **ANDI**, **ORI**, **XORI**, **SLTIF**, **SLTIUF**, **XORIF**, **SLLI**, **SRLI**, **SRAI**, **RETI**, **SEI**, **CLI**, **BRK**, **WAI**, **STP**, **EPCR**, and **EPCW**. IRQ and NMI interrupt handling is supported. JAL/JALR write return addresses to R6 (the link register); subroutine return is `JR R6, 0`. All other opcodes are treated as NOPs (2-cycle no-ops that advance the PC).

## Comparison with Arlet 6502

Both designs target the IHP sg13g2 130nm process on a 1x2 Tiny Tapeout tile. The clock speed is pinned to match the 6502 (~62 MHz), simulating 1970s DRAM constraints where raw clock speed improvements don't matter. The comparison focuses on IPC and transistor efficiency.

| Metric | RISCY-V02 | Arlet 6502 |
|---|---|---|
| Clock period | 17 ns | 16 ns |
| fMax (slow corner) | 58.8 MHz | 62.5 MHz |
| Utilization | 59.6% | 48.4% |
| Transistor count (synth) | 16,282 | 13,082 |
| SRAM-adjusted | 13,132 | 13,082 |

RISCY-V02 supports full subroutine call/return (JAL/JALR + JR R6), PC-relative jumps (J), sign-bit branches (BLTZ/BGEZ), and immediate ALU operations (ADDI, ANDI, ORI, XORI, SLTIF, SLTIUF, XORIF). JAL/JALR write the return address to R6, and subroutine return is just `JR R6, 0` — no dedicated link register hardware needed. The SRAM-adjusted total is within 0.4% of the 6502 at 58.8 MHz, with significantly more capability per transistor (16-bit registers, 3-operand instructions, 2-cycle ALU ops, PC-relative jumps with ±4 KB range, hardware call/return, immediate arithmetic/logic).

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
| R6 | ra | Return address (link register) |
| R7 | sp | Stack pointer |

### Link Register (R6)

R6 serves as the link register. JAL and JALR write the return address (PC+2) to R6. Subroutine return is `JR R6, 0`. Since R6 is a regular GPR, it can be saved/restored with normal load/store instructions — no special LRR/LRW instructions needed. R6 is callee-saved: any function that makes calls must save R6 on entry and restore it before returning.

## Instruction Set

All instructions are 16 bits. Bits [15:12] form the **opcode**, which determines the instruction format:

| Opcode | Format | Field layout | Instructions |
|---|---|---|---|
| 0000..0011 | **U** | `[prefix:3][imm10:10][rd:3]` | LUI, AUIPC |
| 0100..0101 | **J** | `[prefix:4][off12:12]` | J, JAL |
| 0110..1010 | **S** | `[prefix:4][off6[2:0]:3][off6[5:3]:3][rs1:3][rd/rs2:3]` | LB, LBU, LW, SB, SW |
| 1011..1111 | **C** | `[1][grp:3][sub:3][payload:6][rd:3]` | All others |

U-format uses a 3-bit prefix (bits [15:13]), gaining one extra immediate bit. All other formats use the full 4-bit opcode. Within C-format, `grp` (bits [14:12]) and `sub` (bits [11:9]) identify the specific instruction. S-format scrambles the immediate so rs1 stays at [5:3] across all formats.

### LW — Load Word

```
[1000][off6[2:0]:3][off6[5:3]:3][rs1:3][rd:3]
```

`rd = MEM[rs1 + sext(off6) * 2]`

Loads a 16-bit word from memory. The 6-bit signed offset is scaled by 2, giving a range of ±64 bytes from the base register. The memory address must be word-aligned (bit 0 = 0). The low byte is read first, then the high byte.

**Cycle count:** 5 (2 base + 1 address + 2 bytes read)

### SW — Store Word

```
[1010][off6[2:0]:3][off6[5:3]:3][rs1:3][rs2:3]
```

`MEM[rs1 + sext(off6) * 2] = rs2`

Stores a 16-bit word to memory. The offset encoding is identical to LW. The low byte is written first, then the high byte.

**Cycle count:** 5 (2 base + 1 address + 2 bytes written)

### LB — Load Byte (Sign-Extend)

```
[0110][off6[2:0]:3][off6[5:3]:3][rs1:3][rd:3]
```

`rd = sext(MEM[rs1 + sext(off6)])`

Loads a single byte from memory and sign-extends it to 16 bits. The 6-bit signed offset is unscaled (range ±32 bytes from the base register). If bit 7 of the loaded byte is set, the high byte of rd is filled with 0xFF; otherwise 0x00.

**Cycle count:** 4 (2 address + 1 byte read + 1 extension)

### LBU — Load Byte (Zero-Extend)

```
[0111][off6[2:0]:3][off6[5:3]:3][rs1:3][rd:3]
```

`rd = zext(MEM[rs1 + sext(off6)])`

Loads a single byte from memory and zero-extends it to 16 bits. The high byte of rd is always 0x00. Encoding and offset handling are identical to LB.

**Cycle count:** 4 (2 address + 1 byte read + 1 extension)

### SB — Store Byte

```
[1001][off6[2:0]:3][off6[5:3]:3][rs1:3][rs2:3]
```

`MEM[rs1 + sext(off6)] = rs2[7:0]`

Stores the low byte of rs2 to memory. Only one byte is written; adjacent bytes are unaffected. The 6-bit signed offset is unscaled (range ±32 bytes).

**Cycle count:** 3 (2 address + 1 byte written)

### JR — Jump Register

```
[1101110][off6:6][rs:3]
```

`PC = rs + sext(off6) * 2`

Unconditional jump to the address computed from a register plus a scaled signed offset. The 6-bit offset is scaled by 2, giving a range of ±64 bytes from the register value.

**Cycle count:** 4 (2 fetch + 2 address computation in execute)

### BZ — Branch if Zero

```
[1101000][off6:6][rs:3]
```

`if rs == 0: PC = PC + sext(off6) * 2`

Branches to a PC-relative target if the source register is zero. The 6-bit signed offset is scaled by 2, giving a range of ±64 bytes from the next instruction address. The zero check spans two cycles (one byte per cycle) while the ALU speculatively computes the branch target in parallel. Pairs with SLT/SLTU for compare-and-branch patterns: `SLT t, a, b; BZ t, target` (branch if NOT less than).

**Cycle count:** 2 (not taken, overlapped fetch) / 4 (taken: 2 execute + 2 fetch after redirect)

### BNZ — Branch if Non-Zero

```
[1101001][off6:6][rs:3]
```

`if rs != 0: PC = PC + sext(off6) * 2`

Branches to a PC-relative target if the source register is non-zero. Encoding and offset handling are identical to BZ. Pairs with SLT/SLTU for compare-and-branch patterns: `SLT t, a, b; BNZ t, target` (branch if less than).

**Cycle count:** 2 (not taken, overlapped fetch) / 4 (taken: 2 execute + 2 fetch after redirect)

### BLTZ — Branch if Less Than Zero

```
[1101010][off6:6][rs:3]
```

`if rs < 0: PC = PC + sext(off6) * 2`

Branches to a PC-relative target if the source register is negative (sign bit set). The 6-bit signed offset is scaled by 2, giving a range of ±64 bytes from the next instruction address. Tests only the sign bit (rs[15]), so the branch decision is faster than a full zero check. Useful for loop termination on signed counters and sign-dependent control flow.

**Cycle count:** 2 (not taken, overlapped fetch) / 4 (taken: 2 execute + 2 fetch after redirect)

### BGEZ — Branch if Greater or Equal to Zero

```
[1101011][off6:6][rs:3]
```

`if rs >= 0: PC = PC + sext(off6) * 2`

Branches to a PC-relative target if the source register is non-negative (sign bit clear). Encoding and offset handling are identical to BLTZ. Zero is considered non-negative (sign bit = 0), so BGEZ branches on both zero and positive values.

**Cycle count:** 2 (not taken, overlapped fetch) / 4 (taken: 2 execute + 2 fetch after redirect)

### J — Jump

```
[0100][off12:12]
```

`PC = PC + sext(off12) * 2`

Unconditional PC-relative jump. The 12-bit signed offset is scaled by 2, giving a range of ±4096 bytes from the next instruction address. Uses the ALU to compute the target in two cycles (low byte, then high byte with carry).

**Cycle count:** 4 (2 execute + 2 fetch after redirect)

### JAL — Jump and Link

```
[0101][off12:12]
```

`R6 = PC+2; PC = PC + sext(off12) * 2`

Unconditional PC-relative jump that saves the return address in R6 (link register). The offset encoding is identical to J. JAL writes R6 = PC+2 (the address of the next instruction after JAL), then jumps to the target. Used for subroutine calls; return with `JR R6, 0`.

**Cycle count:** 4 (2 execute + 2 fetch after redirect)

### JALR — Jump and Link Register

```
[1101111][off6:6][rs:3]
```

`R6 = PC+2; PC = rs + sext(off6) * 2`

Register-indirect jump that saves the return address in R6 (link register). The offset encoding is identical to JR. Pairs with AUIPC for full 16-bit PC-relative function calls: `AUIPC t0, upper; JALR t0, lower`.

**Cycle count:** 4 (2 address computation + 2 fetch after redirect)

### AUIPC — Add Upper Immediate to PC

```
[001][imm10:10][rd:3]
```

`rd = (PC+2) + (sext(imm10) << 6)`

Adds a sign-extended 10-bit immediate, shifted left by 6, to the address of the next instruction (PC+2). The result is written to rd. This provides a PC-relative base address that pairs with LW/SW/JR's 6-bit offset for full 16-bit PC-relative addressing: AUIPC provides the upper 10 bits (shifted left by 6), and the subsequent load/store/jump provides the lower 6 bits. The use of PC+2 (rather than the AUIPC instruction's own address) is an implementation detail of the pipeline; the linker/assembler must account for this when computing immediates.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### LUI — Load Upper Immediate

```
[000][imm10:10][rd:3]
```

`rd = sext(imm10) << 6`

Loads a sign-extended 10-bit immediate, shifted left by 6, into a register. The result sets bits [15:6] of rd, with bits [5:0] cleared. Pairs with ADDI for full 16-bit constant loading: `LUI rd, hi; ADDI rd, lo`. The immediate range is -512 to +511, covering the full 16-bit address space when shifted.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### LI — Load Immediate

```
[1101100][imm6:6][rd:3]
```

`rd = sext(imm6)`

Loads a sign-extended 6-bit immediate into a register. The immediate range is -32 to +31. No memory access or register read is needed; the value is encoded directly in the instruction. Useful for loading small constants, loop counters, and flag values.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### ADD — Add

```
[1011000][rs2:3][rs1:3][rd:3]
```

`rd = rs1 + rs2`

Adds two registers and writes the result to rd. The 16-bit addition is performed in two cycles (low byte then high byte) with carry propagation between bytes.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### SUB — Subtract

```
[1011001][rs2:3][rs1:3][rd:3]
```

`rd = rs1 - rs2`

Subtracts rs2 from rs1 and writes the result to rd. Implemented as two's complement addition (invert rs2, carry-in = 1) with borrow propagation between bytes.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### AND — Bitwise And

```
[1011010][rs2:3][rs1:3][rd:3]
```

`rd = rs1 & rs2`

Bitwise AND of two registers. Each byte is computed independently (no carry chain).

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### OR — Bitwise Or

```
[1011011][rs2:3][rs1:3][rd:3]
```

`rd = rs1 | rs2`

Bitwise OR of two registers. Each byte is computed independently.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### XOR — Bitwise Exclusive Or

```
[1011100][rs2:3][rs1:3][rd:3]
```

`rd = rs1 ^ rs2`

Bitwise XOR of two registers. Each byte is computed independently.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### SLT — Set Less Than (Signed)

```
[1011101][rs2:3][rs1:3][rd:3]
```

`rd = (rs1 < rs2) ? 1 : 0` (signed comparison)

Compares rs1 and rs2 as signed 16-bit integers. If rs1 is less than rs2, rd is set to 1; otherwise rd is set to 0. Implemented by subtracting rs1 - rs2 and interpreting the carry/sign result. Pairs with BZ/BNZ for compare-and-branch patterns: `SLT t, a, b; BNZ t, target`.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### SLTU — Set Less Than (Unsigned)

```
[1011110][rs2:3][rs1:3][rd:3]
```

`rd = (rs1 < rs2) ? 1 : 0` (unsigned comparison)

Compares rs1 and rs2 as unsigned 16-bit integers. If rs1 is less than rs2, rd is set to 1; otherwise rd is set to 0. Implemented by subtracting and checking the borrow (carry out of the unsigned subtraction).

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### SLL — Shift Left Logical

```
[1100000][rs2:3][rs1:3][rd:3]
```

`rd = rs1 << rs2[3:0]`

Shifts rs1 left by the amount in rs2 (low 4 bits, range 0–15). Vacated bits are filled with zeros. The 16-bit shift is performed in two cycles using an 8-bit barrel shifter: cycle 1 processes the low byte, cycle 2 processes the high byte using bits that shifted across the byte boundary.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### SRL — Shift Right Logical

```
[1100010][rs2:3][rs1:3][rd:3]
```

`rd = rs1 >>u rs2[3:0]`

Shifts rs1 right by the amount in rs2 (low 4 bits, range 0–15). Vacated bits are filled with zeros. Right shifts process the high byte first (reversed order from left shifts) so that bits crossing the byte boundary flow correctly.

**Cycle count:** 2 (1 high byte + 1 low byte, overlapped fetch)

### SRA — Shift Right Arithmetic

```
[1100011][rs2:3][rs1:3][rd:3]
```

`rd = rs1 >>s rs2[3:0]`

Shifts rs1 right by the amount in rs2 (low 4 bits, range 0–15). Vacated bits are filled with copies of the sign bit (rs1[15]). Useful for dividing signed values by powers of two.

**Cycle count:** 2 (1 high byte + 1 low byte, overlapped fetch)

### ADDI — Add Immediate

```
[1110000][imm6:6][rd:3]
```

`rd = rd + sext(imm6)`

Adds a sign-extended 6-bit immediate (-32 to +31) to the destination register. The result overwrites rd. Useful for stack pointer adjustments (`ADDI sp, -4`), loop counter increments, and small constant additions without needing a separate register. Pairs with LUI for full 16-bit constant loading: `LUI rd, hi; ADDI rd, lo`.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### ANDI — And Immediate

```
[1110010][imm6:6][rd:3]
```

`rd = rd & sext(imm6)`

Bitwise AND of the destination register with a sign-extended 6-bit immediate. Useful for masking low bits (`ANDI rd, 0x1F` to keep bits [4:0]).

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### ORI — Or Immediate

```
[1110011][imm6:6][rd:3]
```

`rd = rd | sext(imm6)`

Bitwise OR of the destination register with a sign-extended 6-bit immediate. Sets specific bits without affecting others.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### XORI — Xor Immediate

```
[1110100][imm6:6][rd:3]
```

`rd = rd ^ sext(imm6)`

Bitwise XOR of the destination register with a sign-extended 6-bit immediate. `XORI rd, -1` acts as bitwise NOT.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### SLTIF — Set Less Than Immediate (Signed, Fixed-Destination)

```
[1110101][imm6:6][rs:3]
```

`t0 = (rs < sext(imm6)) ? 1 : 0` (signed comparison)

Compares the source register against a sign-extended 6-bit immediate as signed 16-bit integers. The result (0 or 1) is written to R2 (t0), preserving the source register. Pairs with BNZ/BZ for compare-and-branch: `SLTIF a0, 10; BNZ t0, target`.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### SLTIUF — Set Less Than Immediate (Unsigned, Fixed-Destination)

```
[1110110][imm6:6][rs:3]
```

`t0 = (rs <u sext(imm6)) ? 1 : 0` (unsigned comparison)

Compares the source register against a sign-extended 6-bit immediate as unsigned 16-bit integers. The immediate is sign-extended then treated as unsigned for the comparison. The result is written to R2 (t0).

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### XORIF — Xor Immediate (Fixed-Destination)

```
[1110111][imm6:6][rs:3]
```

`t0 = rs ^ sext(imm6)`

Bitwise XOR of the source register with a sign-extended 6-bit immediate, writing the result to R2 (t0) while preserving the source register. Useful for equality testing: if rs equals sext(imm6), t0 will be zero. Pattern: `XORIF a0, val; BZ t0, equal_label`.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### SLLI — Shift Left Logical Immediate

```
[1100100][00][imm4:4][rd:3]
```

`rd = rd << imm4`

Shifts rd left by a 4-bit immediate (0–15). Vacated bits are filled with zeros. The shift amount is extracted from bits [6:3] of the instruction; bits [8:7] are reserved (zero).

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### SRLI — Shift Right Logical Immediate

```
[1100110][00][imm4:4][rd:3]
```

`rd = rd >>u imm4`

Shifts rd right by a 4-bit immediate (0–15). Vacated bits are filled with zeros.

**Cycle count:** 2 (1 high byte + 1 low byte, overlapped fetch)

### SRAI — Shift Right Arithmetic Immediate

```
[1100111][00][imm4:4][rd:3]
```

`rd = rd >>s imm4`

Shifts rd right by a 4-bit immediate (0–15). Vacated bits are filled with copies of the sign bit (rd[15]). Useful for dividing signed values by powers of two: `SRAI rd, 1` divides by 2 (with rounding toward negative infinity).

**Cycle count:** 2 (1 high byte + 1 low byte, overlapped fetch)

### RETI — Return from Interrupt

```
[1111011][000000000]
```

`I = EPC[0]; PC = EPC & $FFFE`

Restores the interrupt enable state from the saved EPC and returns to the interrupted code. The I bit is restored from EPC bit 0, and PC is set to EPC with bit 0 cleared (ensuring word alignment).

**Cycle count:** 2 (fetch + redirect)

### SEI — Set Interrupt Disable

```
[1111001][000000000]
```

`I = 1`

Disables interrupts by setting the I bit. While I=1, IRQB assertions are ignored.

**Cycle count:** 2

### CLI — Clear Interrupt Disable

```
[1111010][000000000]
```

`I = 0`

Enables interrupts by clearing the I bit. After CLI, a pending IRQ (IRQB=0) will be taken at the next instruction boundary.

**Cycle count:** 2

### BRK — Software Interrupt

```
[1111100][000000000]
```

`EPC = (PC+2 | I); I = 1; PC = $000C`

Triggers a software interrupt. Saves the return address with I bit to EPC, disables interrupts, and vectors to the BRK handler at $000C. Useful for system calls. BRK is unconditional — it fires regardless of the I bit. RETI restores the previous I state.

**Warning:** BRK overwrites EPC like any interrupt entry. If an NMI interrupts a BRK handler before it saves EPC, the return address is lost.

**Cycle count:** 3 (1 execute + 2 fetch after redirect)

### WAI — Wait for Interrupt

```
[1111101][000000000]
```

Halts execution until an interrupt signal arrives. The PC is advanced past WAI before halting, so the return address always points to the next instruction.

- **NMI:** Taken immediately (vectors to $0008). RETI returns past WAI.
- **IRQ with I=0:** Taken (vectors to $0004). RETI returns past WAI.
- **IRQ with I=1:** WAI wakes and resumes at the next instruction without entering a handler (65C02-style hint behavior).

If an interrupt is already pending when WAI executes, it is serviced immediately without entering the wait state.

**Cycle count:** 2 (if interrupt already pending, same as NOP); otherwise halted until wake

### STP — Stop

```
[1111111][000000000]
```

Halts the processor permanently. No interrupt (IRQ or NMI) can wake it. Only a hardware reset recovers execution. Both WAI and STP halt via internal clock gating — the CPU clock stops entirely, reducing dynamic power to zero.

**Cycle count:** 1 (execute then halt)

### EPCR — Read EPC

```
[1111110][000000][rd:3]
```

`rd = EPC`

Reads the Exception Program Counter (including the I bit in bit 0) into a general-purpose register. Used in interrupt handlers to save EPC before re-enabling interrupts for nested interrupt support.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### EPCW — Write EPC

```
[1101101][000000][rs:3]
```

`EPC = rs`

Writes a general-purpose register to the Exception Program Counter. Bit 0 of the source register sets the I bit that will be restored by the next RETI. Used to restore EPC after handling a nested interrupt.

**Cycle count:** 2 (1 low byte + 1 high byte, overlapped fetch)

### All Other Opcodes

Any instruction not matching the above is executed as a NOP: the PC advances past the instruction in 2 cycles with no other effect.

## Instruction Encoding Reference

### Opcode and Formats

Bits [15:12] form the **opcode** and determine the instruction format. Four formats exist:

```
Format  Opcode range   Layout                                           Instructions
U       0000..0011     [prefix:3][imm10:10][rd:3]                       LUI, AUIPC
J       0100..0101     [prefix:4][off12:12]                             J, JAL
S       0110..1010     [prefix:4][off6[2:0]:3][off6[5:3]:3][rs1:3][rd/rs2:3]   loads, stores
C       1011..1111     [1][grp:3][sub:3][payload:6][rd:3]               all others
```

U-format uses a 3-bit prefix (bits [15:13]), gaining one extra immediate bit. S-format scrambles the immediate so rs1 stays at [5:3] across all formats.

Within C-format, `grp` (bits [14:12]) and `sub` (bits [11:9]) identify the specific instruction. The 6-bit payload is rs2+rs1 for R-type operations or imm6 for I-type operations. The hardware reads `op_r = {grp, sub}` directly from the instruction word.

### Encoding Table

```
─── U-format (opcode 0000..0011): upper immediate ───
Opcode  Instruction   Payload
000     LUI           [imm10:10][rd:3]
001     AUIPC         [imm10:10][rd:3]

─── J-format (opcode 0100..0101): PC-relative jump ───
Opcode  Instruction   Payload
0100    J             [off12:12]
0101    JAL           [off12:12]

─── S-format (opcode 0110..1010): load/store ───
Opcode  Instruction   Payload
0110    LB            [off6[2:0]:3][off6[5:3]:3][rs1:3][rd:3]
0111    LBU           [off6[2:0]:3][off6[5:3]:3][rs1:3][rd:3]
1000    LW            [off6[2:0]:3][off6[5:3]:3][rs1:3][rd:3]
1001    SB            [off6[2:0]:3][off6[5:3]:3][rs1:3][rs2:3]
1010    SW            [off6[2:0]:3][off6[5:3]:3][rs1:3][rs2:3]

─── C-format (opcode 1011..1111): compact ───
Opcode  grp  sub  Instruction   Payload
1011    011  000  ADD           [rs2:3][rs1:3][rd:3]
1011    011  001  SUB           [rs2:3][rs1:3][rd:3]
1011    011  010  AND           [rs2:3][rs1:3][rd:3]
1011    011  011  OR            [rs2:3][rs1:3][rd:3]
1011    011  100  XOR           [rs2:3][rs1:3][rd:3]
1011    011  101  SLT           [rs2:3][rs1:3][rd:3]
1011    011  110  SLTU          [rs2:3][rs1:3][rd:3]
1100    100  000  SLL           [rs2:3][rs1:3][rd:3]
1100    100  010  SRL           [rs2:3][rs1:3][rd:3]
1100    100  011  SRA           [rs2:3][rs1:3][rd:3]
1100    100  100  SLLI          [00][imm4:4][rd:3]
1100    100  110  SRLI          [00][imm4:4][rd:3]
1100    100  111  SRAI          [00][imm4:4][rd:3]
1101    101  000  BZ            [off6:6][rs:3]
1101    101  001  BNZ           [off6:6][rs:3]
1101    101  010  BLTZ          [off6:6][rs:3]
1101    101  011  BGEZ          [off6:6][rs:3]
1101    101  100  LI            [imm6:6][rd:3]
1101    101  101  EPCW          [000000][rs:3]
1101    101  110  JR            [off6:6][rs:3]
1101    101  111  JALR          [off6:6][rs:3]
1110    110  000  ADDI          [imm6:6][rd:3]
1110    110  010  ANDI          [imm6:6][rd:3]
1110    110  011  ORI           [imm6:6][rd:3]
1110    110  100  XORI          [imm6:6][rd:3]
1110    110  101  SLTIF         [imm6:6][rs:3]
1110    110  110  SLTIUF        [imm6:6][rs:3]
1110    110  111  XORIF         [imm6:6][rs:3]
1111    111  001  SEI           [000000000]
1111    111  010  CLI           [000000000]
1111    111  011  RETI          [000000000]
1111    111  100  BRK           [000000000]
1111    111  101  WAI           [000000000]
1111    111  110  EPCR          [000000][rd:3]
1111    111  111  STP           [000000000]

All other encodings execute as NOP (2-cycle no-op).
```

## Pipeline and Timing

The processor uses a 2-stage pipeline (Fetch and Execute) that overlap where possible. Most instructions take **2 cycles**. Loads and stores add **1 cycle for address computation** plus **1 cycle per byte** transferred.

### Cycle Counts (Throughput)

Throughput is measured from one instruction boundary (SYNC) to the next:

| Instruction | Cycles | Notes |
|---|---|---|
| NOP/SEI/CLI/AUIPC/LUI/LI/EPCR/EPCW/ADD/SUB/AND/OR/XOR/SLT/SLTU/SLL/SRL/SRA/ADDI/ANDI/ORI/XORI/SLTIF/SLTIUF/XORIF/SLLI/SRLI/SRAI | 2 | 1 execute + 1 overlapped fetch |
| BZ/BNZ/BLTZ/BGEZ (not taken) | 2 | 1 execute + 1 overlapped fetch |
| BZ/BNZ/BLTZ/BGEZ (taken) | 4 | 2 execute + 2 fetch after redirect |
| LB/LBU | 4 | 2 address + 1 byte read + 1 extension |
| SB | 3 | 2 address + 1 byte written (overlapped fetch) |
| LW/SW | 4 | 4 execute (address computation overlaps with fetch) |
| JR/JALR | 4 | 2 execute + 2 fetch after redirect |
| J/JAL | 4 | 2 execute + 2 fetch after redirect |
| RETI | 3 | 1 execute + 2 fetch after redirect |
| BRK | 3 | 1 execute + 2 fetch after redirect |
| WAI (wake) | 2 | 1 execute + 1 overlapped fetch (if interrupt pending) |
| WAI (halt) | — | Halted until interrupt arrives |
| STP | 1 | Dispatch directly to halt (no execute cycle) |
| IRQ entry | 2 | Redirect at instruction boundary |
| NMI entry | 2 | Redirect at instruction boundary |

Instructions that redirect (JR, JALR, J, JAL, RETI) flush the speculative fetch and must wait for new instruction bytes. Non-redirecting instructions benefit from fetch/execute overlap.

### Control Flow Handling

JR/JALR and J/JAL are processed by the execute stage, which computes the target address using the ALU. While execute computes the target, fetch speculatively continues from the sequential PC. When execute completes the jump, it redirects fetch to the correct address, discarding the speculative fetch. JALR and JAL also write R6 (R6 = PC+2) to save the return address for subroutine calls. Subroutine return is `JR R6, 0`, which is a regular register-indirect jump through R6.

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
