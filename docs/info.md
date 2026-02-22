<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

RISCY-V02 is a 16-bit RISC processor that is a pin-compatible drop-in replacement for the WDC 65C02. It uses the same 8-bit multiplexed bus protocol, same control signals, and fits in the same Tiny Tapeout 1x2 tile. Different ISA, same socket. See [Architecture](#architecture) and [Instruction Set](#instruction-set) below.

## How to test

Connect to an external SRAM via the TT mux/demux bus protocol (active clock edge alternates between address output and data transfer). Control inputs: IRQB (active-low), NMIB (active-low edge-triggered), RDY (active-high). See [Bus Protocol](#bus-protocol), [RDY and SYNC Signals](#rdy-and-sync-signals), and [Input Timing](#input-timing) below.

## External hardware

A 32Kx8 asynchronous SRAM (e.g. IS61C256AL-10), two 74HCT573 address latches, a 74LVC245 data bus transceiver, and a 74HCT00 quad NAND for glue logic. See [SRAM PCB Interface Design](#sram-pcb-interface-design) below for the full schematic and timing analysis.

## Comparison with 6502

The comparison baseline is a 6502 implementation based on [Arlet Ottens' open-source 6502 core](https://github.com/Arlet/verilog-6502), wrapped for the same TT mux/demux bus protocol and synthesized on the same IHP sg13g2 130nm process and 1x2 Tiny Tapeout tile. The clock speed is pinned to match the 6502's maximum (~71 MHz), simulating 1970s DRAM constraints where raw clock speed improvements don't matter. The comparison focuses on IPC and transistor efficiency.

| Metric | RISCY-V02 | 6502 |
|---|---|---|
| Clock period | 14 ns | 14 ns |
| fMax (slow corner) | 71.4 MHz | 71.4 MHz |
| Utilization | 62.9% | 48.5% |
| Transistor count (synth) | 16,682 | 13,176 |
| SRAM-adjusted | 13,298 | 13,176 |

The SRAM-adjusted total is within 1% of the 6502, with significantly more capability per transistor: 16-bit registers, 3-operand ALU instructions, 2-cycle execute, PC-relative jumps, hardware call/return, instantaneous interrupts, and immediate arithmetic/logic. Unrecognized opcodes are treated as NOPs (2-cycle no-ops that advance the PC).

## Bus Protocol

RISCY-V02 uses the same TT mux/demux bus protocol as the 6502 comparison model. The active clock edge alternates between address output and data transfer using a dual-edge mux select signal.

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
- **T flag**: single-bit condition flag, set by comparisons (CLT, CLTU, CEQ, CLTI, CLTUI, CEQI), tested by BT/BF branches
- **I flag**: interrupt disable (1 = disabled)
- **ESR**: 2-bit exception status register {I, T}, saved on interrupt entry, restored by RETI
- **EPC**: 16-bit exception PC, saved on interrupt entry
- **16-bit address space**, byte-addressable, little-endian
- **Fixed 16-bit instructions**, fetched low byte first
- **2-stage pipeline**: Fetch and Execute with speculative fetch and redirect

### Reset

On reset:
- PC is set to $0000 and execution begins
- I (interrupt disable) is set to 1 -- interrupts are disabled
- T (condition flag) is cleared to 0
- ESR is set to {I=1, T=0}
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

**Instantaneous dispatch:** All interrupt entry (IRQ, NMI, BRK) is handled at dispatch time in a single cycle. When the FSM is ready (instruction completing or idle), the hardware saves EPC and ESR, sets I=1, and redirects the PC to the vector address -- no execute cycles needed. The 2-cycle vector fetch is the only latency. BRK (INT with vector 1) is handled identically at instruction dispatch. Since all three share the same INT encoding format, software can also trigger IRQ/NMI vectors directly.

**IRQ entry (when IRQB=0 and I=0):**
1. Complete the current instruction
2. Save ESR = {I, T} -- status flags at interrupt entry
3. Save EPC = next_PC -- clean 16-bit return address
4. Set I = 1 -- disable further interrupts
5. Jump to $0006

**NMI entry (on NMIB falling edge, regardless of I):**
1. Complete the current instruction
2. Save ESR = {I, T} -- overwrites any previous ESR
3. Save EPC = next_PC -- overwrites any previous EPC
4. Set I = 1 -- disable IRQs
5. Jump to $0002

**BRK entry (unconditional, regardless of I):**
1. Save ESR = {I, T}
2. Save EPC = PC+2 -- return address
3. Set I = 1 -- disable IRQs
4. Jump to $0004

NMI is edge-triggered: only one NMI fires per falling edge. Holding NMIB low does not re-trigger. NMIB must return high and fall again for a new NMI. NMI has priority over IRQ; if both are pending simultaneously, NMI is taken first, and the subsequent I=1 masks the IRQ.

**Warning:** RETI from an NMI handler is undefined behavior. NMI overwrites EPC and ESR unconditionally, so if an NMI interrupts an IRQ handler before it saves EPC/ESR (via EPCR/SRR), the IRQ's return state is lost. NMI handlers typically reset, halt, or spin.

**Interrupt return (RETI instruction):**
1. Restore {I, T} from ESR
2. Jump to EPC

**Exception state:** EPC is a standalone 16-bit register holding the clean return address. ESR is a 2-bit register holding {I, T} at the time of interrupt entry. Neither is directly addressable through normal register fields. EPC is accessible through EPCR/EPCW; ESR is accessible through SRR/SRW (which read/write the live SR = {I, T}, including ESR on interrupt entry). All GP registers (R0-R7) are directly accessible in interrupt context -- there is no register banking.

**I-bit forwarding:** SEI, CLI, SRW, and RETI all take effect immediately at the next instruction boundary. There is no one-instruction delay: if CLI or SRW clears I while IRQB is asserted, the IRQ fires before the next instruction executes.

**Interrupt latency:** 2 cycles from instruction completion to first handler instruction fetch (instantaneous dispatch + 2-cycle vector fetch). NMI edge detection is combinational -- if the falling edge arrives on the same cycle that the FSM is ready, the NMI is taken immediately with no additional detection delay.

### Register Naming Convention

| Register | Name | Suggested Purpose |
|---|---|---|
| R0 | a0 | Accumulator / implicit base address (I-type loads/stores) |
| R1 | a1 | Argument / scratch |
| R2 | t0 | Temporary 0 |
| R3 | t1 | Temporary 1 |
| R4 | s0 | Saved register 0 |
| R5 | s1 | Saved register 1 |
| R6 | ra | Return address (link register) |
| R7 | sp | Stack pointer |

R0 is the implicit base address register for I-type loads and stores: the effective address is `R0 + sext(imm8)`, and `ir[7:5]` selects the data register. This is the same convention as R7-based SP-relative instructions, but using R0 as the base. Comparisons (CLTI, CLTUI, CEQI, CLT, CLTU, CEQ) write to the T flag rather than a destination register, preserving all GPRs. R-type loads and stores allow explicit register selection for both data and base, with no offset.

### Link Register (R6)

R6 serves as the link register. JAL and JALR write the return address (PC+2) to R6. Subroutine return is `JR R6, 0`. Since R6 is a regular GPR, it can be saved/restored with normal load/store instructions. R6 is callee-saved: any function that makes calls must save R6 on entry and restore it before returning.

R6 is a normal register in all contexts, including interrupt handlers. The interrupt return address is stored in the EPC register (see Interrupts section), not in R6. Interrupt handlers that need to use R6 (or any other register) must save and restore it manually.

## Instruction Encoding

All instructions are 16 bits. The encoding follows RISC-V principles: **fixed 5-bit opcode at [4:0]**, register at [7:5], sign bit always at [15], immediates at the top. The word `0x0000` is ADDI R0, 0 = NOP. All immediates are sign-extended (including ANDI/ORI/XORI), same as RISC-V.

### Encoding Overview

**61 instructions defined.**

| Format | Layout (MSB→LSB) | Used |
|---|---|---|
| I | `[imm8:8\|rs/rd:3\|opcode:5]` | 24 |
| B | `[imm8:8\|funct3:3\|opcode:5]` | 2 |
| J | `[s:1\|imm[6:0]:7\|imm[8:7]:2\|fn1:1\|opcode:5]` | 2 |
| R | `[fn2:2\|rd:3\|rs2:3\|rs1:3\|opcode:5]` | 16 |
| SI | `[fn2:2\|fn4:2\|shamt:4\|rs/rd:3\|opcode:5]` | 7 |
| SYS | `[sub:8\|reg:3\|opcode:5]` | 10 |

Fields are packed MSB-first: opcode at bottom, immediates at top. The sign bit is always ir[15] in every format, enabling sign extension in parallel with decode. The primary register field ir[7:5] is shared across I-type, SI-type, SYS, and R-type (as rs1), enabling speculative register file reads before format decode completes. In R-type, rs2 is at [10:8] and rd at [13:11].

### Opcode Table

```
--- I-type (opcode 0-23) ---
00000 (0)   ADDI    rd = rd + sext(imm8)
00001 (1)   LI      rd = sext(imm8)
00010 (2)   LW      rd = mem16[R0 + sext(imm8)]
00011 (3)   LB      rd = sext(mem[R0 + sext(imm8)])
00100 (4)   LBU     rd = zext(mem[R0 + sext(imm8)])
00101 (5)   SW      mem16[R0 + sext(imm8)] = rs
00110 (6)   SB      mem[R0 + sext(imm8)] = rs[7:0]
00111 (7)   JR      pc = rs + sext(imm8)
01000 (8)   JALR    rs = pc+2; pc = rs + sext(imm8)
01001 (9)   ANDI    rd = rd & sext(imm8)
01010 (10)  ORI     rd = rd | sext(imm8)
01011 (11)  XORI    rd = rd ^ sext(imm8)
01100 (12)  CLTI    T = (rs < sext(imm8))             (signed)
01101 (13)  CLTUI   T = (rs <u sext(imm8))            (unsigned)
01110 (14)  BZ      if rs == 0, pc += sext(imm8) << 1
01111 (15)  BNZ     if rs != 0, pc += sext(imm8) << 1
10000 (16)  CEQI    T = (rs == sext(imm8))
10001 (17)  LWS     rd = mem16[R7 + sext(imm8)]
10010 (18)  LBS     rd = sext(mem[R7 + sext(imm8)])
10011 (19)  LBUS    rd = zext(mem[R7 + sext(imm8)])
10100 (20)  SWS     mem16[R7 + sext(imm8)] = rs
10101 (21)  SBS     mem[R7 + sext(imm8)] = rs[7:0]
10110 (22)  LUI     rd = imm8 << 8
10111 (23)  AUIPC   rd = pc + (imm8 << 8)

--- B-type (opcode 24, funct3 at [7:5]) ---
11000.000   BT      if T == 1, pc += sext(imm8) << 1
11000.001   BF      if T == 0, pc += sext(imm8) << 1

--- J-type (opcode 25, fn1 at [5]) ---
11001.0     J       pc += sext(imm10) << 1
11001.1     JAL     R6 = pc+2; pc += sext(imm10) << 1

--- R-type (opcodes 26-29, fn2 at [15:14]) ---
Opcode 26 (R-ALU1): 00=ADD, 01=SUB, 10=AND, 11=OR
Opcode 27 (R-ALU2): 00=XOR, 01=SLL, 10=SRL, 11=SRA
Opcode 28 (R-MEM):  00=LWR, 01=LBR, 10=LBUR, 11=SWR
Opcode 29 (R-MISC): 00=SBR, 01=CLT, 10=CLTU, 11=CEQ

--- SI-type (opcode 30, fn2 at [15:14]) ---
11110.00    SLLI    rd = rd << shamt
11110.01    SRLI    rd = rd >>u shamt
11110.10    SRAI    rd = rd >>s shamt
11110.11    Shift/rotate through T (fn4 at [13:12]):
  fn4=00    SLLT    T = rd[15]; rd <<= 1
  fn4=01    SRLT    T = rd[0];  rd >>= 1
  fn4=10    RLT     T = rd[15]; rd = {rd[14:0], old_T}
  fn4=11    RRT     T = rd[0];  rd = {old_T, rd[15:1]}

--- SYS-type (opcode 31, sub8 at [15:8]) ---
sub8=0x01   SEI     I = 1
sub8=0x02   CLI     I = 0
sub8=0x03   RETI    {I, T} = ESR; pc = EPC
sub8=0x05   WAI     halt until interrupt
sub8=0x07   STP     halt permanently (reset only)
sub8=0x08   SRW     {I, T} = rs[1:0]           (reg at [7:5])
sub8=0x10   EPCR    rd = EPC                    (reg at [7:5])
sub8=0x18   EPCW    EPC = rs                    (reg at [7:5])
sub8=0x28   SRR     rd = {14'b0, I, T}          (reg at [7:5])
sub8=0xC0+  INT     ESR={I,T}; EPC=pc+2; I=1; pc=(vec+1)*2  (vec at [7:6])

All other encodings execute as NOP (2-cycle no-op).
```

## Instruction Set

### I-type -- Loads, Stores, Immediate, Jumps

#### ADDI -- Add Immediate

`rd = rd + sext(imm8)` -- 2 cycles

Adds a sign-extended 8-bit immediate (-128 to +127) to the destination register. `ADDI R0, 0` (encoding `0x0000`) is the canonical NOP. Useful for stack pointer adjustments and small constant additions. Pairs with LUI for full 16-bit constant loading: `LUI rd, hi; ADDI rd, lo`.

#### LI -- Load Immediate

`rd = sext(imm8)` -- 2 cycles

Loads a sign-extended 8-bit immediate (-128 to +127) into a register. No memory access or register read needed.

#### LW -- Load Word

`rd = MEM16[R0 + sext(imm8)]` -- 4 cycles

Loads a 16-bit word from memory into the register at ir[7:5]. R0 is the implicit base address; the 8-bit signed offset is a byte offset (not scaled), giving a range of -128 to +127 bytes from R0. The low byte is read first, then the high byte.

#### LB -- Load Byte (Sign-Extend)

`rd = sext(MEM[R0 + sext(imm8)])` -- 3 cycles

Loads a single byte and sign-extends it to 16 bits into the register at ir[7:5]. R0 is the implicit base. If bit 7 is set, the high byte is filled with 0xFF; otherwise 0x00.

#### LBU -- Load Byte (Zero-Extend)

`rd = zext(MEM[R0 + sext(imm8)])` -- 3 cycles

Loads a single byte and zero-extends it to 16 bits into the register at ir[7:5]. R0 is the implicit base. The high byte is always 0x00.

#### SW -- Store Word

`MEM16[R0 + sext(imm8)] = rs` -- 4 cycles

Stores the register at ir[7:5] as a 16-bit word to memory. R0 is the implicit base address. The low byte is written first, then the high byte.

#### SB -- Store Byte

`MEM[R0 + sext(imm8)] = rs[7:0]` -- 3 cycles

Stores the low byte of the register at ir[7:5] to memory. R0 is the implicit base address.

#### JR -- Jump Register

`PC = rs + sext(imm8)` -- 3-4 cycles

Unconditional jump to a register plus a signed byte offset. The 8-bit offset gives a range of -128 to +127 bytes from the register value.

#### JALR -- Jump and Link Register

`rs = PC+2; PC = rs + sext(imm8)` -- 4 cycles

Register-indirect jump that saves the return address in the source register. In I-type, the single register field serves as both jump base and link destination. The conventional call sequence uses R6: `JALR R6, offset` reads the jump target from R6, then writes the return address back to R6. Pairs with AUIPC for full 16-bit PC-relative function calls: `AUIPC t0, upper; JALR t0, lower`.


#### ANDI -- And Immediate

`rd = rd & sext(imm8)` -- 2 cycles

Bitwise AND with a sign-extended 8-bit immediate (-128 to +127). With a positive immediate (0-127), masks the low 7 bits and clears the high byte. With a negative immediate, masks the low byte and preserves the high byte. Note: `ANDI rd, 0xFF` is a no-op (sign-extends to 0xFFFF); use `LBU` to extract a low byte instead.

#### ORI -- Or Immediate

`rd = rd | sext(imm8)` -- 2 cycles

Bitwise OR with a sign-extended 8-bit immediate. With a positive immediate, sets bits in the low byte without affecting the high byte. With a negative immediate, sets all high-byte bits.

#### XORI -- Xor Immediate

`rd = rd ^ sext(imm8)` -- 2 cycles

Bitwise XOR with a sign-extended 8-bit immediate. With a positive immediate, toggles bits in the low byte without affecting the high byte. With a negative immediate (`XORI rd, -1`), inverts all 16 bits (bitwise NOT).

#### CLTI -- Compare Less Than Immediate (Signed)

`T = (rs < sext(imm8))` -- 2 cycles

Compares the source register against a sign-extended 8-bit immediate (-128 to +127) as signed integers. Sets T=1 if less, T=0 otherwise. No register is modified. Pattern: `CLTI rs, val; BT target` (branch if rs < val).

#### CLTUI -- Compare Less Than Immediate (Unsigned)

`T = (rs <u sext(imm8))` -- 2 cycles

Compares the source register against a sign-extended 8-bit immediate as unsigned integers. The immediate is sign-extended then treated as unsigned. Sets T=1 if less, T=0 otherwise.

#### BZ -- Branch if Zero

`if rs == 0: PC += sext(imm8) << 1` -- 2 cycles (not taken) / 3-4 cycles (taken)

Branches to a PC-relative target if the source register is zero. The 8-bit signed offset is shifted left by 1, giving a range of -256 to +254 bytes from the next instruction address. Tests the full 16-bit register value. Useful for loop counters and null-pointer checks.

#### BNZ -- Branch if Non-Zero

`if rs != 0: PC += sext(imm8) << 1` -- 2 cycles (not taken) / 3-4 cycles (taken)

Branches to a PC-relative target if the source register is non-zero. Useful for loop counters: `ADDI rd, -1; BNZ rd, loop`.

#### CEQI -- Compare Equal Immediate

`T = (rs == sext(imm8))` -- 2 cycles

Compares the source register against a sign-extended 8-bit immediate (-128 to +127) for equality. Sets T=1 if equal, T=0 otherwise. No register is modified. Pattern: `CEQI rs, val; BT equal_label` (branch if rs == val).

### B-type -- T-Flag Branches

BT and BF use opcode 24 with funct3 at ir[7:5]. They branch based on the T flag set by comparison instructions.

#### BT -- Branch if T Set

`if T == 1: PC += sext(imm8) << 1` -- 2 cycles (not taken) / 3-4 cycles (taken)

Branches if T=1. Same-page taken: 3 cycles; page-crossing: 4 cycles. Pattern: `CLTI rs, val; BT target` (branch if rs < val). `CLT rs1, rs2; BT target` (branch if rs1 < rs2). `CEQI rs, val; BT target` (branch if rs == val).

#### BF -- Branch if T Clear

`if T == 0: PC += sext(imm8) << 1` -- 2 cycles (not taken) / 3-4 cycles (taken)

Branches if T=0. Pattern: `CLTU rs1, rs2; BF target` (branch if rs1 >= rs2). `CLTI rs, val; BF target` (branch if rs >= val).

### I-type -- Upper Immediate

#### LUI -- Load Upper Immediate

`rd = imm8 << 8` -- 2 cycles

Loads an 8-bit immediate into the upper byte of a register, clearing the low byte. The immediate range covers the full 16-bit address space (any upper byte). Pairs with ADDI for full 16-bit constant loading: `LUI rd, hi; ADDI rd, lo`. When the low byte is negative (bit 7 set), compensate the upper byte by adding 1, same as RISC-V's LUI+ADDI convention.

#### AUIPC -- Add Upper Immediate to PC

`rd = (PC+2) + (imm8 << 8)` -- 2 cycles

Adds an 8-bit immediate, placed in the upper byte, to the address of the next instruction (PC+2). Pairs with LW/SW/JR's offset for PC-relative addressing: AUIPC provides the upper bits and the subsequent load/store/jump provides the lower bits.

### J-type -- PC-Relative Jumps

#### J -- Jump

`PC += sext(imm10) << 1` -- 3-4 cycles

Unconditional PC-relative jump. The 10-bit signed offset is shifted left by 1, giving a range of -1024 to +1022 bytes from the next instruction address.

#### JAL -- Jump and Link

`R6 = PC+2; PC += sext(imm10) << 1` -- 4 cycles

Unconditional PC-relative jump that saves the return address in R6. Used for subroutine calls; return with `JR R6, 0`.

### R-type -- Register ALU

All R-type ALU instructions are 2 cycles. rd at ir[13:11], rs2 at ir[10:8], rs1 at ir[7:5].

#### ADD -- `rd = rs1 + rs2`
#### SUB -- `rd = rs1 - rs2`
#### AND -- `rd = rs1 & rs2`
#### OR -- `rd = rs1 | rs2`
#### XOR -- `rd = rs1 ^ rs2`

#### SLL -- Shift Left Logical

`rd = rs1 << rs2[3:0]`

Shifts rs1 left by the amount in rs2 (low 4 bits, range 0-15). Vacated bits are filled with zeros.

#### SRL -- Shift Right Logical

`rd = rs1 >>u rs2[3:0]`

Shifts rs1 right by the amount in rs2 (low 4 bits). Vacated bits are filled with zeros.

#### SRA -- Shift Right Arithmetic

`rd = rs1 >>s rs2[3:0]`

Shifts rs1 right by the amount in rs2 (low 4 bits). Vacated bits are filled with copies of the sign bit (rs1[15]).

### SI-type -- Shift Immediate

All shift immediate instructions are 2 cycles and operate in-place (rd = rd shift shamt).

#### SLLI -- `rd = rd << shamt` (shamt 0-15)
#### SRLI -- `rd = rd >>u shamt` (shamt 0-15)
#### SRAI -- `rd = rd >>s shamt` (shamt 0-15)

### SI-type -- Shift/Rotate Through T

All shift/rotate-through-T instructions are 2 cycles, shift by exactly 1 bit, and capture the shifted-out bit into T. Designed for bit-at-a-time algorithms (CRC, long division, multiply). The shamt field is reserved and must be encoded as 1.

#### SLLT -- Shift Left Logical through T

`T = rd[15]; rd = {rd[14:0], 0}` -- 2 cycles

Shifts rd left by 1. The old bit 15 (shifted out) is captured in T. Bit 0 is filled with 0. Extracts the sign bit and shifts in a single instruction, useful for sign-based algorithms and multi-word shifts.

#### SRLT -- Shift Right Logical through T

`T = rd[0]; rd = {0, rd[15:1]}` -- 2 cycles

Shifts rd right by 1. The old bit 0 (shifted out) is captured in T. Bit 15 is filled with 0.

#### RLT -- Rotate Left through T

`T = rd[15]; rd = {rd[14:0], old_T}` -- 2 cycles

Shifts rd left by 1. The old bit 15 is captured in T. Bit 0 is filled with the previous T value. This creates a 17-bit rotate path (16-bit register + T), equivalent to the 6502's ROL instruction. Chaining RLT across two registers shifts a bit from one into the other.

#### RRT -- Rotate Right through T

`T = rd[0]; rd = {old_T, rd[15:1]}` -- 2 cycles

Shifts rd right by 1. The old bit 0 is captured in T. Bit 15 is filled with the previous T value. This creates a 17-bit rotate path, equivalent to the 6502's ROR instruction.

### R-type -- Register Load/Store and Compare

R-type loads and stores use explicit registers for both data and base, with no offset. For loads, rd at ir[13:11] is the destination and rs1 at ir[7:5] is the address. For stores, rs2 at ir[10:8] is the data and rs1 at ir[7:5] is the address.

#### LWR -- `rd = MEM16[rs1]` -- 4 cycles
#### LBR -- `rd = sext(MEM[rs1])` -- 3 cycles
#### LBUR -- `rd = zext(MEM[rs1])` -- 3 cycles
#### SWR -- `MEM16[rs1] = rs2` -- 4 cycles
#### SBR -- `MEM[rs1] = rs2[7:0]` -- 3 cycles

#### CLT -- Compare Less Than (Signed)

`T = (rs1 < rs2)` -- 2 cycles

Compares rs1 and rs2 as signed 16-bit integers. Sets T=1 if rs1 < rs2, T=0 otherwise. No register is modified. Pattern: `CLT a, b; BT target` (branch if a < b signed).

#### CLTU -- Compare Less Than (Unsigned)

`T = (rs1 <u rs2)` -- 2 cycles

Compares rs1 and rs2 as unsigned 16-bit integers. Sets T=1 if rs1 < rs2, T=0 otherwise. No register is modified. Use `SRR rd; ANDI rd, 1` to capture the result in a register if needed.

#### CEQ -- Compare Equal

`T = (rs1 == rs2)` -- 2 cycles

Compares rs1 and rs2 for equality. Sets T=1 if equal, T=0 otherwise. No register is modified. Pattern: `CEQ a, b; BT target` (branch if a == b).

### System Format

#### SEI -- Set Interrupt Disable

`I = 1` -- 2 cycles

Disables interrupts.

#### CLI -- Clear Interrupt Disable

`I = 0` -- 2 cycles

Enables interrupts. A pending IRQ (IRQB=0) will be taken at the next instruction boundary.

#### RETI -- Return from Interrupt

`{I, T} = ESR; PC = EPC` -- 2 cycles

Restores both I and T flags from the ESR register and returns to the interrupted code. EPC is a clean 16-bit address (no flag packing). The I-bit effect is forwarded: if ESR restores I=0 and IRQB is asserted, the IRQ fires immediately at the next instruction boundary.

#### EPCR -- Read Exception PC

`rd = EPC` -- 2 cycles

Copies the Exception PC register to a general-purpose register. EPC is a clean 16-bit return address. Register is at ir[7:5].

#### EPCW -- Write Exception PC

`EPC = rs` -- 2 cycles

Copies a general-purpose register to the Exception PC register. Register is at ir[7:5]. Modifies only the return address; the saved {I, T} flags are in ESR, not EPC.

#### SRR -- Read Status Register

`rd = {14'b0, I, T}` -- 2 cycles

Reads the current status register into a GP register. Bit 1 = I (interrupt disable), bit 0 = T (condition flag). Bits 15:2 are cleared. Register is at ir[7:5]. Pair with SRW to save/restore interrupt context.

#### SRW -- Write Status Register

`{I, T} = rs[1:0]` -- 2 cycles

Writes the I and T flags from bits 1 and 0 of the source register. Both flags take effect immediately (forwarded to the next instruction boundary). Register is at ir[7:5]. Pair with SRR: `SRR rd` to save, `SRW rs` to restore.

#### INT -- Software Interrupt

`ESR = {I, T}; EPC = PC+2; I = 1; PC = (vector[1:0] + 1) * 2` -- 2 cycles

Triggers a software interrupt. Saves {I, T} to ESR and the return address to EPC, disables interrupts, and vectors to the handler. BRK is the conventional name for INT with vector 1 (handler at $0004). INT is unconditional -- it fires regardless of the I bit.

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
| NOP/AUIPC/LUI/LI/ADD/SUB/AND/OR/XOR/SLL/SRL/SRA/ADDI/ANDI/ORI/XORI/CLTI/CLTUI/CEQI/CLT/CLTU/CEQ/SLLI/SRLI/SRAI/SLLT/SRLT/RLT/RRT | 2 | 1 execute + 1 overlapped fetch |
| SEI/CLI/SRR/SRW | 2 | 1 execute + 1 overlapped fetch |
| BZ/BNZ/BT/BF (not taken) | 2 | 1 execute + 1 overlapped fetch |
| BZ/BNZ/BT/BF (taken, same page) | 3 | 1 execute + 2 fetch after redirect |
| BZ/BNZ/BT/BF (taken, page crossing) | 4 | 2 execute + 2 fetch after redirect |
| LB/LBU/LBS/LBUS/LBR/LBUR | 3 | 2 address + 1 byte read (sign/zero-extend at E_MEM_LO) |
| SB/SBS/SBR | 3 | 2 address + 1 byte written |
| LW/SW/LWS/SWS/LWR/SWR | 4 | 2 address + 2 bytes transferred |
| JR (same page) | 3 | 1 execute + 2 fetch after redirect |
| JR (page crossing) / JALR | 4 | 2 execute + 2 fetch after redirect |
| J (same page) | 3 | 1 execute + 2 fetch after redirect |
| J (page crossing) / JAL | 4 | 2 execute + 2 fetch after redirect |
| RETI | 2 | Instantaneous dispatch + 2 fetch (overlapped) |
| INT (BRK) | 2 | Instantaneous dispatch + 2 fetch (overlapped) |
| WAI (wake) | 2 | 1 execute + 1 overlapped fetch |
| WAI (halt) | -- | Halted until interrupt arrives |
| STP | 1 | 1 execute then halt |
| EPCR/EPCW | 2 | 1 execute + 1 overlapped fetch |
| IRQ entry | 2 | Instantaneous dispatch + 2 fetch |
| NMI entry | 2 | Instantaneous dispatch + 2 fetch |

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

## Code Comparison: RISCY-V02 vs 6502

Side-by-side assembly for common routines, showing how the two ISAs compare on real code. All cycle counts assume same-page branches (the common case for tight loops). The 6502 uses zero-page pointers; RISCY-V02 uses register arguments.

### memcpy

```c
void memcpy(void *dst, const void *src, size_t n);
```

**6502** — arguments in zero page: src ($00), dst ($02), count ($04)

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
    LI   R1, 1          ;  2 cy   2 B    ; mask
    AND  R1, R4, R1     ;  2 cy   2 B    ; R1 = odd flag
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

Total code: **28 bytes**

| | 6502 | RISCY-V02 |
|---|---|---|
| Inner loop | 16 cy/byte | 8.5 cy/byte |
| Boundary overhead | 15 cy / 256 B | none |
| Tail | 18 cy/byte | 6 cy (1 byte) |
| Code size | 28 B | 28 B |

The 6502's `(indirect),Y` is powerful — pointer dereference plus index in one instruction. But the 8-bit index register forces page-boundary handling that complicates the code. RISCY-V02's 16-bit pointers eliminate page handling, and 16-bit word loads/stores copy two bytes per bus transaction, nearly halving throughput cost. The structure is analogous: bulk transfer (pages vs words) with a tail for the remainder (partial page vs odd byte). Code size is identical at 28 bytes — the 6502's compact 1-byte instructions (INY, DEX) compensate for the page-crossing overhead, while RISCY-V02's uniform 2-byte encoding trades density for simplicity.

### strcpy

```c
char *strcpy(char *dst, const char *src);
```

**6502** — arguments in zero page: src ($00), dst ($02)

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

| | 6502 | RISCY-V02 |
|---|---|---|
| Inner loop | 18 cy/char | 13 cy/char |
| Page overhead | 13 cy / 256 chars | none |
| Code size | 18 B | 12 B |

Both versions store the byte before testing for the null terminator — the 6502 via `BEQ` after `STA`, RISCY-V02 via `BNZ` after `SBR`. The 6502 needs an extra `BEQ` branch (2 cycles, not taken) on every character to check for termination, plus page-crossing logic. RISCY-V02 folds the termination check into the loop's back-edge branch. At 12 bytes vs 18, RISCY-V02 is also more compact — the 6502's page-crossing code (6 bytes) adds density overhead that RISCY-V02 simply doesn't need.

Word-copy variant (RISCY-V02 only, R7 = 0x00FF preloaded):

```
strcpy:
    LWR  R5, R3         ;  4 cy   2 B    ; load 2 chars
    AND  R1, R5, R7     ;  2 cy   2 B    ; R1 = low byte
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

Word loop: 23 cy / 2 chars = **11.5 cy/char**, 26 B. The null-byte detection (`AND` + `BZ` + `SUB` + `BZ` = 8 cy) eats the word-load savings, so the speedup over the byte version is modest (~12%). Unlike memcpy, where word copies nearly halve throughput, strcpy's per-element null check limits the benefit.

### 16×16 → 16 Multiply

```c
uint16_t mul(uint16_t a, uint16_t b);
```

Both implementations use the same shift-and-add algorithm (GCC's `__mulsi3` pattern): shift the multiplier right one bit per iteration, conditionally add the multiplicand to the result, shift the multiplicand left, and exit early when the multiplier reaches zero.

**6502** — arguments in zero page: mult ($00), mcand ($02), result ($04)

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
    LI   R1, 1          ;  2 cy   2 B    ; constant mask
loop:
    BZ   R2, done       ;  2 cy   2 B    ; early exit
    AND  R0, R2, R1     ;  2 cy   2 B    ; R0 = bit 0
    SRLI R2, 1          ;  2 cy   2 B    ; multiplier >>= 1
    BZ   R0, no_add     ;  2.5 cy 2 B
    ADD  R4, R4, R3     ;  2 cy   2 B    ; result += mcand
no_add:
    SLLI R3, 1          ;  2 cy   2 B    ; mcand <<= 1
    J    loop           ;  3 cy   2 B
done:
    JR   R6, 0          ;  3 cy   2 B
```

Per iteration (no add): **14 cy** — `BZ`+`AND`+`SRLI`+`BZ`(taken)+`SLLI`+`J`

Per iteration (add): **15 cy** — adds `ADD`

Average: **14.5 cy/iter**. Total code: **20 bytes**

| | 6502 | RISCY-V02 |
|---|---|---|
| Per iteration (avg) | 44 cy | 14.5 cy |
| 16 iterations (avg) | ~704 cy | ~232 cy |
| Code size | 36 B | 20 B |

The 3× per-iteration speedup comes from three sources: 16-bit addition is one instruction (`ADD`) vs seven (`CLC`+3×`LDA`/`ADC`/`STA`); 16-bit shifts are one instruction (`SLLI`/`SRLI`) vs two (`ASL`+`ROL`); and testing a 16-bit value for zero is one instruction (`BZ`) vs three (`LDA`+`ORA`+`BEQ`). Every 16-bit operation that the 6502 must serialize byte-by-byte collapses to a single instruction on RISCY-V02. Code density follows the same pattern: 20 bytes vs 36, a 44% reduction. The 6502's 1-byte `CLC` and implied-operand instructions can't compensate for the sheer number of extra instructions needed to work in 8-bit halves.

### 16 ÷ 16 Unsigned Division

```c
uint16_t udiv16(uint16_t dividend, uint16_t divisor);
// Returns quotient; remainder available as a byproduct.
```

Both implementations use binary long division (restoring): shift the dividend left one bit at a time into a running remainder, trial-subtract the divisor, and shift the success/fail bit into the quotient.

**6502** — arguments in zero page: dividend ($00), divisor ($02), remainder ($04)

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
    SLLT R2             ;  2 cy   2 B    ; dividend <<= 1, T = old bit 15
    RLT  R4             ;  2 cy   2 B    ; remainder <<= 1, shift in T
    CLTU R4, R3         ;  2 cy   2 B    ; T = (rem < div)
    BT   no_sub         ;  2.5 cy 2 B    ; skip if can't subtract
    SUB  R4, R4, R3     ;  2 cy   2 B    ; remainder -= divisor
    ORI  R2, 1          ;  2 cy   2 B    ; set quotient bit
no_sub:
    ADDI R5, -1         ;  2 cy   2 B    ; counter--
    BNZ  R5, loop       ;  3 cy   2 B
    JR   R6, 0          ;  3 cy   2 B
```

Per iteration (no sub): **16 cy** — `SLLT`+`RLT`+`CLTU`+`BT`(taken)+`ADDI`+`BNZ`

Per iteration (sub): **19 cy** — adds `SUB`+`ORI`

Average: **17.5 cy/iter**. Total code: **22 bytes**

| | 6502 | RISCY-V02 |
|---|---|---|
| Per iteration (avg) | 49 cy | 17.5 cy |
| 16 iterations | ~784 cy | ~280 cy |
| Code size | 38 B | 22 B |

The structure is identical — the same restoring division algorithm. The 2.8× speedup comes from three sources: 16-bit shifts are single instructions, the trial subtraction compresses from 6 instructions to 2 (`CLTU`+`SUB`), and `SLLT`+`RLT` chain the dividend's high bit directly into the remainder without needing `SRR`+`ANDI` to extract T into a register (saving 6 cy/iteration vs the pre-SLLT version). At 22 bytes vs 38, RISCY-V02 is 42% more compact — the 6502's `SEC`+`TAY` bookkeeping and byte-by-byte shift chains add up fast.

### CRC-8 (SMBUS)

```c
uint8_t crc8(const uint8_t *data, uint8_t len);  // poly=0x07, init=0
```

Both use the standard bitwise algorithm: XOR each byte into the CRC, then shift left 8 times, conditionally XORing with the polynomial when the high bit shifts out.

**6502** — ptr ($00), len ($02, 8-bit), result in A

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

**RISCY-V02** — R2 = data ptr, R3 = len, result in R4; CRC kept in upper byte

```
crc8:
    LI   R4, 0          ;  2 cy   2 B    crc = 0 (upper byte)
    LI   R0, 0x07       ;  2 cy   2 B    polynomial
    SLLI R0, 8          ;  2 cy   2 B    R0 = 0x0700
byte_loop:
    LBUR R5, R2         ;  3 cy   2 B    R5 = *data
    SLLI R5, 8          ;  2 cy   2 B    data in upper byte
    XOR  R4, R4, R5     ;  2 cy   2 B    crc ^= byte
    LI   R5, 8          ;  2 cy   2 B
bit_loop:
    SLLT R4             ;  2 cy   2 B    crc <<= 1, T = old bit 15
    BF   no_xor         ;  2.5 cy 2 B    skip if bit was 0
    XOR  R4, R4, R0     ;  2 cy   2 B    crc ^= poly
no_xor:
    ADDI R5, -1         ;  2 cy   2 B
    BNZ  R5, bit_loop   ;  3 cy   2 B
    ADDI R2, 1          ;  2 cy   2 B    data++
    ADDI R3, -1         ;  2 cy   2 B    len--
    BNZ  R3, byte_loop  ;  3 cy   2 B
    SRLI R4, 8          ;  2 cy   2 B    move to low byte
    JR   R6, 0          ;  3 cy   2 B
```

Bit loop (no xor): **10 cy** — `SLLT`+`BF`(taken)+`ADDI`+`BNZ`

Bit loop (xor): **11 cy** — adds `XOR`

Average: **10.5 cy/bit**, 84 cy/byte bit processing. Per byte: **100 cy**. Total code: **32 bytes**

| | 6502 | RISCY-V02 |
|---|---|---|
| Bit loop (avg) | 10.5 cy | 10.5 cy |
| Per byte | 101 cy | 100 cy |
| Code size | 22 B | 32 B |

Essentially a tie on speed. The `SLLT` instruction shifts the CRC and captures the overflow bit into T in one instruction — matching the 6502's `ASL` + carry pattern. The remaining per-byte overhead (setup, pointer/counter updates) is slightly more on RISCY-V02 due to the upper-byte CRC convention, but the bit loop is now identical in cycle count. The 6502 wins on density (22 B vs 32 B) — its 1-byte `ASL A`, `DEX`, `INY`, and `RTS` pack the inner loop tightly, while RISCY-V02's uniform 2-byte encoding and explicit counter management cost 10 extra bytes. This is where 8-bit code density shines: the algorithm is inherently 8-bit, so the 6502's implied-operand instructions are at their most effective.

### CRC-16/CCITT

```c
uint16_t crc16(const uint8_t *data, uint8_t len);  // poly=0x1021, init=0xFFFF
```

Same bitwise algorithm, but with a 16-bit accumulator. The data byte is XORed into the high byte of the CRC.

**6502** — ptr ($00), len ($02, 8-bit), crc ($04)

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
    LUI  R0, 0x10       ;  2 cy   2 B    R0 = 0x1000
    ORI  R0, 0x21       ;  2 cy   2 B    R0 = 0x1021
byte_loop:
    LBUR R5, R2         ;  3 cy   2 B    R5 = *data
    SLLI R5, 8          ;  2 cy   2 B    byte → high position
    XOR  R4, R4, R5     ;  2 cy   2 B    crc ^= byte << 8
    LI   R5, 8          ;  2 cy   2 B
bit_loop:
    SLLT R4             ;  2 cy   2 B    crc <<= 1, T = old bit 15
    BF   no_xor         ;  2.5 cy 2 B    skip if bit was 0
    XOR  R4, R4, R0     ;  2 cy   2 B    crc ^= 0x1021
no_xor:
    ADDI R5, -1         ;  2 cy   2 B
    BNZ  R5, bit_loop   ;  3 cy   2 B
    ADDI R2, 1          ;  2 cy   2 B    data++
    ADDI R3, -1         ;  2 cy   2 B    len--
    BNZ  R3, byte_loop  ;  3 cy   2 B
    JR   R6, 0          ;  3 cy   2 B
```

Bit loop (no xor): **10 cy** — `SLLT`+`BF`(taken)+`ADDI`+`BNZ`

Bit loop (xor): **11 cy** — adds `XOR`

Average: **10.5 cy/bit**, 84 cy/byte bit processing. Per byte: **100 cy**. Total code: **34 bytes**

| | 6502 | RISCY-V02 |
|---|---|---|
| Bit loop (avg) | 25.5 cy | 10.5 cy |
| Per byte | 227 cy | 100 cy |
| Code size | 43 B | 34 B |

RISCY-V02 wins CRC-16 by >2× on speed and is also more compact (34 B vs 43 B). The `SLLT` instruction shifts and captures the overflow bit into T in a single instruction, matching the 6502's `ASL`+carry for free. The 6502's bit loop goes from 10.5 to 25.5 cy (2.4× slower) because every shift becomes `ASL`+`ROL` and every XOR becomes `LDA`+`EOR`+`STA` × 2. The polynomial XOR is especially painful: 1 instruction on RISCY-V02 vs 6 on the 6502. The density advantage reverses from CRC-8 because the 6502's byte-serialization overhead (6 extra instructions for XOR alone) outweighs its 1-byte instruction advantage.

### Raster Bar Interrupt Handler

A classic demo effect: an interrupt fires once per scanline to change the background color, producing horizontal rainbow bands. The handler increments a color byte in memory and writes it to a display register — the simplest possible useful work. Both examples target a C64-style system (VIC-II at $D000, color byte in zero page).

**Interrupt entry latency:**

Both CPUs must finish the current instruction before taking the interrupt. The average wait depends on the instruction mix of the interrupted code:

- **6502:** Instructions take 2–7 cycles. Length-biased sampling across a typical game loop gives an average wait of **~1.5 cycles**. After the instruction completes, the hardware pushes PC and status to the stack and reads the IRQ vector: **7 cycles**.
- **RISCY-V02:** Instructions take 2–4 cycles (pipeline-visible). Average wait: **~1 cycle**. After completion, EPC/ESR are saved and the PC is redirected in the same cycle (instantaneous dispatch), then the vector is fetched: **2 cycles**.

**6502** — color byte at $02 (zero page), VIC-II at $D019/$D021

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

Every register the handler touches must be saved and restored. The handler needs R0 (implicit base for I-type memory ops) and R5 (scratch). The color byte is not within reach of the VIC registers, so R0 must be loaded twice — once for zero page, once for $D000.

Register saves go below the current SP without adjusting it. This is safe because RISCY-V02's IRQ entry sets I=1, masking further IRQs, and NMI handlers cannot return (RETI from NMI is undefined behavior per the architecture — NMI handlers reset, halt, or spin). Since nothing that could resume the handler will touch the stack, the space below SP is exclusively ours for the handler's lifetime.

```
                                    ;  2 cy        entry: instantaneous dispatch + fetch vector
irq_handler:
    SWS  R0, -4         ;  4 cy   2 B    save R0 below SP
    SWS  R5, -2         ;  4 cy   2 B    save R5 below SP
    LI   R0, 0          ;  2 cy   2 B    R0 → zero page
    LBU  R5, 2          ;  3 cy   2 B    R5 = color ($0002)
    ADDI R5, 1          ;  2 cy   2 B    color++
    SB   R5, 2          ;  3 cy   2 B    save color ($0002)
    LUI  R0, 0xD0       ;  2 cy   2 B    R0 = $D000
    SB   R5, $21        ;  3 cy   2 B    $D021: background color
    SB   R5, $19        ;  3 cy   2 B    $D019: ack raster interrupt
    LWS  R5, -2         ;  4 cy   2 B    restore R5
    LWS  R0, -4         ;  4 cy   2 B    restore R0
    RETI                ;  2 cy   2 B
```

| Phase | Cycles |
|---|---|
| Instruction wait (avg) | ~1 |
| Hardware entry (dispatch+fetch) | 2 |
| Register save (`SWS`×2) | 8 |
| Handler body | 18 |
| Register restore (`LWS`×2) | 8 |
| Exit (`RETI`+fetch) | 2 |
| **Total** | **~39** |

Total code: **24 bytes**

| | 6502 | RISCY-V02 |
|---|---|---|
| Entry (HW) | 7 cy | 2 cy |
| Insn wait (avg) | ~1.5 cy | ~1 cy |
| Save/restore | 7 cy | 16 cy |
| Handler body | 18 cy | 18 cy |
| Exit | 6 cy | 2 cy |
| **Total** | **~39.5 cy** | **~39 cy** |
| Code size | 15 B | 24 B |

Essentially a tie on speed. The 6502's architectural advantage — each instruction carries its own address (zero page or absolute), so the handler mixes `INC $02` (zero page) with `STA $D021` (absolute) without base register setup — is offset by RISCY-V02's instantaneous interrupt dispatch (2-cycle entry/exit vs 7+6=13 for the 6502). RISCY-V02 must reload R0 when switching memory regions and save/restore two registers (16 cy vs 7 cy), but the 9-cycle entry/exit savings almost exactly compensate. The 6502 is significantly more compact (15 B vs 24 B) — its 1-byte `PHA`/`PLA`/`RTI` and embedded-address instructions (`INC $02`, `STA $D021`) pack tightly, while RISCY-V02 pays for explicit register save/restore and base register setup.

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

**6502** — S at $0200 (page-aligned), i/j in zero page

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

**RISCY-V02** — S base in R0, i in R1, j in R2; output in R3; R7 = 0x00FF (preloaded once)

```
; Setup (once): LI R7, -1; SRLI R7, 8  →  R7 = 0x00FF
rc4_byte:
    ADDI R1, 1          ; 2 cy  2 B    i++
    AND  R1, R1, R7     ; 2 cy  2 B    mod 256
    ADD  R3, R0, R1     ; 2 cy  2 B    R3 = &S[i]
    LBUR R4, R3         ; 3 cy  2 B    R4 = S[i]
    ADD  R2, R2, R4     ; 2 cy  2 B    j += S[i]
    AND  R2, R2, R7     ; 2 cy  2 B    mod 256
    ADD  R3, R0, R2     ; 2 cy  2 B    R3 = &S[j]
    LBUR R5, R3         ; 3 cy  2 B    R5 = S[j]
    SBR  R4, R3         ; 3 cy  2 B    S[j] = old S[i]
    ADD  R3, R0, R1     ; 2 cy  2 B    R3 = &S[i]
    SBR  R5, R3         ; 3 cy  2 B    S[i] = old S[j]
    ADD  R3, R4, R5     ; 2 cy  2 B    R3 = S[i]+S[j]
    AND  R3, R3, R7     ; 2 cy  2 B    mod 256
    ADD  R3, R0, R3     ; 2 cy  2 B    R3 = &S[sum]
    LBUR R3, R3         ; 3 cy  2 B    output byte
    JR   R6, 0          ; 3 cy  2 B
```

**38 cycles, 32 bytes.** Three `AND` instructions (6 cy) with a preloaded mask register are needed for mod-256 masking that the 6502 gets for free from 8-bit registers (the mask setup is amortized over many calls). Five `ADD` instructions (10 cy) compute array addresses that the 6502 folds into its indexed addressing modes. Despite this 16-cycle tax, RISCY-V02 wins by a wide margin.

| | 6502 | RISCY-V02 |
|---|---|---|
| Cycles | 61 | 38 |
| Code size | 34 B | 32 B |
| Speedup | 1.0× | 1.6× |

RISCY-V02 wins decisively on speed (1.6×) and is slightly more compact (32 B vs 34 B). The mod-256 masking requires a preloaded mask register (R7 = 0x00FF) since ANDI is sign-extended, but the per-call cost is identical. Two factors overwhelm the mod-256 and address-computation tax:

1. **Registers eliminate state traffic.** The 6502 stores i and j in zero page — every call does INC+LDX+ADC+STA (14 cy) just to read, update, and write back two index variables. RISCY-V02 keeps i, j, and the S base in registers: state overhead is a single `ADDI` (2 cy).

2. **Multiple live values avoid spills and re-reads.** The swap requires S[i] and S[j] simultaneously, but the 6502's single accumulator forces a stack spill (`PHA`/`PLA`, 7 cy). RISCY-V02 holds both values in R4 and R5, computes the final sum as `ADD R3, R4, R5`, and never touches memory for temporaries.

### 32-bit Arithmetic

32-bit operations reveal the cost of each architecture's word width. The 6502's 8-bit ALU requires four byte-at-a-time steps for each 32-bit operation; RISCY-V02's 16-bit ALU cuts this to two. This section covers every R-type (register-register-register) ALU operation: ADD, SUB, AND, OR, XOR, SLL, SRL, SRA.

**Register conventions:**

- **6502:** 32-bit values in four consecutive zero-page bytes (little-endian): a ($00–$03), b ($04–$07), r ($08–$0B). Shift count in X.
- **RISCY-V02:** 32-bit values in register pairs {high, low}: A = {R1, R0}, B = {R3, R2}, result = {R5, R4}. Shifts are in-place on {R1, R0} with count in R2.

#### 32-bit ADD

```c
uint32_t add32(uint32_t a, uint32_t b);
```

**6502**

```
    CLC             ;  2 cy   1 B
    LDA a           ;  3 cy   2 B
    ADC b           ;  3 cy   2 B
    STA r           ;  3 cy   2 B
    LDA a+1         ;  3 cy   2 B
    ADC b+1         ;  3 cy   2 B
    STA r+1         ;  3 cy   2 B
    LDA a+2         ;  3 cy   2 B
    ADC b+2         ;  3 cy   2 B
    STA r+2         ;  3 cy   2 B
    LDA a+3         ;  3 cy   2 B
    ADC b+3         ;  3 cy   2 B
    STA r+3         ;  3 cy   2 B
```

13 instructions, **25 bytes, 38 cycles.** The carry flag chains automatically through all four ADC operations.

**RISCY-V02** — A = {R1, R0}, B = {R3, R2}, result = {R5, R4}

```
    ADD  R4, R0, R2     ;  2 cy   2 B    Rl = Al + Bl
    CLTU R4, R0         ;  2 cy   2 B    T = carry (result < input)
    ADD  R5, R1, R3     ;  2 cy   2 B    Rh = Ah + Bh
    BF   done           ;  3 cy   2 B    skip if no carry (T=0)
    ADDI R5, 1          ;  2 cy   2 B    Rh += carry
done:
```

5 instructions, **10 bytes, 9–10 cycles.** `CLTU` detects unsigned overflow (result < input ⟹ carry), then a conditional `ADDI` propagates it. No carry flag needed — the T-bit comparison substitutes cleanly. For constant-time code (crypto), replace the branch with `SRR R0; ANDI R0, 1; ADD R5, R5, R0` (6 insns, 12 B, 12 cy).

| | 6502 | RISCY-V02 |
|---|---|---|
| Code size | 25 B | 10 B |
| Cycles | 38 | 9–10 |
| Speedup | 1.0× | 3.8–4.2× |

#### 32-bit SUB

```c
uint32_t sub32(uint32_t a, uint32_t b);
```

**6502**

```
    SEC             ;  2 cy   1 B
    LDA a           ;  3 cy   2 B
    SBC b           ;  3 cy   2 B
    STA r           ;  3 cy   2 B
    LDA a+1         ;  3 cy   2 B
    SBC b+1         ;  3 cy   2 B
    STA r+1         ;  3 cy   2 B
    LDA a+2         ;  3 cy   2 B
    SBC b+2         ;  3 cy   2 B
    STA r+2         ;  3 cy   2 B
    LDA a+3         ;  3 cy   2 B
    SBC b+3         ;  3 cy   2 B
    STA r+3         ;  3 cy   2 B
```

13 instructions, **25 bytes, 38 cycles.** Mirror of ADD: SEC initializes the inverted borrow, and SBC chains carry through all four bytes.

**RISCY-V02** — A = {R1, R0}, B = {R3, R2}, result = {R5, R4}

```
    CLTU R0, R2         ;  2 cy   2 B    T = borrow (Al < Bl)
    SUB  R4, R0, R2     ;  2 cy   2 B    Rl = Al - Bl
    SUB  R5, R1, R3     ;  2 cy   2 B    Rh = Ah - Bh
    BF   done           ;  3 cy   2 B    skip if no borrow (T=0)
    ADDI R5, -1         ;  2 cy   2 B    Rh -= borrow
done:
```

5 instructions, **10 bytes, 9–10 cycles.** `CLTU` must precede `SUB` so the comparison uses the original Al (in case R4 aliases R0). SUB does not modify T, so the borrow survives to the branch. Constant-time variant: `SRR R0; ANDI R0, 1; SUB R5, R5, R0` (6 insns, 12 B, 12 cy).

| | 6502 | RISCY-V02 |
|---|---|---|
| Code size | 25 B | 10 B |
| Cycles | 38 | 9–10 |
| Speedup | 1.0× | 3.8–4.2× |

#### 32-bit AND / OR / XOR

```c
uint32_t and32(uint32_t a, uint32_t b);
uint32_t or32(uint32_t a, uint32_t b);
uint32_t xor32(uint32_t a, uint32_t b);
```

All three bitwise operations are identical in structure — no carry, no interaction between bytes/words.

**6502** (shown for AND; substitute ORA/EOR for OR/XOR)

```
    LDA a           ;  3 cy   2 B
    AND b           ;  3 cy   2 B
    STA r           ;  3 cy   2 B
    LDA a+1         ;  3 cy   2 B
    AND b+1         ;  3 cy   2 B
    STA r+1         ;  3 cy   2 B
    LDA a+2         ;  3 cy   2 B
    AND b+2         ;  3 cy   2 B
    STA r+2         ;  3 cy   2 B
    LDA a+3         ;  3 cy   2 B
    AND b+3         ;  3 cy   2 B
    STA r+3         ;  3 cy   2 B
```

12 instructions, **24 bytes, 36 cycles.**

**RISCY-V02** (shown for AND; substitute OR/XOR)

```
    AND  R4, R0, R2     ;  2 cy   2 B    Rl = Al & Bl
    AND  R5, R1, R3     ;  2 cy   2 B    Rh = Ah & Bh
```

2 instructions, **4 bytes, 4 cycles.**

| | 6502 | RISCY-V02 |
|---|---|---|
| Code size | 24 B | 4 B |
| Cycles | 36 | 4 |
| Speedup | 1.0× | 9.0× |

#### 32-bit SLL (Shift Left Logical)

```c
uint32_t sll32(uint32_t a, unsigned shamt);  // shamt 0–31
```

The 6502 has no barrel shifter — it must loop one bit per iteration, chaining ASL+ROL across four bytes. RISCY-V02's barrel shifter (SLL/SRL shift 0–15 bits in one instruction) enables an O(1) approach: split on whether N >= 16, then shift both halves and merge the cross-word bits.

**6502** — val ($00–$03, modified in-place), shift count in X

```
    LDX shift       ;  3 cy   2 B
    BEQ done        ;  2 cy   2 B
loop:
    ASL val         ;  5 cy   2 B
    ROL val+1       ;  5 cy   2 B
    ROL val+2       ;  5 cy   2 B
    ROL val+3       ;  5 cy   2 B
    DEX             ;  2 cy   1 B
    BNE loop        ;  3 cy   2 B
done:
```

8 instructions, **15 bytes.** Per iteration: **25 cycles.** An 8-bit shift costs 204 cycles.

**RISCY-V02** — {R1, R0} shifted in-place, count in R2 (consumed), R3 scratch

```
    BZ   R2, done       ;  2 cy   2 B
    LI   R3, 16         ;  2 cy   2 B
    CLTU R2, R3         ;  2 cy   2 B    T = (N < 16)
    BT   small          ;  3 cy   2 B
    ; N >= 16: Rh = Rl << (N-16), Rl = 0
    SUB  R2, R2, R3     ;  2 cy   2 B
    SLL  R1, R0, R2     ;  2 cy   2 B
    LI   R0, 0          ;  2 cy   2 B
    J    done           ;  3 cy   2 B
small:
    ; N = 1..15: shift both halves, merge cross-word bits
    SUB  R3, R3, R2     ;  2 cy   2 B    R3 = 16-N
    SRL  R3, R0, R3     ;  2 cy   2 B    R3 = Rl >> (16-N)
    SLL  R1, R1, R2     ;  2 cy   2 B    Rh <<= N
    OR   R1, R1, R3     ;  2 cy   2 B    Rh |= cross bits
    SLL  R0, R0, R2     ;  2 cy   2 B    Rl <<= N
done:
```

13 instructions, **26 bytes, 17–19 cycles** (constant, regardless of shift amount). For compact code at the cost of O(N) time, a loop alternative using SLLT+RLT is 5 insns / 10 B / 9N cy.

| | 6502 | RISCY-V02 |
|---|---|---|
| Code size | 15 B | 26 B |
| 1-bit shift | 29 cy | 19 cy |
| 8-bit shift | 204 cy | 19 cy |
| 16-bit shift | 404 cy | 17 cy |
| Speedup (N=8) | 1.0× | 10.7× |

The 6502 is more compact (1-byte DEX, ASL, ROL) but O(N). RISCY-V02's barrel shifter makes the shift itself free — the overhead is all in the cross-word merge logic. For typical shift amounts (4–12), the barrel version is 5–10× faster.

#### 32-bit SRL (Shift Right Logical)

```c
uint32_t srl32(uint32_t a, unsigned shamt);  // shamt 0–31
```

Mirror of SLL. The 6502 chains LSR+ROR from the MSB down; RISCY-V02 uses the barrel shifter with the halves reversed.

**6502** — val ($00–$03, modified in-place), shift count in X

```
    LDX shift       ;  3 cy   2 B
    BEQ done        ;  2 cy   2 B
loop:
    LSR val+3       ;  5 cy   2 B
    ROR val+2       ;  5 cy   2 B
    ROR val+1       ;  5 cy   2 B
    ROR val         ;  5 cy   2 B
    DEX             ;  2 cy   1 B
    BNE loop        ;  3 cy   2 B
done:
```

8 instructions, **15 bytes.** Per iteration: **25 cycles.**

**RISCY-V02** — {R1, R0} shifted in-place, count in R2 (consumed), R3 scratch

```
    BZ   R2, done       ;  2 cy   2 B
    LI   R3, 16         ;  2 cy   2 B
    CLTU R2, R3         ;  2 cy   2 B    T = (N < 16)
    BT   small          ;  3 cy   2 B
    ; N >= 16: Rl = Rh >> (N-16), Rh = 0
    SUB  R2, R2, R3     ;  2 cy   2 B
    SRL  R0, R1, R2     ;  2 cy   2 B
    LI   R1, 0          ;  2 cy   2 B
    J    done           ;  3 cy   2 B
small:
    ; N = 1..15: shift both halves, merge cross-word bits
    SUB  R3, R3, R2     ;  2 cy   2 B    R3 = 16-N
    SLL  R3, R1, R3     ;  2 cy   2 B    R3 = Rh << (16-N)
    SRL  R0, R0, R2     ;  2 cy   2 B    Rl >>= N
    OR   R0, R0, R3     ;  2 cy   2 B    Rl |= cross bits
    SRL  R1, R1, R2     ;  2 cy   2 B    Rh >>= N
done:
```

13 instructions, **26 bytes, 17–19 cycles** (constant). Loop alternative: SRLT+RRT, 5 insns / 10 B / 9N cy.

| | 6502 | RISCY-V02 |
|---|---|---|
| Code size | 15 B | 26 B |
| 1-bit shift | 29 cy | 19 cy |
| 8-bit shift | 204 cy | 19 cy |
| 16-bit shift | 404 cy | 17 cy |
| Speedup (N=8) | 1.0× | 10.7× |

#### 32-bit SRA (Shift Right Arithmetic)

```c
int32_t sra32(int32_t a, unsigned shamt);  // shamt 0–31
```

Arithmetic right shift preserves the sign bit. The 6502 uses a clever trick: `LDA; ASL A` copies the sign bit into carry, then `ROR` propagates it from the MSB down — but must loop. RISCY-V02's barrel shifter handles it in O(1): the large-shift case uses SRA for the low half and `SRAI R1, 15` to sign-fill the high half.

**6502** — val ($00–$03, modified in-place), shift count in X

```
    LDX shift       ;  3 cy   2 B
    BEQ done        ;  2 cy   2 B
loop:
    LDA val+3       ;  3 cy   2 B    load MSB
    ASL A           ;  2 cy   1 B    sign bit → carry
    ROR val+3       ;  5 cy   2 B    sign-preserving shift
    ROR val+2       ;  5 cy   2 B
    ROR val+1       ;  5 cy   2 B
    ROR val         ;  5 cy   2 B
    DEX             ;  2 cy   1 B
    BNE loop        ;  3 cy   2 B
done:
```

10 instructions, **18 bytes.** Per iteration: **30 cycles.** The LDA+ASL A trick (5 cy, 3 B) is the price of not having a dedicated ASR instruction.

**RISCY-V02** — {R1, R0} shifted in-place, count in R2 (consumed), R3 scratch

```
    BZ   R2, done       ;  2 cy   2 B
    LI   R3, 16         ;  2 cy   2 B
    CLTU R2, R3         ;  2 cy   2 B    T = (N < 16)
    BT   small          ;  3 cy   2 B
    ; N >= 16: Rl = Rh >>s (N-16), Rh = sign-fill
    SUB  R2, R2, R3     ;  2 cy   2 B
    SRA  R0, R1, R2     ;  2 cy   2 B    Rl = Rh >>s (N-16)
    SRAI R1, 15         ;  2 cy   2 B    Rh = 0x0000 or 0xFFFF
    J    done           ;  3 cy   2 B
small:
    ; N = 1..15: shift both halves, merge cross-word bits
    SUB  R3, R3, R2     ;  2 cy   2 B    R3 = 16-N
    SLL  R3, R1, R3     ;  2 cy   2 B    R3 = Rh << (16-N)
    SRL  R0, R0, R2     ;  2 cy   2 B    Rl >>= N (logical)
    OR   R0, R0, R3     ;  2 cy   2 B    Rl |= cross bits
    SRA  R1, R1, R2     ;  2 cy   2 B    Rh >>= N (arithmetic)
done:
```

13 instructions, **26 bytes, 17–19 cycles** (constant). `SRAI R1, 15` sign-fills the high word cleanly: it shifts in 15 copies of the sign bit, producing 0x0000 or 0xFFFF.

| | 6502 | RISCY-V02 |
|---|---|---|
| Code size | 18 B | 26 B |
| 1-bit shift | 34 cy | 19 cy |
| 8-bit shift | 244 cy | 19 cy |
| 16-bit shift | 484 cy | 17 cy |
| Speedup (N=8) | 1.0× | 12.8× |

#### 32-bit Summary

| Operation | 6502 | | RISCY-V02 | | Speedup |
|---|---|---|---|---|---|
| | Bytes | Cycles | Bytes | Cycles | |
| ADD | 25 | 38 | 10 | 9–10 | 3.8–4.2× |
| SUB | 25 | 38 | 10 | 9–10 | 3.8–4.2× |
| AND | 24 | 36 | 4 | 4 | 9.0× |
| OR | 24 | 36 | 4 | 4 | 9.0× |
| XOR | 24 | 36 | 4 | 4 | 9.0× |
| SLL (N=8) | 15 | 204 | 26 | 19 | 10.7× |
| SRL (N=8) | 15 | 204 | 26 | 19 | 10.7× |
| SRA (N=8) | 18 | 244 | 26 | 19 | 12.8× |

For ADD/SUB, the 16-bit ALU collapses 4 byte additions to 2 word additions plus a lightweight carry/borrow chain (CLTU+BF). For bitwise ops, the advantage is stark: 2 instructions vs 12. For shifts, the barrel shifter makes the operation O(1) — the 6502 must loop N times with no escape, while RISCY-V02 handles any shift amount in a fixed 17–19 cycles. This is the clearest architectural win: the barrel shifter transforms shifts from the 6502's weakest operation into a constant-time operation that's 10–13× faster.

The 6502 wins on shift code size (15–18 B vs 26 B) — its compact 1-byte implied instructions pack the loop tightly. RISCY-V02 trades 11 bytes of code for a 10× speedup. For code-size-sensitive contexts, a compact loop alternative using SLLT/RLT (10 B, 9N cy) is available.

### Packed BCD Arithmetic

The 6502's hardware decimal mode (`SED`) makes BCD arithmetic trivial — `ADC` and `SBC` automatically apply nibble correction. RISCY-V02 has no BCD support, so it must perform the correction in software using the Jones algorithm: pre-inject 6 into each nibble, add, detect which nibbles carried (the 6 was needed), and subtract 6 from those that didn't.

#### 8-bit Packed BCD Addition

```c
// a, b: 2-digit packed BCD (0x00–0x99)
// Returns packed BCD sum, carry in C/T
uint8_t bcd_add8(uint8_t a, uint8_t b);
```

**6502** — a in A, b in memory, result in A

```
    SED                 ;  2 cy   1 B    decimal mode
    CLC                 ;  2 cy   1 B
    ADC b               ;  3 cy   2 B    BCD add
    CLD                 ;  2 cy   1 B    back to binary
```

4 instructions, **5 bytes, 9 cycles.** The hardware does all nibble correction and carry propagation automatically. BCD carry out is in C.

**RISCY-V02** — a in R0, b in R1, result in R0, R2–R4 scratch

```
    LI   R2, 0x66       ;  2 cy   2 B    correction constant
    ADD  R3, R0, R2      ;  2 cy   2 B    t1 = a + 0x66
    ADD  R0, R3, R1      ;  2 cy   2 B    t2 = t1 + b
    XOR  R3, R3, R1      ;  2 cy   2 B    t3 = t1 ^ b
    XOR  R3, R0, R3      ;  2 cy   2 B    t4 = t2 ^ t3 (carry bits)
    LUI  R4, 0x01        ;  2 cy   2 B    \
    ADDI R4, 0x10        ;  2 cy   2 B    / R4 = 0x0110 (nibble mask)
    AND  R3, R3, R4      ;  2 cy   2 B    keep only nibble carry bits
    XOR  R3, R3, R4      ;  2 cy   2 B    invert: 1 = no carry (needs -6)
    OR   R4, R3, R3      ;  2 cy   2 B    R4 = copy of R3
    SRLI R4, 2           ;  2 cy   2 B    R4 = R3 >> 2
    SRLI R3, 3           ;  2 cy   2 B    R3 >>= 3
    OR   R3, R3, R4      ;  2 cy   2 B    correction = 6 per nibble
    SUB  R0, R0, R3      ;  2 cy   2 B    subtract excess 6
```

14 instructions, **28 bytes, 28 cycles.** BCD carry is in bit 8 of the result (paralleling the 6502's C flag). The Jones algorithm is branchless but requires 5 steps: inject, add, detect carries, build correction, subtract.

| | 6502 | RISCY-V02 |
|---|---|---|
| Code size | 5 B | 28 B |
| Cycles | 9 cy | 28 cy |
| Speedup | 1.0× | 0.3× |

#### 16-bit Packed BCD Addition

```c
// a, b: 4-digit packed BCD (0x0000–0x9999)
// Returns packed BCD sum, carry in C/T
uint16_t bcd_add16(uint16_t a, uint16_t b);
```

**6502** — val at ($00–$01), addend at ($02–$03), result in-place

```
    SED                 ;  2 cy   1 B    decimal mode
    CLC                 ;  2 cy   1 B
    LDA val+0           ;  3 cy   2 B
    ADC addend+0        ;  3 cy   2 B    low byte BCD add
    STA val+0           ;  3 cy   2 B
    LDA val+1           ;  3 cy   2 B
    ADC addend+1        ;  3 cy   2 B    high byte + carry
    STA val+1           ;  3 cy   2 B
    CLD                 ;  2 cy   1 B
```

9 instructions, **15 bytes, 24 cycles.** Just chain two 8-bit BCD adds through carry, exactly like binary multi-byte addition.

**RISCY-V02** — a in R0, b in R1, result in R0, R2–R4 scratch

```
    LUI  R2, 0x66        ;  2 cy   2 B    \
    ADDI R2, 0x66         ;  2 cy   2 B    / R2 = 0x6666
    ADD  R3, R0, R2       ;  2 cy   2 B    t1 = a + 0x6666
    ADD  R0, R3, R1       ;  2 cy   2 B    t2 = t1 + b
    XOR  R3, R3, R1       ;  2 cy   2 B    t3 = t1 ^ b
    XOR  R3, R0, R3       ;  2 cy   2 B    t4 = t2 ^ t3
    LUI  R4, 0x11         ;  2 cy   2 B    \
    ADDI R4, 0x10         ;  2 cy   2 B    / R4 = 0x1110
    AND  R3, R3, R4       ;  2 cy   2 B    keep nibble carry bits
    XOR  R3, R3, R4       ;  2 cy   2 B    invert: 1 = needs -6
    OR   R4, R3, R3       ;  2 cy   2 B    R4 = copy of R3
    SRLI R4, 2            ;  2 cy   2 B    R4 = R3 >> 2
    SRLI R3, 3            ;  2 cy   2 B    R3 >>= 3
    OR   R3, R3, R4       ;  2 cy   2 B    correction = 6 per nibble
    SUB  R0, R0, R3       ;  2 cy   2 B    subtract excess 6
```

15 instructions, **30 bytes, 30 cycles.** Identical structure to the 8-bit version — the wider register handles all 4 digits in parallel. No ANDI mask needed since the full 16 bits are the result. BCD carry out is detectable by comparing the pre-correction sum against 0x10000 (lost in 16-bit, would need CLTU before the final SUB).

| | 6502 | RISCY-V02 |
|---|---|---|
| Code size | 15 B | 30 B |
| Cycles | 24 cy | 30 cy |
| Speedup | 1.0× | 0.8× |

At 4 digits, the 6502's byte-serial approach starts to cost it: two separate load-add-store sequences, each with 5-cycle zero-page access. RISCY-V02's parallel nibble correction across a 16-bit register nearly closes the gap.

#### 32-bit Packed BCD Addition

```c
// a, b: 8-digit packed BCD (0x00000000–0x99999999)
// Returns packed BCD sum, carry in C/T
uint32_t bcd_add32(uint32_t a, uint32_t b);
```

**6502** — val at ($00–$03), addend at ($04–$07), result in-place

```
    SED                 ;  2 cy   1 B    decimal mode
    CLC                 ;  2 cy   1 B
    LDA val+0           ;  3 cy   2 B
    ADC addend+0        ;  3 cy   2 B    byte 0
    STA val+0           ;  3 cy   2 B
    LDA val+1           ;  3 cy   2 B
    ADC addend+1        ;  3 cy   2 B    byte 1 + carry
    STA val+1           ;  3 cy   2 B
    LDA val+2           ;  3 cy   2 B
    ADC addend+2        ;  3 cy   2 B    byte 2 + carry
    STA val+2           ;  3 cy   2 B
    LDA val+3           ;  3 cy   2 B
    ADC addend+3        ;  3 cy   2 B    byte 3 + carry
    STA val+3           ;  3 cy   2 B
    CLD                 ;  2 cy   1 B
```

15 instructions, **25 bytes, 42 cycles.** Each additional byte costs LDA+ADC+STA (9 cy, 6 B).

**RISCY-V02** — {R1, R0} + {R3, R2}, result in {R1, R0}, R4–R6 scratch

Two Jones corrections chained by a BCD carry. The carry is detected by checking for unsigned overflow in the injected addition (`t2 < b_lo` means bit 16 carried out). The carry is folded into `a_hi` before running Jones on the high half.

```
    ; --- low half: BCD(R0 + R2) ---
    LUI  R4, 0x66        ;  2 cy   2 B    \
    ADDI R4, 0x66         ;  2 cy   2 B    / R4 = 0x6666
    ADD  R5, R0, R4       ;  2 cy   2 B    t1 = a_lo + 0x6666
    ADD  R0, R5, R2       ;  2 cy   2 B    t2 = t1 + b_lo
    CLTU R0, R2           ;  2 cy   2 B    T = BCD carry (t2 < b_lo → overflow)
    XOR  R5, R5, R2       ;  2 cy   2 B    t3 = t1 ^ b_lo
    XOR  R5, R0, R5       ;  2 cy   2 B    t4 = t2 ^ t3
    LUI  R6, 0x11         ;  2 cy   2 B    \
    ADDI R6, 0x10         ;  2 cy   2 B    / R6 = 0x1110
    AND  R5, R5, R6       ;  2 cy   2 B    nibble carry bits
    XOR  R5, R5, R6       ;  2 cy   2 B    invert: 1 = needs -6
    OR   R6, R5, R5       ;  2 cy   2 B    copy
    SRLI R6, 2            ;  2 cy   2 B    R6 = R5 >> 2
    SRLI R5, 3            ;  2 cy   2 B    R5 >>= 3
    OR   R5, R5, R6       ;  2 cy   2 B    correction
    SUB  R0, R0, R5       ;  2 cy   2 B    corrected low result
    ; --- high half: BCD(R1 + R3 + carry) ---
    SRR  R5               ;  2 cy   2 B    \
    ANDI R5, 1            ;  2 cy   2 B    / R5 = carry (0 or 1)
    ADD  R1, R1, R5       ;  2 cy   2 B    a_hi' = a_hi + carry
    LUI  R4, 0x66         ;  2 cy   2 B    \
    ADDI R4, 0x66         ;  2 cy   2 B    / R4 = 0x6666
    ADD  R5, R1, R4       ;  2 cy   2 B    t1 = a_hi' + 0x6666
    ADD  R1, R5, R3       ;  2 cy   2 B    t2 = t1 + b_hi
    XOR  R5, R5, R3       ;  2 cy   2 B    t3 = t1 ^ b_hi
    XOR  R5, R1, R5       ;  2 cy   2 B    t4 = t2 ^ t3
    LUI  R6, 0x11         ;  2 cy   2 B    \
    ADDI R6, 0x10         ;  2 cy   2 B    / R6 = 0x1110
    AND  R5, R5, R6       ;  2 cy   2 B    nibble carry bits
    XOR  R5, R5, R6       ;  2 cy   2 B    invert
    OR   R6, R5, R5       ;  2 cy   2 B    copy
    SRLI R6, 2            ;  2 cy   2 B    R6 = R5 >> 2
    SRLI R5, 3            ;  2 cy   2 B    R5 >>= 3
    OR   R5, R5, R6       ;  2 cy   2 B    correction
    SUB  R1, R1, R5       ;  2 cy   2 B    corrected high result
```

34 instructions, **68 bytes, 68 cycles.** The high half is a near-copy of the low half, with 3 extra instructions to extract and add the BCD carry (SRR+ANDI+ADD). In a loop or subroutine, the constant loads (0x6666, 0x1110) could be hoisted, saving 8 instructions per call.

| | 6502 | RISCY-V02 |
|---|---|---|
| Code size | 25 B | 68 B |
| Cycles | 42 cy | 68 cy |
| Speedup | 1.0× | 0.6× |

The 6502's advantage continues to erode. Its cost grows by 9 cycles per byte (LDA+ADC+STA), while RISCY-V02's second Jones pass adds 18 instructions (carry extraction + the fixed Jones sequence). The wider the operand, the less the per-digit overhead of the Jones algorithm matters.

#### BCD Summary

| Operation | 6502 | | RISCY-V02 | | Speedup |
|---|---|---|---|---|---|
| | Bytes | Cycles | Bytes | Cycles | |
| 8-bit add (2 digits) | 5 | 9 | 28 | 28 | 0.3× |
| 16-bit add (4 digits) | 15 | 24 | 30 | 30 | 0.8× |
| 32-bit add (8 digits) | 25 | 42 | 68 | 68 | 0.6× |

Hardware BCD is the 6502's clearest architectural advantage. For 2-digit addition, `SED; CLC; ADC; CLD` is unbeatable — 4 instructions, 5 bytes, 9 cycles. The RISCY-V02 needs 14 instructions of bit manipulation to do what the 6502 does in microcode.

But the gap narrows with wider operands. The 6502 scales linearly — each additional byte costs LDA+ADC+STA (9 cy, 6 B) — while RISCY-V02's Jones algorithm handles all 4 nibbles in a 16-bit register in parallel. The cost per additional 16-bit word is one carry extraction (3 insns) plus a repeat of the fixed Jones sequence (15 insns). At 4 digits the cycle counts nearly converge; at 8 digits the 6502 leads by only 1.6×.

The real question is whether BCD matters enough to justify the ~400 transistors the 6502 spends on decimal mode. In the 1970s home computer context, BCD was used for score displays, clock readouts, and financial calculations — common but not performance-critical. A subroutine call to a BCD add routine costs RISCY-V02 about 28 cycles vs the 6502's 9 — noticeable but not crippling, and the transistor budget is better spent on features that accelerate the hot loops (barrel shifter, wider ALU).


## Register File SRAM Analysis

RISCY-V02's register file is an 8-word x 16-bit regular array of general-purpose registers (R0–R7) with 2 read ports and 1 write port (2R1W). All ports are 16 bits wide and use 3-bit select lines. Both bytes of a register are read/written simultaneously. Standard cell synthesis implements it using latches and mux trees, but a real chip would use SRAM — the array is perfectly regular and far too large for individual register cells.

This section designs an equivalent 8T SRAM register file from first principles, counts every transistor, and computes the SRAM-adjusted transistor count for fair comparison with the 6502 baseline.

### Why This Discount Is Fair

The discount applies only to **regular storage arrays** — structures where identical bit cells are arranged in a grid with shared decode/sense logic. The methodology is:

1. Identify separately-synthesizable modules that are pure regular arrays
2. Count their standard cell transistors exactly (from standalone synthesis)
3. Design an equivalent SRAM from first principles, counting every transistor
4. Apply the same methodology to the comparison target (the 6502)

The 6502 has no regular arrays — its registers (A, X, Y, SP) are asymmetric, each wired to different parts of the datapath. The same methodology applied to the 6502 yields zero discount.

### Standard Cell Register File (Synthesized)

The register file is a single Verilog module (`riscyv02_regfile`) marked `(* keep_hierarchy *)`. This prevents the synthesizer from flattening it into the parent module, so its cell counts appear as a sub-module in `stat.json` — extracted from the same synthesis run as the total, eliminating cross-run non-determinism.

It contains leader latches (20, transparent-high: 16 data + 3 sel + 1 we), follower latches (128, gated by ~clk & decoded wen), and read mux trees. Write inputs are combinational from execute; the leader-follower pair acts as a negedge-triggered write.

The exact cell counts vary slightly between synthesis runs (Yosys ABC optimization is non-deterministic), but the 148 latches are always present. The `transistor_count.py` script reads the actual count from each build's `stat.json`.

#### Functional Breakdown

- **20 leader latches** (16 w_data + 3 w_sel + 1 w_we): write port staging, transparent during clk=1
- **128 follower latches** (8 regs × 16 bits): pure storage array, perfectly regular
- **Combinational cells**: write decode (3-to-8) and 2 read mux trees (8:1 × 16 bits each)

Tx/cell counts are from the PDK's CDL SPICE netlist (one M-line = one MOSFET), the same source used for all transistor count estimates in this project.

### 8T SRAM Register File Design

#### Why 8T

6T SRAM provides 1 port. Our register file requires 2 simultaneous reads (the ALU needs both operands in the same cycle). The minimum cell for 2 ports is 8T:

- **6T** = 4T storage + 2T access = 1 port
- **8T** = 4T storage + 2T RW access + 2T read-only = 2 ports (1RW + 1R)

We time-share the RW port: reads during clk=1, writes at negedge. The R-only port provides the second simultaneous read. This matches our pipeline exactly.

#### 8T Bit Cell

```
Storage:   P1 P2 N1 N2  (cross-coupled inverters)     = 4T
RW port:   N3 N4        (access NMOS, gated by WL_rw)  = 2T
R port:    N5 N6        (N5=access gated by WL_r,      = 2T
                         N6=driver gated by QB)
                                                       ────
                                                         8T
```

The read-only port connects N6's gate to QB (complement of stored value), so the read bit line gives the non-inverted value Q — no output inversion needed.

#### Storage Array

8 rows x 16 columns = 128 cells x 8T = **1,024T**

#### Write Path

Writes occur during clk=0 through the RW port. Both bytes are written simultaneously (16-bit write port), requiring row decode, write enable, and data drivers.

##### Row Decoder (w_sel -> 8 one-hot lines)

A 3-to-8 decoder for all 8 rows using `w_sel[2:0]`:

| Component | Count | Tx/each | Transistors |
|---|---|---|---|
| INV (complement w_sel[2:0]) | 3 | 2 | 6 |
| AND3 (NAND3 + INV, one per row) | 8 | 8 | 64 |
| **Subtotal** | | | **70** |

##### Word Line Gating

Each decoded row line is ANDed with w_we to produce the write word line. Both byte halves share one word line (no byte select):

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| AND2 | WL[i] = row[i] AND w_we | 8 | 6 | 48 |
| **Subtotal** | | | | **48** |

##### Write Drivers

Generate complementary data for the bit lines. 16 data/complement pairs drive all 16 columns:

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| INV | ~w_data[i] (complement) | 16 | 2 | 32 |
| **Subtotal** | | | | **32** |

**Write decode + drivers total: 150T**

##### Write Staging

Both the standard cell regfile and the SRAM equivalent need write staging. The standard cell version uses leader latches (included in the module). The SRAM equivalent uses input latches to hold w_data/w_sel/w_we stable during the write pulse:

| Component | Count | Tx/each | Transistors |
|---|---|---|---|
| Data latch (TG + inverter loop) | 16 | 6 | 96 |
| Address latch | 3 | 6 | 18 |
| Enable latch (with reset) | 1 | 8 | 8 |
| **Subtotal** | | | **122** |

**Write path total: 150 + 122 = 272T**

#### Read Path 1 (RW Port, Differential)

During clk=1, the RW port reads r1_sel. This is a 3-bit address selecting one of 8 GP rows. Differential bit lines (BL/BLB) give correct polarity directly. Full 16-bit output (no byte select).

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| INV | complement r1_sel[2:0] | 3 | 2 | 6 |
| AND3 | row decode (one per row) | 8 | 8 | 64 |
| PMOS | precharge BL[0..15] | 16 | 1 | 16 |
| PMOS | precharge BLB[0..15] | 16 | 1 | 16 |
| PMOS | equalize BL=BLB | 16 | 1 | 16 |
| **Subtotal** | | | | **118** |

For an 8-deep array the bit-line swing is large and fast — no sense amplifiers are needed.

#### Read Path 2 (R-Only Port, Single-Ended)

The 8T cell's dedicated read port: N5 (access, gated by read word line) in series with N6 (driver, gated by QB). Read bit line (RBL) is pulled high by a keeper; selected cell conditionally discharges it. Port 2 uses a 3-bit address. Full 16-bit output (no byte select).

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| INV | complement r2_sel inputs | 3 | 2 | 6 |
| AND3 | row decode (one per row) | 8 | 8 | 64 |
| PMOS | pull-up keeper RBL[0..15] | 16 | 1 | 16 |
| **Subtotal** | | | | **86** |

#### Grand Total

| Component | Transistors | % |
|---|---|---|
| Storage array (128 x 8T) | 1,024 | 68.3% |
| Write path (decode + drivers + staging) | 272 | 18.1% |
| Read path 1 (RW, differential) | 118 | 7.9% |
| Read path 2 (R, single-ended) | 86 | 5.7% |
| **Total** | **1,500** | **100%** |

#### Gate Transistor Counts Used

All counts use standard CMOS complementary logic:

| Gate | Transistors | Structure |
|---|---|---|
| INV | 2 | 1 PMOS + 1 NMOS |
| NAND2 | 4 | 2P parallel + 2N series |
| AND2 | 6 | NAND2 + INV |
| NAND3 | 6 | 3P parallel + 3N series |
| AND3 | 8 | NAND3 + INV |
| MUX2 | 6 | 2 transmission gates + 1 INV |
| PMOS (precharge/keeper) | 1 | single transistor |

### Comparison

| | Standard Cell | 8T SRAM |
|---|---|---|
| Write staging | 20 leader latches × 20T = 400 | 20 latches × 6T = 122 (TG-based) |
| Storage | 128 follower latches × 20T = 2,560 | 128 cells × 8T = 1,024 |
| Peripherals | decode + read mux trees | Decode + drivers = 354 |
| **Total** | **(from synthesis)** | **1,500** |

Standard cell counts vary slightly between synthesis runs. The exact count for each build is extracted automatically from `stat.json`.

The SRAM saves on both storage (8T vs 20T per bit) and peripherals (word-line decode replaces explicit mux trees — asserting one word line selects all 16 bits of one register, eliminating the 8:1 mux per bit that standard cells require). Write staging is present in both: leader latches in standard cells, input latches in SRAM.

### SRAM-Adjusted Figures

These figures are computed automatically by `transistor_count.py` from each build's `stat.json`. The regfile standard cell count is extracted from the `riscyv02_regfile` sub-module (preserved by `keep_hierarchy`), ensuring consistency with the total.

| Metric | Value |
|---|---|
| Register file (8T SRAM equivalent) | 1,500 |
| Other values | (computed by `transistor_count.py`) |

### Methodology Notes

1. **Transistor counts are exact**, not estimates. Standard cell counts come from the PDK's CDL SPICE netlist (one M-line = one MOSFET). SRAM counts come from the circuit design above, using textbook CMOS gate structures.

2. **The 8T cell transistor count is definitional.** An 8T SRAM cell has 8 transistors by definition — that's what "8T" means. This is not a process-specific or PDK-specific number.

3. **The same methodology applies to both designs.** The 6502's registers (A, X, Y, SP) are asymmetric special-purpose registers wired to different datapath elements. They are not a regular array and would not use SRAM in any implementation. Applying this methodology to the 6502 yields zero discount.

4. **No SRAM macro exists** at this size for IHP sg13g2. The smallest available macro (64x32, 2048 bits) stores 16x more than needed and is physically larger than the entire RISCY-V02 design. The SRAM analysis here is a paper design representing what a custom chip would use.

## SRAM PCB Interface Design
### Overview

Connect the RISCY-V02 CPU (on a TT IHP board) to an IS61C256AL-10 32Kx8
asynchronous SRAM. The CPU uses a 6502-style muxed bus protocol where address
and data share the `uio[7:0]` pins across two clock phases.

### Bus Protocol Recap

```
              ┌───────┐       ┌───────┐
  clk     ───┘       └───────┘       └───────
          addr phase   data phase   addr phase
          (mux_sel=0)  (mux_sel=1)  (mux_sel=0)

  uo_out:  AB[7:0]     {..,SYNC,RWB}  AB[7:0]
  uio:     AB[15:8]    D[7:0]         AB[15:8]
           (output)    (bidir)        (output)
```

- **clk LOW** = address phase: `uo_out` = AB[7:0], `uio_out` = AB[15:8]
- **clk HIGH** = data phase: `uo_out[0]` = RWB, `uio` = data bus (direction per RWB)

### Components

| Ref | Part | Qty | Purpose |
|-----|------|-----|---------|
| U1 | 74HCT573 | 1 | Address latch, low byte (AB[7:0]) |
| U2 | 74HCT573 | 1 | Address latch, high byte (AB[15:8]) |
| U3 | 74LVC245 | 1 | Data bus transceiver (level shift 5V<->3.3V) |
| U4 | 74HCT00 | 1 | Quad NAND — all glue logic |
| U5 | IS61C256AL-10TL | 1 | 32Kx8 SRAM |
| | 100nF caps | 5 | Decoupling, one per IC |

**74HCT** series (5V, TTL-input thresholds VIH=2.0V) for address latches:
accepts 3.3V TT outputs as valid HIGH.

**74LVC245** (3.3V, 5V-tolerant inputs) for data bus: translates 5V SRAM
outputs down to 3.3V for TT inputs, and 3.3V TT outputs are valid HIGH for
5V SRAM inputs (VIH=2.2V).

### Glue Logic (U4: 74HCT00, quad NAND)

All active control signals derived from `clk` and `uo_out[0]` (which is
AB[0] during address phase, RWB during data phase — but glue logic only
uses it meaningfully during data phase, when clk is HIGH).

```
Gate A:  !clk        = NAND(clk, clk)           → address latch LE
Gate B:  OE (SRAM)   = NAND(clk, uo_out[0])     → SRAM OE
Gate C:  !uo_out[0]  = NAND(uo_out[0], uo_out[0])  (inverter)
Gate D:  WE (SRAM)   = NAND(clk, !uo_out[0])    → SRAM WE
```

Truth table for SRAM control during each phase:

| Phase | clk | uo_out[0] | OE | WE | SRAM state |
|-------|-----|-----------|----|----|------------|
| Addr  | 0   | AB[0]     | 1  | 1  | Deselected output (I/O = High-Z) |
| Read  | 1   | RWB=1     | 0  | 1  | Read (drives I/O) |
| Write | 1   | RWB=0     | 1  | 0  | Write (accepts I/O) |

During address phase, both OE and WE are HIGH regardless of AB[0]:
`NAND(0, x) = 1` always. So the SRAM outputs are in high-Z, preventing
bus contention with the address latch outputs on the shared uio pins.

### Connections

#### Address Latches (U1, U2: 74HCT573)

74HCT573: transparent when LE=HIGH, latches on LE falling edge.

```
U1 (low byte):                    U2 (high byte):
  D[7:0]  ← uo_out[7:0]            D[7:0]  ← uio[7:0]
  Q[7:0]  → SRAM A0-A7              Q[6:0]  → SRAM A8-A14
  LE      ← !clk (Gate A)           Q[7]      (unused)
  OE      ← GND (always enabled)    LE      ← !clk (Gate A)
  VCC     ← 5V                      OE      ← GND (always enabled)
                                     VCC     ← 5V
```

LE = !clk: transparent during clk LOW (address phase), latches at posedge
(start of data phase). Address is held stable on SRAM throughout data phase.

#### Data Bus Transceiver (U3: 74LVC245)

```
U3 (74LVC245, powered at 3.3V):
  A[7:0]  ↔ SRAM I/O[7:0]       (5V side, 5V-tolerant inputs)
  B[7:0]  ↔ uio[7:0]            (3.3V side, directly to TT chip)
  DIR     ← SRAM OE (Gate B)    (see below)
  OE      ← !clk (Gate A)       (enable only during data phase)
  VCC     ← 3.3V
```

DIR controls direction: when OE (SRAM) = LOW (read cycle), DIR=LOW → A-to-B
(SRAM drives, TT receives). When OE (SRAM) = HIGH (write cycle), DIR=HIGH →
B-to-A (TT drives, SRAM receives). The 74LVC245 active-low OE disables the
buffer during address phase, preventing contention when uio carries address.

Note: DIR = Gate B output = NAND(clk, uo_out[0]). During data phase reads:
NAND(1,1) = 0 → A-to-B. During data phase writes: NAND(1,0) = 1 → B-to-A.
During address phase: NAND(0,x) = 1, but OE is HIGH (disabled) so DIR is
don't-care.

#### SRAM (U5: IS61C256AL-10TL)

```
  A0-A7   ← U1 Q[7:0]    (latched low address byte)
  A8-A14  ← U2 Q[6:0]    (latched high address byte)
  I/O0-7  ↔ U3 A[7:0]    (data bus via level-shifting transceiver)
  CE      ← GND           (always selected)
  OE      ← Gate B        (NAND(clk, uo_out[0]))
  WE      ← Gate D        (NAND(clk, !uo_out[0]))
  VCC     ← 5V
  GND     ← GND
```

CE is tied LOW so the SRAM is always selected. This lets tAA (address access
time) start counting as soon as the address latch updates, maximizing the
time available for the SRAM to produce valid output data.

### Timing Analysis

#### Read Cycle

```
    negedge           posedge           negedge
       │  addr phase    │  data phase     │
  clk  ┘────────────────┐────────────────┘
       │                │                │
  addr ──╱XXXX valid XXXX╲── latched ────── (held by U1/U2)
       │    │            │                │
       │    ├─ tAA(10ns) ─→ data valid    │
       │                │                │
  OE   ────── HIGH ──────╲── LOW ─────────╱
       │                │ tDOE(6ns)→valid │
       │                │                │
  SRAM ──── High-Z ─────╱── driving ─────╲
  I/O  │                │      ↓         │
       │                │  CPU samples   │
```

The address becomes valid partway through the address phase (after TT output
delay + latch propagation). tAA (10ns) starts counting from that point. If
the half-period is long enough, tAA is satisfied before the data phase even
starts. The secondary constraint tDOE (6ns from OE going LOW at posedge)
determines the earliest data-valid point within the data phase.

**Minimum half-period for read** (ignoring TT mux delays):
`max(tAA - addr_setup_time, tDOE) + tsu_cpu ≈ 6-10ns + margin`

#### Write Cycle

```
    negedge           posedge           negedge
       │  addr phase    │  data phase     │
  clk  ┘────────────────┐────────────────┘
       │                │                │
  addr ──╱XXXX valid XXXX╲── latched ────── (held)
       │                │ ├── tAW(9ns) ──→│ WE↑
       │                │                │
  WE   ────── HIGH ──────╲── LOW ─────────╱
       │                │←── tPWE(8ns) ──→│
       │                │                │
  DIN  ────── X ─────────╱── valid ──────╲
       │                │ ├── tSD(7ns) ──→│ WE↑
       │                │            tHD=0│
```

Write terminates at negedge clk (WE goes HIGH). The SRAM needs:
- tAW = 9ns: address valid before WE rises (satisfied: address was latched
  at start of data phase, stable throughout)
- tSD = 7ns: data valid before WE rises
- tPWE = 8ns: WE pulse width (= data phase duration = half-period)
- tHD = 0ns, tHA = 0ns: zero hold after WE rises

**Minimum half-period for write** (ignoring TT mux delays):
`max(tPWE, tAW, tSD + data_setup_time) ≈ 9ns + margin`

#### Practical Clock Speed

The TT IHP mux/demux infrastructure adds significant delay to both the
clock-to-output and input-to-register paths. For sky130, round-trip latency
was measured at ~20ns; IHP numbers are not yet available.

Assuming ~10ns output delay and ~10ns input delay:

- **Read**: need address valid to data sampled = tAA + TT_output + TT_input
  = 10 + 10 + 10 = 30ns. Half-period must exceed this, so full period > 60ns
  (~16 MHz). Conservatively, **5-10 MHz** for comfortable margin.

- **Write**: need WE pulse > tPWE (8ns) and data setup > tSD (7ns). The data
  arrives after TT output delay, so half-period > TT_output_delay + tSD =
  10 + 7 = 17ns, full period > 34ns (~29 MHz). Less constrained than reads.

**Recommended starting clock: 4 MHz** (250ns period, 125ns per phase).
Provides ~10x margin over SRAM timing and comfortable margin for TT delays.
Tune up from there empirically.

### Voltage Level Summary

| Signal | Source | Voltage | Destination | Threshold | OK? |
|--------|--------|---------|-------------|-----------|-----|
| Address (uo_out, uio) | TT (3.3V) | 3.3V | 74HCT573 (5V) | VIH=2.0V | Yes |
| Latched addr | 74HCT573 (5V) | 5V | SRAM (5V) | VIH=2.2V | Yes |
| Write data (uio) | TT (3.3V) | 3.3V | 74LVC245 B-side | native | Yes |
| Write data to SRAM | 74LVC245 A-side (3.3V) | 3.3V | SRAM (5V) | VIH=2.2V | Yes |
| Read data from SRAM | SRAM (5V) | 5V | 74LVC245 A-side | 5V-tolerant | Yes |
| Read data to TT | 74LVC245 B-side (3.3V) | 3.3V | TT (3.3V) | native | Yes |
| clk, uo_out[0] | TT (3.3V) | 3.3V | 74HCT00 (5V) | VIH=2.0V | Yes |
| OE, WE | 74HCT00 (5V) | 5V | SRAM (5V) | VIH=2.2V | Yes |
| !clk (LE) | 74HCT00 (5V) | 5V | 74HCT573 (5V) | VIH=3.15V | Yes |
| !clk (buffer OE) | 74HCT00 (5V) | 5V | 74LVC245 OE | 5V-tolerant | Yes |

The 74LVC245 is the key part: powered at 3.3V with 5V-tolerant inputs, it
safely bridges the two voltage domains on the data bus. All other 5V→5V and
3.3V→5V paths work because 3.3V exceeds the HCT input threshold of 2.0V.

No signal path puts 5V into a 3.3V-only input.

### Schematic (text)

```
                     TT IHP Board                         PCB
                 ┌─────────────────┐
                 │   RISCY-V02     │
                 │                 │         ┌──────────┐
  uo_out[7:0] ──┤──────────────────┼────D───→│ U1       │    ┌──────────────┐
                 │                 │    !clk─→│ 74HCT573 │──Q→│ A0-A7        │
                 │                 │         └──────────┘    │              │
   uio[7:0] ──┬─┤──────────────────┼────D───→│ U2       │──Q→│ A8-A14       │
              │ │                 │    !clk─→│ 74HCT573 │    │              │
              │ │                 │         └──────────┘    │  IS61C256AL  │
              │ │                 │                          │  (U5)        │
              │ │                 │   ┌──────────┐          │              │
              └─┤──────────────────┼─B─┤ U3       ├─A──────→│ I/O0-I/O7    │
                │                 │   │ 74LVC245 │          │              │
                │                 │   └──────────┘    GND──→│ CE           │
                │                 │         ┌─────┐         │              │
         clk ──┤──────────────────┼────┐    │ U4  │    ┌───→│ OE           │
                │                 │    ├───→│NAND │────┘    │              │
  uo_out[0] ──┤──────────────────┼──┬─┘   │(x4) │────────→│ WE           │
                │                 │  │      │HCT00│         │              │
                 └─────────────────┘  │      └──┬──┘         └──────────────┘
                                      │         │
                                      └─→!clk───→ U1.LE, U2.LE, U3.OE
```

### SRAM Hold Time Requirements

All zero:
- **tHA = 0ns**: address hold from write end
- **tHD = 0ns**: data hold from write end
- **tOHA = 2ns**: output hold from address change (SRAM's guarantee to us,
  irrelevant — we sample data before address changes)

No hold violations are possible against this asynchronous SRAM.
All timing requirements are setup-like (minimum pulse widths and setup times
before write-terminating edges), solvable by slowing the clock.
