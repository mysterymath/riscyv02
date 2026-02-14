# RISCY-V02 User Manual

RISCY-V02 is a 16-bit RISC processor that is a pin-compatible drop-in replacement for the WDC 65C02. It uses the same 8-bit multiplexed bus protocol, same control signals, and fits in the same Tiny Tapeout 1x2 tile. Different ISA, same socket.

## Current Status

The processor implements: **LW**, **SW**, **LB**, **LBU**, **SB**, **LW.RR**, **LB.RR**, **LBU.RR**, **SW.RR**, **SB.RR**, **LW.A**, **LB.A**, **LBU.A**, **SW.A**, **SB.A**, **JR**, **JALR**, **J**, **JAL**, **AUIPC**, **LUI**, **LI**, **BZ**, **BNZ**, **ADD**, **SUB**, **AND**, **OR**, **XOR**, **SLT**, **SLTU**, **SLL**, **SRL**, **SRA**, **ADDI**, **ANDI**, **ORI**, **XORI**, **SLTI**, **SLTUI**, **XORIF**, **SLLI**, **SRLI**, **SRAI**, **RETI**, **EPCR**, **EPCW**, **SEI**, **CLI**, **INT** (BRK), **WAI**, and **STP**. IRQ and NMI interrupt handling saves the return address to the Exception PC (EPC) register; EPCR/EPCW allow handlers to read and modify it. JAL/JALR write return addresses to R6 (the link register); subroutine return is `JR R6, 0`. Auto-modify load/store instructions (LW.A, LB.A, LBU.A, SW.A, SB.A) provide PUSH/POP semantics with zero cycle overhead vs regular load/store. R,8-format loads/stores use R0 as an implicit data register, while R,R-format loads/stores allow explicit register selection. All other opcodes are treated as NOPs (2-cycle no-ops that advance the PC).

## Comparison with Arlet 6502

Both designs target the IHP sg13g2 130nm process on a 1x2 Tiny Tapeout tile. The clock speed is pinned to match the 6502 (~62 MHz), simulating 1970s DRAM constraints where raw clock speed improvements don't matter. The comparison focuses on IPC and transistor efficiency.

| Metric | RISCY-V02 | Arlet 6502 |
|---|---|---|
| Clock period | 14 ns | 14 ns |
| fMax (slow corner) | 71.4 MHz | 71.4 MHz |
| Utilization | 62.7% | 45.3% |
| Transistor count (synth) | 17,104 | 13,176 |
| SRAM-adjusted | 13,484 | 13,176 |

RISCY-V02 supports full subroutine call/return (JAL/JALR + JR R6), PC-relative jumps (J), zero/non-zero branches (BZ/BNZ) that pair with SLT/SLTU for compare-and-branch, immediate ALU operations (ADDI, ANDI, ORI, XORI, SLTI, SLTUI, XORIF), and auto-modify load/store (LW.A, LB.A, LBU.A, SW.A, SB.A) for zero-overhead PUSH/POP. Interrupt handling saves the return address to a dedicated EPC register (accessible via EPCR/EPCW), leaving all GP registers directly accessible in the handler for software monitors and full state manipulation. The SRAM-adjusted total is within 5.5% of the 6502, with significantly more capability per transistor (16-bit registers, 3-operand instructions, 2-cycle ALU ops, PC-relative jumps, hardware call/return, immediate arithmetic/logic, auto-modify addressing).

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
| R0 | a0 | Accumulator / implicit load dest / store data (R,8 format) |
| R1 | a1 | Argument / return value 1 |
| R2 | t0 | Temporary 0 |
| R3 | t1 | Temporary 1 |
| R4 | s0 | Saved register 0 |
| R5 | s1 | Saved register 1 |
| R6 | ra | Return address (link register) |
| R7 | sp | Stack pointer |

R0 is the implicit data register for R,8-format loads and stores: loads write their result to R0, stores read their data from R0. SLTI, SLTUI, and XORIF also write their result to R0 (non-destructive compare/test patterns). R,R-format loads and stores allow explicit register selection for both data and base.

### Link Register (R6)

R6 serves as the link register. JAL and JALR write the return address (PC+2) to R6. Subroutine return is `JR R6, 0`. Since R6 is a regular GPR, it can be saved/restored with normal load/store instructions. R6 is callee-saved: any function that makes calls must save R6 on entry and restore it before returning.

R6 is a normal register in all contexts, including interrupt handlers. The interrupt return address is stored in the EPC register (see Interrupts section), not in R6. Interrupt handlers that need to use R6 (or any other register) must save and restore it manually.

## Instruction Encoding

All instructions are 16 bits. The encoding uses a **variable-width prefix-free** scheme: the prefix at the MSB determines the format and instruction, with shorter prefixes for more common instructions. Register fields are always at the LSB for fixed positions. The word `0x0000` is ADDI R0, 0 = NOP.

### Encoding Formats

| Level | Format | Layout | Instructions |
|---|---|---|---|
| 6 | R,7 | `[prefix:6\|imm7:7\|reg:3]` | LUI, AUIPC |
| 6 | "10" | `[prefix:6\|off10:10]` | J, JAL |
| 7 | R,R,R | `[prefix:7\|rd:3\|rs2:3\|rs1:3]` | ADD, SUB, AND, OR, XOR, SLT, SLTU, SLL, SRL, SRA |
| 9 | R,4 | `[prefix:9\|shamt:4\|reg:3]` | SLLI, SRLI, SRAI |
| 10 | R,R | `[prefix:10\|rd:3\|rs:3]` | LW.RR, LB.RR, LBU.RR, SW.RR, SB.RR, LW.A, LB.A, LBU.A, SW.A, SB.A |
| 10+ | System | `[prefix:10\|sub:6]` | SEI, CLI, RETI, INT, WAI, STP |

### Bit Layout

```
R,7:   [prefix:6 @ 15:10] [imm7:7 @ 9:3]     [reg:3 @ 2:0]
"10":  [prefix:6 @ 15:10] [off10:10 @ 9:0]
R,R,R: [prefix:7 @ 15:9]  [rd:3 @ 8:6] [rs2:3 @ 5:3] [rs1:3 @ 2:0]
R,4:   [prefix:9 @ 15:7]  [shamt:4 @ 6:3]    [reg:3 @ 2:0]
R,R:   [prefix:10 @ 15:6] [rd:3 @ 5:3]       [rs:3 @ 2:0]
System:[prefix:10 @ 15:6] [sub:6 @ 5:0]
```

### Prefix Table

```
--- R,8 format (5-bit prefix) ---
00000   ADDI    rd = rd + sext(imm8)
00001   LI      rd = sext(imm8)
00010   LW      R0 = mem16[rs + sext(imm8)]
00011   LB      R0 = sext(mem[rs + sext(imm8)])
00100   LBU     R0 = zext(mem[rs + sext(imm8)])
00101   SW      mem16[rs + sext(imm8)] = R0
00110   SB      mem[rs + sext(imm8)] = R0[7:0]
00111   JR      pc = rs + sext(imm8) << 1
01000   JALR    R6 = pc+2; pc = rs + sext(imm8) << 1


01001   ANDI    rd = rd & zext(imm8)
01010   ORI     rd = rd | zext(imm8)
01011   XORI    rd = rd ^ zext(imm8)
01100   SLTI    R0 = (rs < sext(imm8)) ? 1 : 0   (signed)
01101   SLTUI   R0 = (rs <u sext(imm8)) ? 1 : 0  (unsigned)
01110   BZ      if rs == 0, pc += sext(imm8) << 1
01111   BNZ     if rs != 0, pc += sext(imm8) << 1
10000   XORIF   R0 = rs ^ zext(imm8)

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
1111010110  LW.RR    rd = mem16[rs]
1111010111  LB.RR    rd = sext(mem[rs])
1111011000  LBU.RR   rd = zext(mem[rs])
1111011001  SW.RR    mem16[rs] = rd
1111011010  SB.RR    mem[rs] = rd[7:0]
1111011011  LW.A     rd = mem16[rs]; rs += 2
1111011100  LB.A     rd = sext(mem[rs]); rs += 1
1111011101  LBU.A    rd = zext(mem[rs]); rs += 1
1111011110  SW.A     rs -= 2; mem16[rs] = rd
1111011111  SB.A     rs -= 1; mem[rs] = rd[7:0]

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

`R0 = MEM16[rs + sext(imm8)]` -- 4 cycles

Loads a 16-bit word from memory into R0. The 8-bit signed offset is a byte offset (not scaled), giving a range of -128 to +127 bytes from the base register. The low byte is read first, then the high byte.

#### LB -- Load Byte (Sign-Extend)

`R0 = sext(MEM[rs + sext(imm8)])` -- 4 cycles

Loads a single byte and sign-extends it to 16 bits into R0. If bit 7 is set, the high byte is filled with 0xFF; otherwise 0x00.

#### LBU -- Load Byte (Zero-Extend)

`R0 = zext(MEM[rs + sext(imm8)])` -- 4 cycles

Loads a single byte and zero-extends it to 16 bits into R0. The high byte is always 0x00.

#### SW -- Store Word

`MEM16[rs + sext(imm8)] = R0` -- 4 cycles

Stores R0 as a 16-bit word to memory. The low byte is written first, then the high byte.

#### SB -- Store Byte

`MEM[rs + sext(imm8)] = R0[7:0]` -- 3 cycles

Stores the low byte of R0 to memory.

#### JR -- Jump Register

`PC = rs + sext(imm8) << 1` -- 4 cycles

Unconditional jump to a register plus a scaled signed offset. The 8-bit offset is shifted left by 1, giving a range of -256 to +254 bytes from the register value.

#### JALR -- Jump and Link Register

`R6 = PC+2; PC = rs + sext(imm8) << 1` -- 4 cycles

Register-indirect jump that saves the return address in R6. Pairs with AUIPC for full 16-bit PC-relative function calls: `AUIPC t0, upper; JALR t0, lower`.


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

`R0 = (rs < sext(imm8)) ? 1 : 0` -- 2 cycles

Compares the source register against a sign-extended 8-bit immediate (-128 to +127) as signed integers. The result (0 or 1) is written to R0, preserving the source register. Pattern: `SLTI rs, val; BNZ R0, target` (branch if rs < val).

#### SLTUI -- Set Less Than Immediate (Unsigned)

`R0 = (rs <u sext(imm8)) ? 1 : 0` -- 2 cycles

Compares the source register against a sign-extended 8-bit immediate as unsigned integers. The immediate is sign-extended then treated as unsigned. The result is written to R0.

#### BZ -- Branch if Zero

`if rs == 0: PC += sext(off8) << 1` -- 2 cycles (not taken) / 4 cycles (taken)

Branches to a PC-relative target if the source register is zero. The 8-bit signed offset is shifted left by 1, giving a range of -256 to +254 bytes from the next instruction address. Pairs with SLT/SLTU for compare-and-branch: `SLT t, a, b; BZ t, target` (branch if NOT less than).

#### BNZ -- Branch if Non-Zero

`if rs != 0: PC += sext(off8) << 1` -- 2 cycles (not taken) / 4 cycles (taken)

Branches to a PC-relative target if the source register is non-zero. Pairs with SLT/SLTU: `SLT t, a, b; BNZ t, target` (branch if less than).

#### XORIF -- Xor Immediate (Fixed-Destination)

`R0 = rs ^ zext(imm8)` -- 2 cycles

Bitwise XOR of the source register with a zero-extended 8-bit immediate, writing the result to R0 while preserving the source register. Useful for equality testing: if rs equals zext(imm8), R0 will be zero. Pattern: `XORIF rs, val; BZ R0, equal_label`.

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

R,R-format loads and stores use explicit registers for both data and base, with no offset. Auto-modify variants (.A) adjust the base register automatically.

#### LW.RR -- `rd = MEM16[rs]` -- 4 cycles
#### LB.RR -- `rd = sext(MEM[rs])` -- 4 cycles
#### LBU.RR -- `rd = zext(MEM[rs])` -- 4 cycles
#### SW.RR -- `MEM16[rs] = rd` -- 4 cycles
#### SB.RR -- `MEM[rs] = rd[7:0]` -- 3 cycles

#### LW.A -- Load Word, Post-Increment

`rd = MEM16[rs]; rs += 2` -- 4 cycles

Loads a 16-bit word from the address in rs, then increments rs by 2 (word size). POP idiom: `LW.A rd, (sp)` pops a word from the stack. When rd and rs are the same register, the loaded value overwrites the incremented pointer.

#### LB.A -- Load Byte (Sign-Extend), Post-Increment

`rd = sext(MEM[rs]); rs += 1` -- 4 cycles

#### LBU.A -- Load Byte (Zero-Extend), Post-Increment

`rd = zext(MEM[rs]); rs += 1` -- 4 cycles

#### SW.A -- Store Word, Pre-Decrement

`rs -= 2; MEM16[rs] = rd` -- 4 cycles

Decrements rs by 2, then stores rd to the new address. PUSH idiom: `SW.A rd, (sp)` pushes a word onto the stack. Pairs with LW.A for stack operations.

#### SB.A -- Store Byte, Pre-Decrement

`rs -= 1; MEM[rs] = rd[7:0]` -- 3 cycles

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

**Cycle count:** 2 (if interrupt already pending); otherwise halted until wake.

#### STP -- Stop

Halts the processor permanently. No interrupt can wake it. Only a hardware reset recovers. Both WAI and STP halt via internal clock gating, reducing dynamic power to zero.

**Cycle count:** 1 (execute then halt)

## Pipeline and Timing

The processor uses a 2-stage pipeline (Fetch and Execute) that overlap where possible. Most instructions take **2 cycles**. Loads and stores add cycles for bus access.

### Cycle Counts (Throughput)

Throughput is measured from one instruction boundary (SYNC) to the next:

| Instruction | Cycles | Notes |
|---|---|---|
| NOP/SEI/CLI/AUIPC/LUI/LI/ADD/SUB/AND/OR/XOR/SLT/SLTU/SLL/SRL/SRA/ADDI/ANDI/ORI/XORI/SLTI/SLTUI/XORIF/SLLI/SRLI/SRAI | 2 | 1 execute + 1 overlapped fetch |
| BZ/BNZ (not taken) | 2 | 1 execute + 1 overlapped fetch |
| BZ/BNZ (taken) | 4 | 2 execute + 2 fetch after redirect |
| LB/LBU/LB.RR/LBU.RR/LB.A/LBU.A | 4 | 2 address + 1 byte read + 1 extension |
| SB/SB.RR/SB.A | 3 | 2 address + 1 byte written |
| LW/SW/LW.RR/SW.RR/LW.A/SW.A | 4 | 2 address + 2 bytes transferred |
| JR/JALR | 4 | 2 execute + 2 fetch after redirect |
| J/JAL | 4 | 2 execute + 2 fetch after redirect |
| RETI | 4 | 2 execute + 2 fetch after redirect |
| INT (BRK) | 4 | 2 execute + 2 fetch after redirect |
| WAI (wake) | 2 | If interrupt already pending |
| WAI (halt) | -- | Halted until interrupt arrives |
| STP | 1 | Dispatch directly to halt |
| EPCR/EPCW | 2 | 1 execute + 1 overlapped fetch |
| IRQ entry | 4 | 2 execute (save EPC) + 2 fetch |
| NMI entry | 4 | 2 execute (save EPC) + 2 fetch |

Instructions that redirect (JR, JALR, J, JAL, RETI, branches taken) flush the speculative fetch and must wait for new instruction bytes. Non-redirecting instructions benefit from fetch/execute overlap.

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
