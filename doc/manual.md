# RISCY-V02 User Manual

RISCY-V02 is a 16-bit RISC processor that is a pin-compatible drop-in replacement for the WDC 65C02. It uses the same 8-bit multiplexed bus protocol, same control signals, and fits in the same Tiny Tapeout 1x2 tile. Different ISA, same socket.

## Comparison with Arlet 6502

Both designs target the IHP sg13g2 130nm process on a 1x2 Tiny Tapeout tile. The clock speed is pinned to match the 6502 (~62 MHz), simulating 1970s DRAM constraints where raw clock speed improvements don't matter. The comparison focuses on IPC and transistor efficiency.

| Metric | RISCY-V02 | Arlet 6502 |
|---|---|---|
| Clock period | 14 ns | 14 ns |
| fMax (slow corner) | 71.4 MHz | 71.4 MHz |
| Utilization | 62.8% | 45.3% |
| Transistor count (synth) | 16,632 | 13,176 |
| SRAM-adjusted | 13,240 | 13,176 |

The SRAM-adjusted total is within 0.5% of the 6502, with significantly more capability per transistor: 16-bit registers, 3-operand ALU instructions, 2-cycle execute, PC-relative jumps, hardware call/return, instantaneous interrupts, and immediate arithmetic/logic. Unrecognized opcodes are treated as NOPs (2-cycle no-ops that advance the PC).

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

Shifts rd left by 1. The old bit 15 (shifted out) is captured in T. Bit 0 is filled with 0. Replaces the `CLTI rd, 0` + `SLLI rd, 1` pattern for extracting the sign bit before a shift.

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

## Code Comparison: RISCY-V02 vs 65C02

See [code_comparison.md](code_comparison.md) for side-by-side assembly comparisons (memcpy, strcpy, multiply, division, CRC-8/16, raster bar IRQ handler, RC4, 32-bit arithmetic, packed BCD).
