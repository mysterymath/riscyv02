# RISCY-V02 User Manual

RISCY-V02 is a 16-bit RISC processor that is a pin-compatible drop-in replacement for the WDC 65C02. It uses the same 8-bit multiplexed bus protocol, same control signals, and fits in the same Tiny Tapeout 1x2 tile. Different ISA, same socket.

## Comparison with Arlet 6502

Both designs target the IHP sg13g2 130nm process on a 1x2 Tiny Tapeout tile. The clock speed is pinned to match the 6502 (~62 MHz), simulating 1970s DRAM constraints where raw clock speed improvements don't matter. The comparison focuses on IPC and transistor efficiency.

| Metric | RISCY-V02 | Arlet 6502 |
|---|---|---|
| Clock period | 14 ns | 14 ns |
| fMax (slow corner) | 71.4 MHz | 71.4 MHz |
| Utilization | 59.6% | 45.3% |
| Transistor count (synth) | 16,074 | 13,176 |
| SRAM-adjusted | 12,750 | 13,176 |

The SRAM-adjusted total is 3.2% below the 6502, with significantly more capability per transistor: 16-bit registers, 3-operand ALU instructions, 2-cycle execute, PC-relative jumps, hardware call/return, and immediate arithmetic/logic. Unrecognized opcodes are treated as NOPs (2-cycle no-ops that advance the PC).

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

- **8 general-purpose registers**: R0-R7, each 16 bits wide (3-bit encoding)
- **16-bit program counter** (not directly accessible)
- **16-bit address space**, byte-addressable, little-endian
- **Fixed 16-bit instructions**, fetched low byte first
- **2-stage pipeline**: Fetch and Execute with speculative fetch and redirect

### Reset

On reset:
- PC is set to $0000 and execution begins
- I (interrupt disable) is set to 1 -- interrupts are disabled
- All registers are cleared to zero

There is no vector fetch; code is placed directly at address $0000. Software must execute CLI to enable interrupts.

### Interrupts

RISCY-V02 supports maskable IRQ and non-maskable NMI interrupts.

**Vector table** (2-byte spacing; IRQ last for inline handler):

| Vector ID | Address | Trigger |
|---|---|---|
| RESET | $0000 | RESB rising edge |
| 0 (NMI) | $0002 | NMIB falling edge, non-maskable |
| 1 (BRK) | $0004 | BRK instruction, unconditional |
| 2 (IRQ) | $0006 | IRQB low, level-sensitive, masked by I=1 |

Vector addresses are computed as `(vector_id + 1) * 2`. Each vector slot is one instruction (2 bytes) -- enough for a JR trampoline to reach the actual handler. IRQ is placed last so its handler can run inline without a jump, since nothing follows it.

**Instruction synthesis:** All interrupt entry (IRQ, NMI, BRK) uses the same mechanism. The hardware writes a synthetic INT instruction into the instruction register (ir) encoding the vector ID and destination register (R6). This synthetic instruction then executes through the normal decode path -- no special-case interrupt logic in the execute unit. IRQ and NMI are *internal opcodes*: they use instruction encodings synthesized by the interrupt controller rather than fetched from memory. BRK is the software-accessible form of the same instruction family. Since all three share the same encoding format, software can also trigger IRQ/NMI vectors directly by encoding the corresponding INT instruction.

**IRQ entry (when IRQB=0 and I=0):**
1. Complete the current instruction
2. Synthesize INT instruction with vector 2 into ir
3. Save EPC = (next_PC | I) -- return address with I bit in bit 0
4. Set I = 1 -- disable further interrupts
5. Jump to $0006

**NMI entry (on NMIB falling edge, regardless of I):**
1. Complete the current instruction
2. Synthesize INT instruction with vector 0 into ir
3. Save EPC = (next_PC | I) -- overwrites any previous EPC
4. Set I = 1 -- disable IRQs
5. Jump to $0002

**BRK entry (unconditional, regardless of I):**
1. Save EPC = (PC+2 | I) -- return address with I bit in bit 0
2. Set I = 1 -- disable IRQs
3. Jump to $0004

NMI is edge-triggered: only one NMI fires per falling edge. Holding NMIB low does not re-trigger. NMIB must return high and fall again for a new NMI. NMI has priority over IRQ; if both are pending simultaneously, NMI is taken first, and the subsequent I=1 masks the IRQ.

**Warning:** RETI from an NMI handler is undefined behavior. NMI overwrites EPC unconditionally, so if an NMI interrupts an IRQ handler before it saves EPC (via EPCR), the IRQ's return address is lost. NMI handlers typically reset, halt, or spin.

**Interrupt return (RETI instruction):**
1. Restore I = EPC[0]
2. Jump to EPC & $FFFE

**Exception PC (EPC) register:** The EPC is a 16-bit register stored as entry 8 in the register file (logical R9). It is not directly addressable through normal register fields (which are 3-bit), but is accessible through dedicated EPCR and EPCW instructions. On interrupt entry, EPC receives `{return_addr[15:1], old_I_flag}`. EPCR copies EPC to a GP register; EPCW copies a GP register to EPC. This allows interrupt handlers to read, modify, or redirect the return address. All GP registers (R0-R7) are directly accessible in interrupt context -- there is no register banking.

**Interrupt latency:** 4 cycles from instruction completion to first handler instruction fetch (2 cycles to save EPC + 2 fetch). NMI edge detection is combinational -- if the falling edge arrives on the same cycle that the FSM is ready, the NMI is taken immediately with no additional detection delay.

### Register Naming Convention

| Register | Name | Suggested Purpose |
|---|---|---|
| R0 | a0 | Accumulator / implicit base address (R,8 format loads/stores) |
| R1 | a1 | Argument / comparison result (SLTI/SLTUI/XORIF/ANDIF dest) |
| R2 | t0 | Temporary 0 |
| R3 | t1 | Temporary 1 |
| R4 | s0 | Saved register 0 |
| R5 | s1 | Saved register 1 |
| R6 | ra | Return address (link register) |
| R7 | sp | Stack pointer |

R0 is the implicit base address register for R,8-format loads and stores: the effective address is `R0 + sext(imm8)`, and `ir[2:0]` selects the data register. This is the same convention as R7-based SP-relative instructions, but using R0 as the base. SLTI, SLTUI, XORIF, and ANDIF write their result to R1 (non-destructive compare/test patterns that preserve both R0 and the source register). R,R-format loads and stores allow explicit register selection for both data and base, with no offset.

### Link Register (R6)

R6 serves as the link register. JAL and JALR write the return address (PC+2) to R6. Subroutine return is `JR R6, 0`. Since R6 is a regular GPR, it can be saved/restored with normal load/store instructions. R6 is callee-saved: any function that makes calls must save R6 on entry and restore it before returning.

R6 is a normal register in all contexts, including interrupt handlers. The interrupt return address is stored in the EPC register (see Interrupts section), not in R6. Interrupt handlers that need to use R6 (or any other register) must save and restore it manually.

## Instruction Encoding

All instructions are 16 bits. The encoding uses a **variable-width prefix-free** scheme: shorter prefixes for more common instructions. The word `0x0000` is ADDI R0, 0 = NOP.

### Encoding Overview

**53 instructions defined. 8,459 of 65,536 encodings free (12.9%).**

| Format | Prefix | Layout | Used |
|---|---|---|---|
| R,8 | 5-bit | `[imm8:8\|reg:3]` | 23 |
| R,7 | 6-bit | `[imm7:7\|reg:3]` | 2 |
| "10" | 6-bit | `[off10:10]` | 2 |
| R,R,R | 7-bit | `[rd:3\|rs2:3\|rs1:3]` | 10 |
| R,4 | 9-bit | `[shamt:4\|reg:3]` | 3 |
| R,R | 10-bit | `[rd:3\|rs:3]` | 5 |
| System | 11-16 bit | various | 8 |

Capacity = how many instructions of that format could fit in the total free space. Mutually exclusive: one R,8 uses the space of 4 R,R,R or 32 R,R. Fields are packed MSB-first: prefix at top, register at LSB.

### Prefix Table

```
--- R,8 format (5-bit prefix) ---
00000   ADDI    rd = rd + sext(imm8)
00001   LI      rd = sext(imm8)
00010   LW      rd = mem16[R0 + sext(imm8)]
00011   LB      rd = sext(mem[R0 + sext(imm8)])
00100   LBU     rd = zext(mem[R0 + sext(imm8)])
00101   SW      mem16[R0 + sext(imm8)] = rs
00110   SB      mem[R0 + sext(imm8)] = rs[7:0]
00111   JR      pc = rs + sext(imm8) << 1
01000   JALR    rs = pc+2; pc = rs + sext(imm8) << 1


01001   ANDI    rd = rd & zext(imm8)
01010   ORI     rd = rd | zext(imm8)
01011   XORI    rd = rd ^ zext(imm8)
01100   SLTI    R1 = (rs < sext(imm8)) ? 1 : 0   (signed)
01101   SLTUI   R1 = (rs <u sext(imm8)) ? 1 : 0  (unsigned)
01110   BZ      if rs == 0, pc += sext(imm8) << 1
01111   BNZ     if rs != 0, pc += sext(imm8) << 1
10000   XORIF   R1 = rs ^ zext(imm8)
10110   ANDIF   R1 = rs & zext(imm8)
10001   LWS    rd = mem16[R7 + sext(imm8)]
10010   LBS    rd = sext(mem[R7 + sext(imm8)])
10011   LBUS   rd = zext(mem[R7 + sext(imm8)])
10100   SWS    mem16[R7 + sext(imm8)] = rs
10101   SBS    mem[R7 + sext(imm8)] = rs[7:0]

--- R,7 format (6-bit prefix) ---
110100  LUI     rd = sext(imm7) << 9
110101  AUIPC   rd = pc + (sext(imm7) << 9)

--- "10" format (6-bit prefix) ---
110110  J       pc += sext(off10) << 1
110111  JAL     R6 = pc+2; pc += sext(off10) << 1

--- R,R,R format (7-bit prefix) ---
1110000 ADD     rd = rs1 + rs2
1110001 SUB     rd = rs1 - rs2
1110010 AND     rd = rs1 & rs2
1110011 OR      rd = rs1 | rs2
1110100 XOR     rd = rs1 ^ rs2
1110101 SLT     rd = (rs1 < rs2) ? 1 : 0   (signed)
1110110 SLTU    rd = (rs1 <u rs2) ? 1 : 0  (unsigned)
1110111 SLL     rd = rs1 << rs2[3:0]
1111000 SRL     rd = rs1 >>u rs2[3:0]
1111001 SRA     rd = rs1 >>s rs2[3:0]

--- R,4 format (9-bit prefix) ---
111101000  SLLI   rd = rd << shamt
111101001  SRLI   rd = rd >>u shamt
111101010  SRAI   rd = rd >>s shamt

--- R,R format (10-bit prefix) ---
1111010110  LWR    rd = mem16[rs]
1111010111  LBR    rd = sext(mem[rs])
1111011000  LBUR   rd = zext(mem[rs])
1111011001  SWR    mem16[rs] = rd
1111011010  SBR    mem[rs] = rd[7:0]

--- System format (10-bit prefix + sub) ---
1111100000 000001  SEI    I = 1
1111100000 000010  CLI    I = 0
1111100000 000011  RETI   I = EPC[0]; pc = EPC & $FFFE
1111100000 010rrr  EPCR   rd = EPC
1111100000 011rrr  EPCW   EPC = rs
1111100000 1xxxxx  INT    EPC = (pc+2 | I); I = 1; pc = (vec+1)*2
1111100000 000101  WAI    halt until interrupt
1111100000 000111  STP    halt permanently (reset only)

All other encodings execute as NOP (2-cycle no-op).
```

## Instruction Set

### R,9 Format -- Loads, Stores, Immediate, Jumps

#### ADDI -- Add Immediate

`rd = rd + sext(imm8)` -- 2 cycles

Adds a sign-extended 8-bit immediate (-128 to +127) to the destination register. `ADDI R0, 0` (encoding `0x0000`) is the canonical NOP. Useful for stack pointer adjustments and small constant additions. Pairs with LUI for full 16-bit constant loading: `LUI rd, hi; ADDI rd, lo`.

#### LI -- Load Immediate

`rd = sext(imm8)` -- 2 cycles

Loads a sign-extended 8-bit immediate (-128 to +127) into a register. No memory access or register read needed.

#### LW -- Load Word

`rd = MEM16[R0 + sext(imm8)]` -- 4 cycles

Loads a 16-bit word from memory into the register at ir[2:0]. R0 is the implicit base address; the 8-bit signed offset is a byte offset (not scaled), giving a range of -128 to +127 bytes from R0. The low byte is read first, then the high byte.

#### LB -- Load Byte (Sign-Extend)

`rd = sext(MEM[R0 + sext(imm8)])` -- 3 cycles

Loads a single byte and sign-extends it to 16 bits into the register at ir[2:0]. R0 is the implicit base. If bit 7 is set, the high byte is filled with 0xFF; otherwise 0x00.

#### LBU -- Load Byte (Zero-Extend)

`rd = zext(MEM[R0 + sext(imm8)])` -- 3 cycles

Loads a single byte and zero-extends it to 16 bits into the register at ir[2:0]. R0 is the implicit base. The high byte is always 0x00.

#### SW -- Store Word

`MEM16[R0 + sext(imm8)] = rs` -- 4 cycles

Stores the register at ir[2:0] as a 16-bit word to memory. R0 is the implicit base address. The low byte is written first, then the high byte.

#### SB -- Store Byte

`MEM[R0 + sext(imm8)] = rs[7:0]` -- 3 cycles

Stores the low byte of the register at ir[2:0] to memory. R0 is the implicit base address.

#### JR -- Jump Register

`PC = rs + sext(imm8) << 1` -- 4 cycles

Unconditional jump to a register plus a scaled signed offset. The 8-bit offset is shifted left by 1, giving a range of -256 to +254 bytes from the register value.

#### JALR -- Jump and Link Register

`rs = PC+2; PC = rs + sext(imm8) << 1` -- 4 cycles

Register-indirect jump that saves the return address in the source register. In the R,8 format, the single register field serves as both jump base and link destination. The conventional call sequence uses R6: `JALR R6, offset` reads the jump target from R6, then writes the return address back to R6. Pairs with AUIPC for full 16-bit PC-relative function calls: `AUIPC t0, upper; JALR t0, lower`.


#### ANDI -- And Immediate

`rd = rd & zext(imm8)` -- 2 cycles

Bitwise AND with a zero-extended 8-bit immediate (0 to 255). Only the low byte is masked; the high byte of rd is always cleared.

#### ORI -- Or Immediate

`rd = rd | zext(imm8)` -- 2 cycles

Bitwise OR with a zero-extended 8-bit immediate. Sets bits in the low byte without affecting the high byte.

#### XORI -- Xor Immediate

`rd = rd ^ zext(imm8)` -- 2 cycles

Bitwise XOR with a zero-extended 8-bit immediate. Toggles bits in the low byte without affecting the high byte.

#### SLTI -- Set Less Than Immediate (Signed)

`R1 = (rs < sext(imm8)) ? 1 : 0` -- 2 cycles

Compares the source register against a sign-extended 8-bit immediate (-128 to +127) as signed integers. The result (0 or 1) is written to R1, preserving both R0 (the base register) and the source register. Pattern: `SLTI rs, val; BNZ R1, target` (branch if rs < val).

#### SLTUI -- Set Less Than Immediate (Unsigned)

`R1 = (rs <u sext(imm8)) ? 1 : 0` -- 2 cycles

Compares the source register against a sign-extended 8-bit immediate as unsigned integers. The immediate is sign-extended then treated as unsigned. The result is written to R1.

#### BZ -- Branch if Zero

`if rs == 0: PC += sext(off8) << 1` -- 2 cycles (not taken) / 4 cycles (taken)

Branches to a PC-relative target if the source register is zero. The 8-bit signed offset is shifted left by 1, giving a range of -256 to +254 bytes from the next instruction address. Pairs with SLT/SLTU for compare-and-branch: `SLT t, a, b; BZ t, target` (branch if NOT less than).

#### BNZ -- Branch if Non-Zero

`if rs != 0: PC += sext(off8) << 1` -- 2 cycles (not taken) / 4 cycles (taken)

Branches to a PC-relative target if the source register is non-zero. Pairs with SLT/SLTU: `SLT t, a, b; BNZ t, target` (branch if less than).

#### XORIF -- Xor Immediate (Fixed-Destination)

`R1 = rs ^ zext(imm8)` -- 2 cycles

Bitwise XOR of the source register with a zero-extended 8-bit immediate, writing the result to R1 while preserving both R0 and the source register. Useful for equality testing: if rs equals zext(imm8), R1 will be zero. Pattern: `XORIF rs, val; BZ R1, equal_label`.

#### ANDIF -- And Immediate (Fixed-Destination)

`R1 = rs & zext(imm8)` -- 2 cycles

Bitwise AND of the source register with a zero-extended 8-bit immediate, writing the result to R1 while preserving the source register. Useful for bit testing: if any masked bits are set, R1 will be non-zero. Pattern: `ANDIF rs, 0x80; BNZ R1, bit7_set`.

### R,7 Format -- Upper Immediate

#### LUI -- Load Upper Immediate

`rd = sext(imm7) << 9` -- 2 cycles

Loads a sign-extended 7-bit immediate, shifted left by 9, into a register. The low 9 bits are cleared. The immediate range is -64 to +63, covering the full 16-bit address space when shifted. Pairs with ADDI for full 16-bit constant loading: `LUI rd, hi; ADDI rd, lo`.

#### AUIPC -- Add Upper Immediate to PC

`rd = (PC+2) + (sext(imm7) << 9)` -- 2 cycles

Adds a sign-extended 7-bit immediate, shifted left by 9, to the address of the next instruction (PC+2). Pairs with LW/SW/JR's offset for PC-relative addressing: AUIPC provides the upper bits and the subsequent load/store/jump provides the lower bits.

### "10" Format -- PC-Relative Jumps

#### J -- Jump

`PC += sext(off10) << 1` -- 4 cycles

Unconditional PC-relative jump. The 10-bit signed offset is shifted left by 1, giving a range of -1024 to +1022 bytes from the next instruction address.

#### JAL -- Jump and Link

`R6 = PC+2; PC += sext(off10) << 1` -- 4 cycles

Unconditional PC-relative jump that saves the return address in R6. Used for subroutine calls; return with `JR R6, 0`.

### R,R,R Format -- Register ALU

All R,R,R instructions are 2 cycles.

#### ADD -- `rd = rs1 + rs2`
#### SUB -- `rd = rs1 - rs2`
#### AND -- `rd = rs1 & rs2`
#### OR -- `rd = rs1 | rs2`
#### XOR -- `rd = rs1 ^ rs2`

#### SLT -- Set Less Than (Signed)

`rd = (rs1 < rs2) ? 1 : 0`

Compares rs1 and rs2 as signed 16-bit integers.

#### SLTU -- Set Less Than (Unsigned)

`rd = (rs1 <u rs2) ? 1 : 0`

Compares rs1 and rs2 as unsigned 16-bit integers.

#### SLL -- Shift Left Logical

`rd = rs1 << rs2[3:0]`

Shifts rs1 left by the amount in rs2 (low 4 bits, range 0-15). Vacated bits are filled with zeros.

#### SRL -- Shift Right Logical

`rd = rs1 >>u rs2[3:0]`

Shifts rs1 right by the amount in rs2 (low 4 bits). Vacated bits are filled with zeros.

#### SRA -- Shift Right Arithmetic

`rd = rs1 >>s rs2[3:0]`

Shifts rs1 right by the amount in rs2 (low 4 bits). Vacated bits are filled with copies of the sign bit (rs1[15]).

### R,4 Format -- Shift Immediate

All shift immediate instructions are 2 cycles and operate in-place (rd = rd shift shamt).

#### SLLI -- `rd = rd << shamt` (shamt 0-15)
#### SRLI -- `rd = rd >>u shamt` (shamt 0-15)
#### SRAI -- `rd = rd >>s shamt` (shamt 0-15)

### R,R Format -- Register Load/Store

R,R-format loads and stores use explicit registers for both data and base, with no offset.

#### LWR -- `rd = MEM16[rs]` -- 4 cycles
#### LBR -- `rd = sext(MEM[rs])` -- 3 cycles
#### LBUR -- `rd = zext(MEM[rs])` -- 3 cycles
#### SWR -- `MEM16[rs] = rd` -- 4 cycles
#### SBR -- `MEM[rs] = rd[7:0]` -- 3 cycles

### System Format

#### SEI -- Set Interrupt Disable

`I = 1` -- 2 cycles

Disables interrupts.

#### CLI -- Clear Interrupt Disable

`I = 0` -- 2 cycles

Enables interrupts. A pending IRQ (IRQB=0) will be taken at the next instruction boundary.

#### RETI -- Return from Interrupt

`I = EPC[0]; PC = EPC & $FFFE` -- 4 cycles

Restores the interrupt enable state from the EPC register and returns to the interrupted code. The I bit is restored from EPC bit 0, and PC is set to EPC with bit 0 cleared.

#### EPCR -- Read Exception PC

`rd = EPC` -- 2 cycles

Copies the Exception PC register to a general-purpose register. The value includes the saved I bit in bit 0. Register is at ir[2:0].

#### EPCW -- Write Exception PC

`EPC = rs` -- 2 cycles

Copies a general-purpose register to the Exception PC register. Bit 0 of the written value becomes the I bit restored by the next RETI. Register is at ir[2:0].

#### INT -- Software Interrupt

`EPC = (PC+2 | I); I = 1; PC = (vector[1:0] + 1) * 2` -- 4 cycles

Triggers a software interrupt. Saves the return address (with I bit in bit 0) to EPC, disables interrupts, and vectors to the handler. BRK is the conventional name for INT with vector 1 (handler at $0004). INT is unconditional -- it fires regardless of the I bit.

#### WAI -- Wait for Interrupt

Halts execution until an interrupt signal arrives. The PC is advanced past WAI before halting, so the return address always points to the next instruction.

- **NMI:** Taken immediately (vectors to $0002). RETI returns past WAI.
- **IRQ with I=0:** Taken (vectors to $0006). RETI returns past WAI.
- **IRQ with I=1:** WAI wakes and resumes at the next instruction without entering a handler (65C02-style hint behavior).

**Cycle count:** 2 (1 execute + 1 overlapped fetch, if interrupt already pending); otherwise halted until wake.

#### STP -- Stop

Halts the processor permanently. No interrupt can wake it. Only a hardware reset recovers. Both WAI and STP halt via internal clock gating, reducing dynamic power to zero.

**Cycle count:** 1 (1 execute then halt)

## Pipeline and Timing

The processor uses a 2-stage pipeline (Fetch and Execute) that overlap where possible. Most instructions take **2 cycles**. Loads and stores add cycles for bus access.

### Cycle Counts (Throughput)

Throughput is measured from one instruction boundary (SYNC) to the next:

| Instruction | Cycles | Notes |
|---|---|---|
| NOP/AUIPC/LUI/LI/ADD/SUB/AND/OR/XOR/SLT/SLTU/SLL/SRL/SRA/ADDI/ANDI/ORI/XORI/SLTI/SLTUI/XORIF/ANDIF/SLLI/SRLI/SRAI | 2 | 1 execute + 1 overlapped fetch |
| SEI/CLI | 2 | 1 execute + 1 overlapped fetch |
| BZ/BNZ (not taken) | 2 | 1 execute + 1 overlapped fetch |
| BZ/BNZ (taken, same page) | 3 | 1 execute + 2 fetch after redirect |
| BZ/BNZ (taken, page crossing) | 4 | 2 execute + 2 fetch after redirect |
| LB/LBU/LBS/LBUS/LBR/LBUR | 3 | 2 address + 1 byte read (sign/zero-extend at E_MEM_LO) |
| SB/SBS/SBR | 3 | 2 address + 1 byte written |
| LW/SW/LWS/SWS/LWR/SWR | 4 | 2 address + 2 bytes transferred |
| JR (same page) | 3 | 1 execute + 2 fetch after redirect |
| JR (page crossing) / JALR | 4 | 2 execute + 2 fetch after redirect |
| J (same page) | 3 | 1 execute + 2 fetch after redirect |
| J (page crossing) / JAL | 4 | 2 execute + 2 fetch after redirect |
| RETI | 4 | 2 execute + 2 fetch after redirect |
| INT (BRK) | 4 | 2 execute + 2 fetch after redirect |
| WAI (wake) | 2 | 1 execute + 1 overlapped fetch |
| WAI (halt) | -- | Halted until interrupt arrives |
| STP | 1 | 1 execute then halt |
| EPCR/EPCW | 2 | 1 execute + 1 overlapped fetch |
| IRQ entry | 4 | 2 execute (save EPC) + 2 fetch |
| NMI entry | 4 | 2 execute (save EPC) + 2 fetch |

Instructions that redirect (JR, JALR, J, JAL, RETI, branches taken) flush the speculative fetch and must wait for new instruction bytes. Non-redirecting instructions benefit from fetch/execute overlap.

### Self-Modifying Code

Because the fetch of the next instruction is pipelined ahead of the current instruction's memory operations, **a store is never visible to the immediately following instruction fetch**. The next instruction's bytes were already read from memory before the store was committed.

The instruction *after* that — two instructions past the store — sees the stored value, because its fetch happens during the intervening instruction's execution, by which time the store has completed.

To fence, insert any instruction (even a NOP) between the store and the modified code:

```
SB [target]     ; store writes to 'target' address
NOP             ; fence — target's fetch happens during NOP's execution
target:         ; this instruction sees the stored value
```

Without the fence, `target` would execute the *old* instruction encoding that was fetched in parallel with the store.

This also applies to word stores (SW/SWR): both bytes are written before the instruction two past the store is fetched. A single fence instruction is always sufficient.

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

When SYNC goes high, the previous instruction has retired and a new instruction has started execution.

### Single-Step Protocol

To single-step at instruction boundaries:

1. CPU runs normally with RDY high
2. Monitor SYNC during data phases -- when SYNC = 1, an instruction boundary is reached
3. Pull RDY low to halt at that boundary
4. Examine bus state while halted (address shows current fetch address)
5. Pulse RDY high for one clock cycle -- CPU advances one instruction
6. SYNC goes high again at the next boundary; pull RDY low to halt
7. Repeat from step 4

### Wait-State Protocol

For slow memory or DMA:

1. External logic decodes address during address phase (mux_sel = 0)
2. If access requires wait states, pull RDY low before the clock edge
3. Memory completes access and drives data
4. Pull RDY high -- processor continues on next clock edge

## Input Timing

All inputs (`ui_in`, `uio_in` during reads) have a 4ns setup requirement before the capturing clock edge. This applies to:

- **RDY** (`ui_in[2]`): must be stable 4ns before posedge clk
- **Data bus** (`uio_in`): must be stable 4ns before negedge clk (data phase capture)

Outputs are valid 4ns after their launching clock edge, providing 4ns of margin for external combinational logic in feedback paths.

## Code Comparison: RISCY-V02 vs 65C02

Side-by-side assembly for common routines, showing how the two ISAs compare on real code. All cycle counts assume same-page branches (the common case for tight loops). The 65C02 uses zero-page pointers; RISCY-V02 uses register arguments.

### memcpy

```c
void memcpy(void *dst, const void *src, size_t n);
```

**65C02** — arguments in zero page: src ($00), dst ($02), count ($04)

```
memcpy:
    LDY #0              ;  2 cy   2 B
    LDX count+1         ;  3 cy   2 B    ; full pages
    BEQ partial         ;  2 cy   2 B
page:
    LDA (src),Y         ;  5 cy   2 B
    STA (dst),Y         ;  6 cy   2 B
    INY                 ;  2 cy   1 B
    BNE page            ;  3 cy   2 B
    INC src+1           ;  5 cy   2 B    ; next page
    INC dst+1           ;  5 cy   2 B
    DEX                 ;  2 cy   1 B
    BNE page            ;  3 cy   2 B
partial:
    LDX count           ;  3 cy   2 B    ; remaining bytes
    BEQ done            ;  2 cy   2 B
tail:
    LDA (src),Y         ;  5 cy   2 B
    STA (dst),Y         ;  6 cy   2 B
    INY                 ;  2 cy   1 B
    DEX                 ;  2 cy   1 B
    BNE tail            ;  3 cy   2 B
done:
    RTS                 ;  6 cy   1 B
```

Inner loop (full pages): `LDA (src),Y` + `STA (dst),Y` + `INY` + `BNE` = **16 cy/byte**, 7 B

Page boundary: `INC` + `INC` + `DEX` + `BNE` = 15 cy / 256 bytes (0.06 cy/byte amortized)

Tail loop (partial page): adds `DEX` for count = **18 cy/byte**, 8 B

Total code: **28 bytes**

**RISCY-V02** — arguments in registers: R2 = dst, R3 = src, R4 = count

```
memcpy:
    ANDIF R4, 1         ;  2 cy   2 B    ; R1 = odd flag
    SRLI R4, 1          ;  2 cy   2 B    ; R4 = word count
    BZ   R4, tail       ;  2 cy   2 B
words:
    LWR  R5, R3         ;  4 cy   2 B
    SWR  R5, R2         ;  4 cy   2 B
    ADDI R3, 2          ;  2 cy   2 B
    ADDI R2, 2          ;  2 cy   2 B
    ADDI R4, -1         ;  2 cy   2 B
    BNZ  R4, words      ;  3 cy   2 B
tail:
    BZ   R1, done       ;  2 cy   2 B
    LBUR R5, R3         ;  3 cy   2 B
    SBR  R5, R2         ;  3 cy   2 B
done:
    JR   R6, 0          ;  3 cy   2 B
```

Word loop: `LWR` + `SWR` + 3×`ADDI` + `BNZ` = 17 cy / 2 bytes = **8.5 cy/byte**, 12 B

Tail: single `LBUR` + `SBR` for the trailing odd byte (if any). No page handling needed.

Total code: **26 bytes**

| | 65C02 | RISCY-V02 |
|---|---|---|
| Inner loop | 16 cy/byte | 8.5 cy/byte |
| Boundary overhead | 15 cy / 256 B | none |
| Tail | 18 cy/byte | 6 cy (1 byte) |
| Code size | 28 B | 26 B |

The 65C02's `(indirect),Y` is powerful — pointer dereference plus index in one instruction. But the 8-bit index register forces page-boundary handling that complicates the code. RISCY-V02's 16-bit pointers eliminate page handling, and 16-bit word loads/stores copy two bytes per bus transaction, nearly halving throughput cost. The structure is analogous: bulk transfer (pages vs words) with a tail for the remainder (partial page vs odd byte).

### strcpy

```c
char *strcpy(char *dst, const char *src);
```

**65C02** — arguments in zero page: src ($00), dst ($02)

```
strcpy:
    LDY #0              ;  2 cy   2 B
loop:
    LDA (src),Y         ;  5 cy   2 B
    STA (dst),Y         ;  6 cy   2 B
    BEQ done            ;  2 cy   2 B
    INY                 ;  2 cy   1 B
    BNE loop            ;  3 cy   2 B
    INC src+1           ;  5 cy   2 B    ; page crossing
    INC dst+1           ;  5 cy   2 B
    BRA loop            ;  3 cy   2 B
done:
    RTS                 ;  6 cy   1 B
```

Inner loop: `LDA` + `STA` + `BEQ` + `INY` + `BNE` = **18 cy/char**, 9 B

Page crossing: `INC` + `INC` + `BRA` = 13 cy / 256 chars (0.05 cy/char amortized)

Total code: **18 bytes**

**RISCY-V02** — arguments in registers: R2 = dst, R3 = src

```
strcpy:
    LBUR R5, R3         ;  3 cy   2 B
    SBR  R5, R2         ;  3 cy   2 B
    ADDI R3, 1          ;  2 cy   2 B
    ADDI R2, 1          ;  2 cy   2 B
    BNZ  R5, strcpy     ;  3 cy   2 B
    JR   R6, 0          ;  3 cy   2 B
```

Inner loop: `LBUR` + `SBR` + 2×`ADDI` + `BNZ` = **13 cy/char**, 10 B. No page handling.

Total code: **12 bytes**

| | 65C02 | RISCY-V02 |
|---|---|---|
| Inner loop | 18 cy/char | 13 cy/char |
| Page overhead | 13 cy / 256 chars | none |
| Code size | 18 B | 12 B |

Both versions store the byte before testing for the null terminator — the 65C02 via `BEQ` after `STA`, RISCY-V02 via `BNZ` after `SBR`. The 65C02 needs an extra `BEQ` branch (2 cycles, not taken) on every character to check for termination, plus page-crossing logic. RISCY-V02 folds the termination check into the loop's back-edge branch.

Word-copy variant (RISCY-V02 only):

```
strcpy:
    LWR  R5, R3         ;  4 cy   2 B    ; load 2 chars
    ANDIF R5, 0xFF      ;  2 cy   2 B    ; R1 = low byte
    BZ   R1, lo         ;  2 cy   2 B
    SUB  R1, R5, R1     ;  2 cy   2 B    ; R1 = high byte << 8
    BZ   R1, hi         ;  2 cy   2 B
    SWR  R5, R2         ;  4 cy   2 B    ; store 2 chars
    ADDI R3, 2          ;  2 cy   2 B
    ADDI R2, 2          ;  2 cy   2 B
    J    strcpy         ;  3 cy   2 B
lo: SBR  R5, R2         ;  3 cy   2 B    ; store null
    JR   R6, 0          ;  3 cy   2 B
hi: SWR  R5, R2         ;  4 cy   2 B    ; store char + null
    JR   R6, 0          ;  3 cy   2 B
```

Word loop: 23 cy / 2 chars = **11.5 cy/char**, 26 B. The null-byte detection (`ANDIF` + `BZ` + `SUB` + `BZ` = 8 cy) eats most of the word-load savings, so the speedup over the byte version is modest (~12%). Unlike memcpy, where word copies nearly halve throughput, strcpy's per-element null check limits the benefit.

### 16×16 → 16 Multiply

```c
uint16_t mul(uint16_t a, uint16_t b);
```

Both implementations use the same shift-and-add algorithm (GCC's `__mulsi3` pattern): shift the multiplier right one bit per iteration, conditionally add the multiplicand to the result, shift the multiplicand left, and exit early when the multiplier reaches zero.

**65C02** — arguments in zero page: mult ($00), mcand ($02), result ($04)

```
multiply:
    LDA #0              ;  2 cy   2 B
    STA result          ;  3 cy   2 B
    STA result+1        ;  3 cy   2 B
loop:
    LDA mult            ;  3 cy   2 B    ; early exit
    ORA mult+1          ;  3 cy   2 B
    BEQ done            ;  2 cy   2 B
    LSR mult+1          ;  5 cy   2 B    ; shift out bit 0
    ROR mult            ;  5 cy   2 B
    BCC no_add          ;  2.5 cy 2 B
    CLC                 ;  2 cy   1 B    ; result += mcand
    LDA result          ;  3 cy   2 B
    ADC mcand           ;  3 cy   2 B
    STA result          ;  3 cy   2 B
    LDA result+1        ;  3 cy   2 B
    ADC mcand+1         ;  3 cy   2 B
    STA result+1        ;  3 cy   2 B
no_add:
    ASL mcand           ;  5 cy   2 B    ; mcand <<= 1
    ROL mcand+1         ;  5 cy   2 B
    BRA loop            ;  3 cy   2 B
done:
    RTS                 ;  6 cy   1 B
```

Per iteration (no add): **34 cy** — `LDA`+`ORA`+`BEQ`+`LSR`+`ROR`+`BCC`(taken)+`ASL`+`ROL`+`BRA`

Per iteration (add): **54 cy** — adds `CLC` + 3×(`LDA`/`ADC`/`STA`) chain

Average: **44 cy/iter**. Total code: **36 bytes**

**RISCY-V02** — arguments in registers: R2 = multiplier, R3 = multiplicand, result in R4

```
multiply:
    LI   R4, 0          ;  2 cy   2 B
loop:
    BZ   R2, done       ;  2 cy   2 B    ; early exit
    ANDIF R2, 1         ;  2 cy   2 B    ; R1 = bit 0
    SRLI R2, 1          ;  2 cy   2 B    ; multiplier >>= 1
    BZ   R1, no_add     ;  2.5 cy 2 B
    ADD  R4, R4, R3     ;  2 cy   2 B    ; result += mcand
no_add:
    SLLI R3, 1          ;  2 cy   2 B    ; mcand <<= 1
    J    loop           ;  3 cy   2 B
done:
    JR   R6, 0          ;  3 cy   2 B
```

Per iteration (no add): **14 cy** — `BZ`+`ANDIF`+`SRLI`+`BZ`(taken)+`SLLI`+`J`

Per iteration (add): **15 cy** — adds `ADD`

Average: **14.5 cy/iter**. Total code: **18 bytes**

| | 65C02 | RISCY-V02 |
|---|---|---|
| Per iteration (avg) | 44 cy | 14.5 cy |
| 16 iterations (avg) | ~704 cy | ~232 cy |
| Code size | 36 B | 18 B |

The 3× per-iteration speedup comes from three sources: 16-bit addition is one instruction (`ADD`) vs seven (`CLC`+3×`LDA`/`ADC`/`STA`); 16-bit shifts are one instruction (`SLLI`/`SRLI`) vs two (`ASL`+`ROL`); and testing a 16-bit value for zero is one instruction (`BZ`) vs three (`LDA`+`ORA`+`BEQ`). Every 16-bit operation that the 6502 must serialize byte-by-byte collapses to a single instruction on RISCY-V02.

### 16 ÷ 16 Unsigned Division

```c
uint16_t udiv16(uint16_t dividend, uint16_t divisor);
// Returns quotient; remainder available as a byproduct.
```

Both implementations use binary long division (restoring): shift the dividend left one bit at a time into a running remainder, trial-subtract the divisor, and shift the success/fail bit into the quotient.

**65C02** — arguments in zero page: dividend ($00), divisor ($02), remainder ($04)

```
udiv16:
    LDA #0              ;  2 cy   2 B
    STA rem             ;  3 cy   2 B
    STA rem+1           ;  3 cy   2 B
    LDX #16             ;  2 cy   2 B
loop:
    ASL dividend        ;  5 cy   2 B    ; shift dividend left
    ROL dividend+1      ;  5 cy   2 B    ;   high bit → carry
    ROL rem             ;  5 cy   2 B    ; shift into remainder
    ROL rem+1           ;  5 cy   2 B
    SEC                 ;  2 cy   1 B    ; trial subtract
    LDA rem             ;  3 cy   2 B
    SBC divisor         ;  3 cy   2 B
    TAY                 ;  2 cy   1 B    ; save low result
    LDA rem+1           ;  3 cy   2 B
    SBC divisor+1       ;  3 cy   2 B
    BCC no_sub          ;  2.5 cy 2 B    ; borrow → can't subtract
    STA rem+1           ;  3 cy   2 B    ; commit subtraction
    STY rem             ;  3 cy   2 B
    INC dividend        ;  5 cy   2 B    ; set quotient bit
no_sub:
    DEX                 ;  2 cy   1 B
    BNE loop            ;  3 cy   2 B
    RTS                 ;  6 cy   1 B
```

Per iteration (no sub): **44 cy** — `ASL`+`ROL`×3+`SEC`+`LDA`+`SBC`+`TAY`+`LDA`+`SBC`+`BCC`(taken)+`DEX`+`BNE`

Per iteration (sub): **54 cy** — adds `STA`+`STY`+`INC`

Average: **49 cy/iter**. Total code: **38 bytes**

**RISCY-V02** — R2 = dividend (becomes quotient), R3 = divisor, R4 = remainder

```
udiv16:
    LI   R4, 0          ;  2 cy   2 B    ; remainder = 0
    LI   R5, 16         ;  2 cy   2 B    ; counter
loop:
    SLTI R2, 0          ;  2 cy   2 B    ; R1 = bit 15 of dividend
    SLLI R4, 1          ;  2 cy   2 B    ; remainder <<= 1
    OR   R4, R4, R1     ;  2 cy   2 B    ; shift in high bit
    SLLI R2, 1          ;  2 cy   2 B    ; dividend <<= 1
    SLTU R0, R4, R3     ;  2 cy   2 B    ; R0 = (rem < div)
    BNZ  R0, no_sub     ;  2.5 cy 2 B    ; skip if can't subtract
    SUB  R4, R4, R3     ;  2 cy   2 B    ; remainder -= divisor
    ORI  R2, 1          ;  2 cy   2 B    ; set quotient bit
no_sub:
    ADDI R5, -1         ;  2 cy   2 B    ; counter--
    BNZ  R5, loop       ;  3 cy   2 B
    JR   R6, 0          ;  3 cy   2 B
```

Per iteration (no sub): **18 cy** — `SLTI`+`SLLI`+`OR`+`SLLI`+`SLTU`+`BNZ`(taken)+`ADDI`+`BNZ`

Per iteration (sub): **21 cy** — adds `SUB`+`ORI`

Average: **19.5 cy/iter**. Total code: **26 bytes**

| | 65C02 | RISCY-V02 |
|---|---|---|
| Per iteration (avg) | 49 cy | 19.5 cy |
| 16 iterations | ~784 cy | ~312 cy |
| Code size | 38 B | 26 B |

The structure is identical — the same restoring division algorithm. The 2.5× speedup is less dramatic than multiplication's 3× because division's inner loop is dominated by shifts and a compare-subtract, which compress less: the 6502's 4-instruction shift chain (`ASL`+`ROL`×3) becomes 3 instructions (`SLTI`+`SLLI`+`OR`) since RISCY-V02 lacks a carry flag and must extract the high bit explicitly. The trial subtraction compresses better: `SEC`+`LDA`+`SBC`+`TAY`+`LDA`+`SBC` (6 instructions) becomes one `SLTU`+`SUB` (2 instructions).

### CRC-8 (SMBUS)

```c
uint8_t crc8(const uint8_t *data, uint8_t len);  // poly=0x07, init=0
```

Both use the standard bitwise algorithm: XOR each byte into the CRC, then shift left 8 times, conditionally XORing with the polynomial when the high bit shifts out.

**65C02** — ptr ($00), len ($02, 8-bit), result in A

```
crc8:
    LDA #0              ;  2 cy   2 B    crc = 0
    LDY #0              ;  2 cy   2 B    index
byte_loop:
    EOR (ptr),Y         ;  5 cy   2 B    crc ^= *data
    LDX #8              ;  2 cy   2 B
bit_loop:
    ASL A               ;  2 cy   1 B    crc <<= 1
    BCC no_xor          ;  2.5 cy 2 B
    EOR #$07            ;  2 cy   2 B    crc ^= poly
no_xor:
    DEX                 ;  2 cy   1 B
    BNE bit_loop        ;  3 cy   2 B
    INY                 ;  2 cy   1 B
    DEC len             ;  5 cy   2 B
    BNE byte_loop       ;  3 cy   2 B
    RTS                 ;  6 cy   1 B
```

Bit loop (no xor): **10 cy** — `ASL`+`BCC`(taken)+`DEX`+`BNE`

Bit loop (xor): **11 cy** — adds `EOR`

Average: **10.5 cy/bit**, 84 cy/byte bit processing. Per byte: **101 cy**. Total code: **22 bytes**

**RISCY-V02** — R2 = data ptr, R3 = len, result in R4

```
crc8:
    LI   R4, 0          ;  2 cy   2 B    crc = 0
byte_loop:
    LBUR R5, R2         ;  3 cy   2 B    R5 = *data
    XOR  R4, R4, R5     ;  2 cy   2 B    crc ^= byte
    LI   R5, 8          ;  2 cy   2 B
bit_loop:
    ANDIF R4, 0x80      ;  2 cy   2 B    R1 = bit 7
    SLLI R4, 1          ;  2 cy   2 B    crc <<= 1
    BZ   R1, no_xor     ;  2.5 cy 2 B
    XORI R4, 0x07       ;  2 cy   2 B    crc ^= poly
no_xor:
    ADDI R5, -1         ;  2 cy   2 B
    BNZ  R5, bit_loop   ;  3 cy   2 B
    ADDI R2, 1          ;  2 cy   2 B    data++
    ADDI R3, -1         ;  2 cy   2 B    len--
    BNZ  R3, byte_loop  ;  3 cy   2 B
    ANDI R4, 0xFF       ;  2 cy   2 B    mask to 8 bits
    JR   R6, 0          ;  3 cy   2 B
```

Bit loop (no xor): **12 cy** — `ANDIF`+`SLLI`+`BZ`(taken)+`ADDI`+`BNZ`

Bit loop (xor): **13 cy** — adds `XORI`

Average: **12.5 cy/bit**, 100 cy/byte bit processing. Per byte: **114 cy**. Total code: **30 bytes**

| | 65C02 | RISCY-V02 |
|---|---|---|
| Bit loop (avg) | 10.5 cy | 12.5 cy |
| Per byte | 101 cy | 114 cy |
| Code size | 22 B | 30 B |

The 65C02 wins CRC-8. The carry flag is the difference: `ASL` shifts the CRC and captures the overflow bit in one instruction; RISCY-V02 needs a separate `ANDIF` to extract bit 7 before shifting. With the CRC, byte overhead, and polynomial all fitting naturally in 8-bit operations, the 6502 plays to its strengths.

### CRC-16/CCITT

```c
uint16_t crc16(const uint8_t *data, uint8_t len);  // poly=0x1021, init=0xFFFF
```

Same bitwise algorithm, but with a 16-bit accumulator. The data byte is XORed into the high byte of the CRC.

**65C02** — ptr ($00), len ($02, 8-bit), crc ($04)

```
crc16:
    LDA #$FF            ;  2 cy   2 B    crc = 0xFFFF
    STA crc             ;  3 cy   2 B
    STA crc+1           ;  3 cy   2 B
    LDY #0              ;  2 cy   2 B
byte_loop:
    LDA crc+1           ;  3 cy   2 B    crc_hi ^= *data
    EOR (ptr),Y         ;  5 cy   2 B
    STA crc+1           ;  3 cy   2 B
    LDX #8              ;  2 cy   2 B
bit_loop:
    ASL crc             ;  5 cy   2 B    crc <<= 1
    ROL crc+1           ;  5 cy   2 B
    BCC no_xor          ;  2.5 cy 2 B
    LDA crc+1           ;  3 cy   2 B    crc ^= 0x1021
    EOR #$10            ;  2 cy   2 B
    STA crc+1           ;  3 cy   2 B
    LDA crc             ;  3 cy   2 B
    EOR #$21            ;  2 cy   2 B
    STA crc             ;  3 cy   2 B
no_xor:
    DEX                 ;  2 cy   1 B
    BNE bit_loop        ;  3 cy   2 B
    INY                 ;  2 cy   1 B
    DEC len             ;  5 cy   2 B
    BNE byte_loop       ;  3 cy   2 B
    RTS                 ;  6 cy   1 B
```

Bit loop (no xor): **18 cy** — `ASL`+`ROL`+`BCC`(taken)+`DEX`+`BNE`

Bit loop (xor): **33 cy** — adds 2×(`LDA`+`EOR`+`STA`)

Average: **25.5 cy/bit**, 204 cy/byte bit processing. Per byte: **227 cy**. Total code: **43 bytes**

**RISCY-V02** — R2 = data ptr, R3 = len, R4 = crc, R0 = polynomial

```
crc16:
    LI   R4, -1         ;  2 cy   2 B    crc = 0xFFFF
    LUI  R0, 8          ;  2 cy   2 B    R0 = 0x1000
    ORI  R0, 0x21       ;  2 cy   2 B    R0 = 0x1021
byte_loop:
    LBUR R5, R2         ;  3 cy   2 B    R5 = *data
    SLLI R5, 8          ;  2 cy   2 B    byte → high position
    XOR  R4, R4, R5     ;  2 cy   2 B    crc ^= byte << 8
    LI   R5, 8          ;  2 cy   2 B
bit_loop:
    SLTI R4, 0          ;  2 cy   2 B    R1 = bit 15
    SLLI R4, 1          ;  2 cy   2 B    crc <<= 1
    BZ   R1, no_xor     ;  2.5 cy 2 B
    XOR  R4, R4, R0     ;  2 cy   2 B    crc ^= 0x1021
no_xor:
    ADDI R5, -1         ;  2 cy   2 B
    BNZ  R5, bit_loop   ;  3 cy   2 B
    ADDI R2, 1          ;  2 cy   2 B    data++
    ADDI R3, -1         ;  2 cy   2 B    len--
    BNZ  R3, byte_loop  ;  3 cy   2 B
    JR   R6, 0          ;  3 cy   2 B
```

Bit loop (no xor): **12 cy** — `SLTI`+`SLLI`+`BZ`(taken)+`ADDI`+`BNZ`

Bit loop (xor): **13 cy** — adds `XOR`

Average: **12.5 cy/bit**, 100 cy/byte bit processing. Per byte: **116 cy**. Total code: **36 bytes**

| | 65C02 | RISCY-V02 |
|---|---|---|
| Bit loop (avg) | 25.5 cy | 12.5 cy |
| Per byte | 227 cy | 116 cy |
| Code size | 43 B | 36 B |

RISCY-V02 wins CRC-16 by ~2×. The key insight is that RISCY-V02's bit loop costs the same 12.5 cy/bit regardless of CRC width — `SLTI` extracts bit 15 just as cheaply as `ANDIF` extracts bit 7. The 6502's bit loop goes from 10.5 to 25.5 cy (2.4× slower) because every shift becomes `ASL`+`ROL` and every XOR becomes `LDA`+`EOR`+`STA` × 2. The polynomial XOR is especially painful: 1 instruction on RISCY-V02 vs 6 on the 6502.

### Raster Bar Interrupt Handler

A classic demo effect: an interrupt fires once per scanline to change the background color, producing horizontal rainbow bands. The handler increments a color byte in memory and writes it to a display register — the simplest possible useful work. Both examples target a C64-style system (VIC-II at $D000, color byte in zero page).

**Interrupt entry latency:**

Both CPUs must finish the current instruction before taking the interrupt. The average wait depends on the instruction mix of the interrupted code:

- **65C02:** Instructions take 2–7 cycles. Length-biased sampling across a typical game loop gives an average wait of **~1.5 cycles**. After the instruction completes, the hardware pushes PC and status to the stack and reads the IRQ vector: **7 cycles**.
- **RISCY-V02:** Instructions take 2–4 cycles (pipeline-visible). Average wait: **~1 cycle**. After completion, INT saves PC to EPC and fetches from the vector: **4 cycles**.

**65C02** — color byte at $02 (zero page), VIC-II at $D019/$D021

```
                                    ;  7 cy        entry: push PC+P, read vector
irq_handler:
    PHA                 ;  3 cy   1 B    save A
    INC $02             ;  5 cy   2 B    color++ (zero page RMW)
    LDA $02             ;  3 cy   2 B    load updated color
    STA $D021           ;  4 cy   3 B    set background color
    LDA #$01            ;  2 cy   2 B
    STA $D019           ;  4 cy   3 B    ack raster interrupt
    PLA                 ;  4 cy   1 B    restore A
    RTI                 ;  6 cy   1 B
```

| Phase | Cycles |
|---|---|
| Instruction wait (avg) | ~1.5 |
| Hardware entry (push+vector) | 7 |
| Register save (`PHA`) | 3 |
| Handler body | 18 |
| Register restore (`PLA`) | 4 |
| Exit (`RTI`) | 6 |
| **Total** | **~39.5** |

Total code: **15 bytes**

**RISCY-V02** — color byte at $0002 (zero page), VIC-II at $D000

Every register the handler touches must be saved and restored. The handler needs R0 (implicit base for R,8 memory ops) and R5 (scratch). The color byte is not within reach of the VIC registers, so R0 must be loaded twice — once for zero page, once for $D000.

Register saves go below the current SP without adjusting it. This is safe because RISCY-V02's IRQ entry sets I=1, masking further IRQs, and NMI handlers cannot return (RETI from NMI is undefined behavior per the architecture — NMI handlers reset, halt, or spin). Since nothing that could resume the handler will touch the stack, the space below SP is exclusively ours for the handler's lifetime.

```
                                    ;  4 cy        entry: save PC→EPC, fetch vector
irq_handler:
    SWS  R0, -4         ;  4 cy   2 B    save R0 below SP
    SWS  R5, -2         ;  4 cy   2 B    save R5 below SP
    LI   R0, 0          ;  2 cy   2 B    R0 → zero page
    LBU  R5, 2          ;  3 cy   2 B    R5 = color ($0002)
    ADDI R5, 1          ;  2 cy   2 B    color++
    SB   R5, 2          ;  3 cy   2 B    save color ($0002)
    LUI  R0, -24        ;  2 cy   2 B    R0 = $D000
    SB   R5, $21        ;  3 cy   2 B    $D021: background color
    SB   R5, $19        ;  3 cy   2 B    $D019: ack raster interrupt
    LWS  R5, -2         ;  4 cy   2 B    restore R5
    LWS  R0, -4         ;  4 cy   2 B    restore R0
    RETI                ;  4 cy   2 B
```

| Phase | Cycles |
|---|---|
| Instruction wait (avg) | ~1 |
| Hardware entry (INT+fetch) | 4 |
| Register save (`SWS`×2) | 8 |
| Handler body | 18 |
| Register restore (`LWS`×2) | 8 |
| Exit (`RETI`+fetch) | 4 |
| **Total** | **~43** |

Total code: **24 bytes**

| | 65C02 | RISCY-V02 |
|---|---|---|
| Entry (HW) | 7 cy | 4 cy |
| Insn wait (avg) | ~1.5 cy | ~1 cy |
| Save/restore | 7 cy | 16 cy |
| Handler body | 18 cy | 18 cy |
| Exit | 6 cy | 4 cy |
| **Total** | **~39.5 cy** | **~43 cy** |
| Code size | 15 B | 24 B |

The 65C02 wins. The core advantage is architectural: each 6502 instruction carries its own address (zero page or absolute), so the handler freely mixes `INC $02` (zero page) with `STA $D021` (absolute) without base register setup. RISCY-V02 must reload R0 when switching between memory regions — `LI R0, 0` for zero page, then `LUI` for $D000 — costing 4 cycles of base setup where the 6502 needs none. On top of that, the 6502's zero-page RMW (`INC $02`, 5 cy) modifies memory without a register, so the handler only clobbers A (one `PHA`/`PLA` pair, 7 cy). RISCY-V02 must save/restore two registers (16 cy) since every memory access flows through the register file.

For handlers with more useful work, RISCY-V02's save/restore is fixed while its body instructions are generally faster, so the crossover comes quickly.

### RC4 Keystream (PRGA)

RC4's pseudo-random generation algorithm — the core inner loop of the stream cipher. Each call generates one byte of keystream from a 256-byte permutation table S and two indices i, j:

```
i = (i + 1) mod 256
j = (j + S[i]) mod 256
swap(S[i], S[j])
output = S[(S[i] + S[j]) mod 256]
```

Four array-indexed operations with computed indices, a swap, and double indirection — a worst case for a load-store architecture that must compute every address through registers.

**65C02** — S at $0200 (page-aligned), i/j in zero page

```
rc4_byte:
    INC i           ; 5 cy  2 B    i = (i+1) mod 256
    LDX i           ; 3 cy  2 B    X = i
    LDA $0200,X     ; 4 cy  3 B    A = S[i]
    PHA             ; 3 cy  1 B    save S[i]
    CLC             ; 2 cy  1 B
    ADC j           ; 3 cy  2 B    A = j + S[i]
    STA j           ; 3 cy  2 B    j updated
    TAY             ; 2 cy  1 B    Y = j
    LDA $0200,Y     ; 4 cy  3 B    A = S[j]
    STA $0200,X     ; 5 cy  3 B    S[i] = S[j]
    PLA             ; 4 cy  1 B    A = old S[i]
    STA $0200,Y     ; 5 cy  3 B    S[j] = old S[i]
    CLC             ; 2 cy  1 B
    ADC $0200,X     ; 4 cy  3 B    A = S[i]+S[j] (new)
    TAY             ; 2 cy  1 B
    LDA $0200,Y     ; 4 cy  3 B    output byte
    RTS             ; 6 cy  1 B
```

**61 cycles, 34 bytes.** S[i] is saved with `PHA` before the j computation (avoiding a re-read), then restored with `PLA` for the swap. The i and j indices must live in zero page because X and Y are needed for array indexing.

**RISCY-V02** — S base in R0, i in R1, j in R2; output in R3

```
rc4_byte:
    ADDI R1, 1          ; 2 cy  2 B    i++
    ANDI R1, 0xFF       ; 2 cy  2 B    mod 256
    ADD  R3, R0, R1     ; 2 cy  2 B    R3 = &S[i]
    LBUR R4, R3         ; 3 cy  2 B    R4 = S[i]
    ADD  R2, R2, R4     ; 2 cy  2 B    j += S[i]
    ANDI R2, 0xFF       ; 2 cy  2 B    mod 256
    ADD  R3, R0, R2     ; 2 cy  2 B    R3 = &S[j]
    LBUR R5, R3         ; 3 cy  2 B    R5 = S[j]
    SBR  R4, R3         ; 3 cy  2 B    S[j] = old S[i]
    ADD  R3, R0, R1     ; 2 cy  2 B    R3 = &S[i]
    SBR  R5, R3         ; 3 cy  2 B    S[i] = old S[j]
    ADD  R3, R4, R5     ; 2 cy  2 B    R3 = S[i]+S[j]
    ANDI R3, 0xFF       ; 2 cy  2 B    mod 256
    ADD  R3, R0, R3     ; 2 cy  2 B    R3 = &S[sum]
    LBUR R3, R3         ; 3 cy  2 B    output byte
    JR   R6, 0          ; 3 cy  2 B
```

**38 cycles, 32 bytes.** Three `ANDI` instructions (6 cy) are needed for mod-256 masking that the 6502 gets for free from 8-bit registers. Five `ADD` instructions (10 cy) compute array addresses that the 6502 folds into its indexed addressing modes. Despite this 16-cycle tax, RISCY-V02 wins by a wide margin.

| | 65C02 | RISCY-V02 |
|---|---|---|
| Cycles | 61 | 38 |
| Code size | 34 B | 32 B |
| Speedup | 1.0× | 1.6× |

RISCY-V02 wins decisively. Two factors overwhelm the mod-256 and address-computation tax:

1. **Registers eliminate state traffic.** The 6502 stores i and j in zero page — every call does INC+LDX+ADC+STA (14 cy) just to read, update, and write back two index variables. RISCY-V02 keeps i, j, and the S base in registers: state overhead is a single `ADDI` (2 cy).

2. **Multiple live values avoid spills and re-reads.** The swap requires S[i] and S[j] simultaneously, but the 6502's single accumulator forces a stack spill (`PHA`/`PLA`, 7 cy). RISCY-V02 holds both values in R4 and R5, computes the final sum as `ADD R3, R4, R5`, and never touches memory for temporaries.
