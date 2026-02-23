## Overview

RISCY-V02 is a 16-bit RISC processor that is logically pin-compatible with the
WDC 65C02. Adjusted for lack of a usable SRAM IP on TT IHP, the design fits
roughly within the same transistor count (~13K) as an off-the-shelf model of
the 6502 on the same process. This is comparable to the 11K of a 65C02, so
we're in the right ballpark; hand layout would of course do much better.

In comparison to the 6502, it provides:

| RISCY-V02 | 6502 |
| --- | --- |
| 8x 16-bit registers | 3x 8-bit registers |
| 2-cycle 16-bit arithmetic | 2/3-cycle 8-bit arithmetic |
| 2-cycle variable-width shifts (arithmetic or logical) | 2-3 cycle 1-bit logical shifts |
| 2 cycle interrupt entry/exit | 7-cycle interrupt entry, 6 cycle exit |
| 4-cycle calls, 3-4 cycle returns | 6-cycle calls/returns |
| 2-byte instructions | 1-3 byte instructions, ~2.25 bytes avg (Megaman 5) |
| 3-cycle 16-bit stack-relative load/store byte | 5/6-cycle 16-bit stack-relative load/store byte |
| 16,682 transistors (TT IHP) | 13,176 transistors (TT IHP) |
| 13,298 SRAM-adjusted transistors | 13,176 SRAM-adjusted transistors |

This project exists to provide evidence against a notion floating around in the
retrocomputing scene: that the 6502 was a "local optima" in the design space
of processors in its transistor budget. This never sat right with me, because
it implies that we haven't learned anything about how to make CPUs in the
intervening 40 years, and yet its design is full of things now generally
considered to be bad ideas: microcode PLAs, a large selection of borderline
useless addressing modes available on questionable instructions, hardware BCD.
One of the major points of RISC is that this area is better spent on things
that make the processor faster: pipelining, barrel shifters, and more
registers! This design does exactly that.

## How it works

### Bus Protocol

Like the 65C02, but unlike the 6502, the RISCY-V02 operates as a modern
edge-triggered design on a single clock. Unfortunately, TT doesn't provide
enough pins to implement the 6502's pinout. However, the 65c02 is negedge
triggered, and it produces its non-write output at some point after the
negedge, and its write output at some point after the following posedge. Both
are largely expected to be latched at the following negedge.

Accordingly, we adjust the timing so that the pins are exposed in two phases:
address and data. At negedge, the address pins are exposed for the system to
latch on the following posedge. Then, the pins are muxed over to expose the
control outputs and the data (read or write), to be latched on the following
negedge. Control inputs stay consistent between the two phases.

### Pinout

**Address Phase**
- `uo_out[7:0]` = AB[7:0]
- `uio_out[7:0]` = AB[15:8] (all output)

**Data Phase**
- `uo_out[0]` = RWB (1 = read, 0 = write)
- `uo_out[1]` = SYNC (1 = at instruction boundary)
- `uo_out[7:2]` = 0
- `uio[7:0]` = D[7:0] (bidirectional; output during writes, input during reads)

**Control inputs**
- `ui_in[0]` = IRQB (active-low interrupt request, level-sensitive)
- `ui_in[1]` = NMIB (active-low non-maskable interrupt, edge-triggered)
- `ui_in[2]` = RDY (active-high ready signal)

### Architecture

- **8x 16-bit general-purpose registers**: R0-R7 (3-bit encoding)
- **16-bit program counter**
- **T flag**: single-bit condition flag, set by comparisons (CLT, CLTU, CEQ, CLTI, CLTUI, CEQI), shift-through-T instructions (SLLT, SRLT, RLT, RRT), and SRW; tested by BT/BF branches
- **I flag**: interrupt disable (1 = disabled)
- **ESR**: 2-bit exception status register {I, T}, saved on interrupt entry, restored by RETI
- **EPC**: 16-bit exception PC, saved on interrupt entry
- **Fixed 16-bit instructions**: fetched low byte first
- **2-stage pipeline**: Fetch,Execute with speculative fetch and redirect

### Reset

- PC is set to $0000 and execution begins
- I (interrupt disable) is set to 1 -- interrupts are disabled
- T (condition flag) is cleared to 0
- ESR is set to {I=1, T=0}
- All registers are cleared to zero

### Interrupts

RISCY-V02 supports maskable IRQ and non-maskable NMI interrupts.

**Vector table** (2-byte spacing; IRQ last for inline handler):

| Vector ID | Address | Trigger |
|---|---|---|
| RESET | $0000 | RESB rising edge |
| 0 (NMI) | $0002 | NMIB falling edge, non-maskable |
| 1 (BRK) | $0004 | BRK instruction, unconditional |
| 2 (IRQ) | $0006 | IRQB low, level-sensitive, masked by I=1 |

Each vector slot is one instruction (2 bytes) -- enough for a J trampoline to
reach the actual handler. IRQ is placed last so its handler can run inline
without a jump, since nothing follows it.

NMI is edge-triggered; the behavior is broadly similar to the 6502. NMI has
priority over IRQ; if both are pending simultaneously, NMI is taken first, and
the subsequent I=1 masks the IRQ. NMI's state is sampled on negedge.

**Warning:** Unlike the 6502, RETI from an NMI handler is undefined behavior.
NMI overwrites EPC and ESR unconditionally, so if an NMI interrupts an IRQ
handler before it saves EPC/ESR (via EPCR/SRR), the IRQ's return state is lost.
NMI handlers typically reset, halt, or spin. This is typical of modern RISC
CPUs: NMI is intended for fatal hardware fault handling.

**Interrupt latency:** 2 cycles from instruction completion to first handler
instruction fetch (instantaneous dispatch + 2-cycle vector fetch). NMI edge
detection is combinational -- if the falling edge arrives on the same cycle
that the FSM is ready, the NMI is taken immediately with no additional
detection delay.

[Interrupt implementation](#interrupt-implementation) details are described
later.

## How to test

Connect to an external SRAM via the TT mux/demux bus protocol (active clock edge alternates between address output and data transfer). Control inputs: IRQB (active-low), NMIB (active-low edge-triggered), RDY (active-high). See [Bus Protocol](#bus-protocol), [RDY and SYNC Signals](#rdy-and-sync-signals), and [Input Timing](#input-timing) below.

## External hardware

A 32Kx8 asynchronous SRAM (e.g. IS61C256AL-10), two 74HCT573 address latches, a 74LVC245 data bus transceiver, and a 74HCT00 quad NAND for glue logic. See [SRAM PCB Interface Design](#sram-pcb-interface-design) below for the full schematic and timing analysis.

### Interrupt implementation

**Dispatch:** All interrupt entry (IRQ, NMI, BRK) is handled at dispatch time
in a single cycle. When the FSM is ready (instruction completing or idle), the
hardware saves EPC and ESR, sets I=1, and redirects the PC to the vector
address. The 2-cycle vector fetch is the only latency. BRK (INT with vector 1)
is handled identically at instruction dispatch. Since all three share the same
INT encoding format, software can also trigger IRQ/NMI vectors directly.

**Interrupt entry:**
1. Complete the current instruction
2. Save ESR = {I, T} -- status flags at interrupt entry
3. Save EPC = next_PC -- clean 16-bit return address
4. Set I = 1 -- disable further interrupts
5. Jump to vector entry

**Interrupt return (RETI instruction):**
1. Restore {I, T} from ESR
2. Jump to EPC

**Exception state:** EPC is a standalone 16-bit register holding the clean return address. ESR is a 2-bit register holding {I, T} at the time of interrupt entry. Neither is directly addressable through normal register fields. EPC is accessible through EPCR/EPCW. SRR/SRW read/write the live {I, T} flags; ESR is saved/restored automatically during interrupt entry and RETI. All GP registers (R0-R7) are directly accessible in interrupt context -- there is no register banking.

### Register Naming Convention

Two registers have architectural roles: R0 is the implicit base for I-type loads and stores (`R0 + sext(imm8)`), making it the natural accumulator/pointer, and R7 is the stack pointer for SP-relative memory instructions. The remaining six are truly general-purpose. Comparisons write to the T flag rather than a destination register, so all eight GPRs are available as operands.

| Register | Name | Purpose |
|---|---|---|
| R0 | a0 | Accumulator / implicit base (I-type memory) |
| R1 | a1 | Argument / scratch |
| R2 | t0 | Temporary |
| R3 | t1 | Temporary |
| R4 | s0 | Callee-saved |
| R5 | s1 | Callee-saved |
| R6 | ra | Return address (JAL/JALR write PC+2 here; return via `JR R6, 0`) |
| R7 | sp | Stack pointer |

R6 is a normal GPR — callee-saved, and interrupt handlers that use it must save and restore it manually. The interrupt return address lives in EPC, not R6. R-type loads and stores bypass the R0 convention, allowing explicit selection of both data register and base with no offset.

## Instruction Encoding

All 61 instructions are fixed 16-bit. Three properties drive the encoding: the sign bit is always ir[15], so sign extension runs in parallel with decode; the primary register field ir[7:5] is shared across I/SI/SYS/R-type formats, enabling a speculative register read before the opcode is fully decoded; and `0x0000` encodes ADDI R0, 0 (NOP). All immediates are sign-extended, same as RISC-V.

| Format | Layout (MSB→LSB) | Used |
|---|---|---|
| I | `[imm8:8\|rs/rd:3\|opcode:5]` | 24 |
| B | `[imm8:8\|funct3:3\|opcode:5]` | 2 |
| J | `[s:1\|imm[6:0]:7\|imm[8:7]:2\|fn1:1\|opcode:5]` | 2 |
| R | `[fn2:2\|rd:3\|rs2:3\|rs1:3\|opcode:5]` | 16 |
| SI | `[fn2:2\|fn4:2\|shamt:4\|rs/rd:3\|opcode:5]` | 7 |
| SYS | `[sub:8\|reg:3\|opcode:5]` | 10 |

In R-type, rs2 is at [10:8] and rd at [13:11].

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

Adds a sign-extended 8-bit immediate (-128 to +127) to the destination register. `ADDI R0, 0` (encoding `0x0000`) is the canonical NOP. Pairs with LUI for full 16-bit constant loading: `LUI rd, hi; ADDI rd, lo`.

#### LI -- Load Immediate

`rd = sext(imm8)` -- 2 cycles

Loads a sign-extended 8-bit immediate (-128 to +127) into a register.

#### LW, LB, LBU -- Load (R0-relative)

All I-type loads use R0 as the implicit base with a signed byte offset (-128 to +127). Word loads transfer low byte first.

- `LW rd, imm8` -- `rd = MEM16[R0 + sext(imm8)]` -- 4 cycles
- `LB rd, imm8` -- `rd = sext(MEM[R0 + sext(imm8)])` -- 3 cycles
- `LBU rd, imm8` -- `rd = zext(MEM[R0 + sext(imm8)])` -- 3 cycles

#### SW, SB -- Store (R0-relative)

- `SW rs, imm8` -- `MEM16[R0 + sext(imm8)] = rs` -- 4 cycles (low byte first)
- `SB rs, imm8` -- `MEM[R0 + sext(imm8)] = rs[7:0]` -- 3 cycles

#### JR -- Jump Register

`PC = rs + sext(imm8)` -- 3-4 cycles

Unconditional jump to a register plus a signed byte offset (-128 to +127).

#### JALR -- Jump and Link Register

`rs = PC+2; PC = rs + sext(imm8)` -- 4 cycles

Register-indirect call. The single register field is both jump base and link destination: `JALR R6, offset` reads the target from R6, then writes the return address back. Pairs with AUIPC for full 16-bit PC-relative calls: `AUIPC t0, upper; JALR t0, lower`.

#### ANDI -- And Immediate

`rd = rd & sext(imm8)` -- 2 cycles

Positive immediate masks low 7 bits and clears the high byte; negative immediate masks the low byte and preserves the high byte. Note: `ANDI rd, 0xFF` is a no-op (sign-extends to 0xFFFF); use `LBU` to extract a low byte.

#### ORI -- Or Immediate

`rd = rd | sext(imm8)` -- 2 cycles

#### XORI -- Xor Immediate

`rd = rd ^ sext(imm8)` -- 2 cycles

`XORI rd, -1` inverts all 16 bits (bitwise NOT).

#### CLTI -- Compare Less Than Immediate (Signed)

`T = (rs < sext(imm8))` -- 2 cycles

Sets T=1 if less, T=0 otherwise. No register modified. Pattern: `CLTI rs, val; BT target`.

#### CLTUI -- Compare Less Than Immediate (Unsigned)

`T = (rs <u sext(imm8))` -- 2 cycles

The immediate is sign-extended then compared as unsigned.

#### BZ -- Branch if Zero

`if rs == 0: PC += sext(imm8) << 1` -- 2 cycles (not taken) / 3-4 cycles (taken)

The offset is shifted left by 1, giving -256 to +254 bytes range. Tests the full 16-bit register value.

#### BNZ -- Branch if Non-Zero

`if rs != 0: PC += sext(imm8) << 1` -- 2 cycles (not taken) / 3-4 cycles (taken)

Pattern: `ADDI rd, -1; BNZ rd, loop`.

#### CEQI -- Compare Equal Immediate

`T = (rs == sext(imm8))` -- 2 cycles

### B-type -- T-Flag Branches

Branch based on the T flag set by comparison instructions. Same-page taken: 3 cycles; page-crossing: 4 cycles.

#### BT -- Branch if T Set

`if T == 1: PC += sext(imm8) << 1` -- 2 cycles (not taken) / 3-4 cycles (taken)

#### BF -- Branch if T Clear

`if T == 0: PC += sext(imm8) << 1` -- 2 cycles (not taken) / 3-4 cycles (taken)

Pattern: `CLTI rs, val; BF target` (branch if rs >= val).

### I-type -- Upper Immediate

#### LUI -- Load Upper Immediate

`rd = imm8 << 8` -- 2 cycles

Loads an 8-bit immediate into the upper byte, clearing the low byte. Pairs with ADDI for full 16-bit constants: `LUI rd, hi; ADDI rd, lo`. When the low byte has bit 7 set, compensate the upper byte by +1 (same as RISC-V).

#### AUIPC -- Add Upper Immediate to PC

`rd = (PC+2) + (imm8 << 8)` -- 2 cycles

Pairs with LW/SW/JR's offset for PC-relative addressing: AUIPC provides the upper bits, the subsequent instruction provides the lower bits.

### J-type -- PC-Relative Jumps

#### J -- Jump

`PC += sext(imm10) << 1` -- 3-4 cycles

Range: -1024 to +1022 bytes.

#### JAL -- Jump and Link

`R6 = PC+2; PC += sext(imm10) << 1` -- 4 cycles

Subroutine call; return with `JR R6, 0`.

### R-type -- Register ALU

All R-type ALU instructions are 2 cycles. rd at ir[13:11], rs2 at ir[10:8], rs1 at ir[7:5].

#### ADD -- `rd = rs1 + rs2`
#### SUB -- `rd = rs1 - rs2`
#### AND -- `rd = rs1 & rs2`
#### OR -- `rd = rs1 | rs2`
#### XOR -- `rd = rs1 ^ rs2`
#### SLL -- `rd = rs1 << rs2[3:0]` (shift left logical, 0-15)
#### SRL -- `rd = rs1 >>u rs2[3:0]` (shift right logical, 0-15)
#### SRA -- `rd = rs1 >>s rs2[3:0]` (shift right arithmetic, 0-15)

### SI-type -- Shift Immediate

All shift immediate instructions are 2 cycles and operate in-place (rd = rd shift shamt).

#### SLLI -- `rd = rd << shamt` (shamt 0-15)
#### SRLI -- `rd = rd >>u shamt` (shamt 0-15)
#### SRAI -- `rd = rd >>s shamt` (shamt 0-15)

### SI-type -- Shift/Rotate Through T

All 2 cycles, shift by exactly 1 bit, capture the shifted-out bit into T. Designed for bit-at-a-time algorithms (CRC, long division, multiply) and multi-word shifts.

#### SLLT -- `T = rd[15]; rd = {rd[14:0], 0}`
#### SRLT -- `T = rd[0]; rd = {0, rd[15:1]}`

#### RLT -- Rotate Left through T

`T = rd[15]; rd = {rd[14:0], old_T}` -- 17-bit rotate path (equivalent to 6502 ROL). Chaining RLT across two registers shifts a bit from one into the other.

#### RRT -- Rotate Right through T

`T = rd[0]; rd = {old_T, rd[15:1]}` -- equivalent to 6502 ROR.

### R-type -- Register Load/Store and Compare

Explicit registers for both data and base, no offset. Loads: rd at ir[13:11], address at rs1 ir[7:5]. Stores: data at rs2 ir[10:8], address at rs1 ir[7:5].

#### LWR -- `rd = MEM16[rs1]` -- 4 cycles
#### LBR -- `rd = sext(MEM[rs1])` -- 3 cycles
#### LBUR -- `rd = zext(MEM[rs1])` -- 3 cycles
#### SWR -- `MEM16[rs1] = rs2` -- 4 cycles
#### SBR -- `MEM[rs1] = rs2[7:0]` -- 3 cycles

#### CLT -- `T = (rs1 < rs2)` -- 2 cycles (signed)
#### CLTU -- `T = (rs1 <u rs2)` -- 2 cycles (unsigned)
#### CEQ -- `T = (rs1 == rs2)` -- 2 cycles

No register modified. Use `SRR rd; ANDI rd, 1` to capture T into a register.

### System Format

#### SEI -- `I = 1` -- 2 cycles (disable interrupts)
#### CLI -- `I = 0` -- 2 cycles (enable interrupts; pending IRQ taken at next boundary)

#### RETI -- Return from Interrupt

`{I, T} = ESR; PC = EPC` -- 2 cycles

Restores flags from ESR and returns. If ESR restores I=0 and IRQB is asserted, the IRQ fires immediately.

#### EPCR -- `rd = EPC` -- 2 cycles
#### EPCW -- `EPC = rs` -- 2 cycles
#### SRR -- `rd = {14'b0, I, T}` -- 2 cycles
#### SRW -- `{I, T} = rs[1:0]` -- 2 cycles (both flags forwarded immediately)

Pair SRR/SRW to save/restore interrupt context. EPCR/EPCW access the return address only; the saved {I, T} flags are in ESR.

#### INT -- Software Interrupt

`ESR = {I, T}; EPC = PC+2; I = 1; PC = (vector[1:0] + 1) * 2` -- 2 cycles

Unconditional — fires regardless of the I bit. BRK is INT with vector 1 (handler at $0004).

#### WAI -- Wait for Interrupt

Halts until an interrupt arrives. PC is advanced past WAI before halting, so RETI returns to the next instruction. With I=1, an IRQ wakes execution without entering a handler (65C02-style hint). 2 cycles if interrupt already pending; otherwise halted until wake.

#### STP -- Stop

Halts permanently (reset only). Both WAI and STP halt via internal clock gating. 1 cycle.

## Pipeline and Timing

The 2-stage pipeline (Fetch and Execute) overlaps fetch of the next instruction with execution of the current one. For sequential code and not-taken branches, the execute cost is completely hidden — throughput is limited by the 2-cycle fetch. Only taken branches and jumps pay execute cost directly, because the redirect flushes the speculative fetch.

### Cycle Counts (Throughput)

Throughput measured from one instruction boundary (SYNC) to the next:

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

### Self-Modifying Code

Because the next instruction's fetch overlaps with the current instruction's execution, **a store is never visible to the immediately following instruction fetch**. The instruction two past the store sees the new value. To fence, insert any instruction between the store and the modified code:

```
SB [target]     ; store writes to 'target' address
NOP             ; fence — target's fetch happens during NOP's execution
target:         ; this instruction sees the stored value
```

A single fence instruction is always sufficient, including for word stores.

## RDY and SYNC Signals

These provide W65C02S-compatible hooks for wait-state insertion, DMA, and single-step debugging — any system that needs to stall the CPU or observe instruction boundaries can use the same techniques as existing 65C02 designs.

### RDY (Ready Input)

When `ui_in[2]` is low, the processor halts atomically: all CPU state freezes (PC, registers, pipeline, ALU carry), bus outputs remain stable, and the bus protocol mux continues toggling. The processor resumes on the next edge after RDY returns high. RDY halts on both reads and writes, matching W65C02S behavior.

### SYNC (Instruction Boundary Output)

`uo_out[1]` during the data phase is high for one cycle when a new instruction begins execution.

### Single-Step and Wait-State Protocols

To **single-step**, monitor SYNC during data phases and pull RDY low when it goes high. The CPU halts at the instruction boundary. Pulse RDY high for one clock cycle to advance one instruction, then pull it low again when SYNC reasserts.

For **wait states**, external logic decodes the address during the address phase and pulls RDY low before the data-phase clock edge if the access needs more time. When the memory is ready, RDY goes high and the CPU continues.

## Input Timing

All inputs have a 4ns setup requirement before the capturing edge: RDY before posedge clk, data bus before negedge clk. Outputs are valid 4ns after their launching edge.

## Code Comparison: RISCY-V02 vs 6502

Side-by-side assembly for common routines. All cycle counts assume same-page branches. The 6502 uses zero-page pointers; RISCY-V02 uses register arguments.

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

The 6502's `(indirect),Y` is powerful — pointer dereference plus index in one instruction — but the 8-bit index forces page-boundary handling. RISCY-V02's 16-bit pointers eliminate page handling, and word loads/stores copy two bytes per transaction, nearly halving throughput cost. Code size is identical: the 6502's compact 1-byte instructions (INY, DEX) compensate for page-crossing overhead.

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

Both versions store-then-test for the null terminator. The 6502 needs a separate `BEQ` (2 cycles) every character plus page-crossing logic; RISCY-V02 folds termination into the back-edge branch. The 6502's page-crossing code (6 bytes) wipes out its density advantage.

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

Word loop: 23 cy / 2 chars = **11.5 cy/char**, 26 B. The null-byte detection (8 cy) eats most of the word-load savings — unlike memcpy, strcpy's per-element null check limits the benefit.

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

The 3× speedup: 16-bit addition is one instruction vs seven, shifts are one vs two, and zero-test is one vs three. Every 16-bit operation the 6502 serializes byte-by-byte collapses to a single instruction. The 6502's 1-byte implied-operand instructions can't compensate for the sheer number of extra instructions needed to work in 8-bit halves.

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

Same restoring division algorithm. The 2.8× speedup: 16-bit shifts are single instructions, trial subtraction compresses from 6 instructions to 2, and `SLLT`+`RLT` chain the dividend's high bit directly into the remainder without extracting T into a register. The 6502's `SEC`+`TAY` bookkeeping and byte-by-byte shift chains add up fast.

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

Essentially a tie. `SLLT` matches the 6502's `ASL` + carry pattern — the bit loops are identical in cycle count. The 6502 wins on density (22 B vs 32 B) because its 1-byte implied-operand instructions pack tightly in an inherently 8-bit algorithm.

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

RISCY-V02 wins CRC-16 by >2× and is more compact. The 6502's bit loop balloons from 10.5 to 25.5 cy because every shift becomes `ASL`+`ROL` and every XOR becomes `LDA`+`EOR`+`STA` × 2 — the polynomial XOR alone is 1 instruction vs 6. The density advantage reverses from CRC-8 because byte-serialization overhead outweighs the 1-byte instruction advantage.

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

Essentially a tie. The 6502's advantage — each instruction carries its own address, so the handler mixes zero-page and absolute accesses without base register setup — is offset by RISCY-V02's 2-cycle entry/exit vs 13. RISCY-V02 must reload R0 when switching memory regions and save/restore two registers (16 cy vs 7 cy), but the entry/exit savings compensate. For handlers with more useful work, the save/restore cost is fixed while body instructions are generally faster.

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

RISCY-V02 wins 1.6× on speed and is slightly more compact despite needing explicit mod-256 masking (ANDI is sign-extended, so a preloaded mask register is needed). Two factors overwhelm the masking/address tax: registers eliminate state traffic (the 6502 spends 14 cy per call reading and writing i/j in zero page; RISCY-V02 keeps them in registers), and multiple live values avoid spills (the swap needs S[i] and S[j] simultaneously, forcing the 6502 into a `PHA`/`PLA` spill that RISCY-V02 avoids entirely).

### 32-bit Arithmetic

32-bit operations expose the word-width cost directly: the 6502's 8-bit ALU requires four byte-at-a-time steps; RISCY-V02's 16-bit ALU cuts this to two. Convention: **6502** uses four zero-page bytes (a $00–$03, b $04–$07, r $08–$0B); **RISCY-V02** uses register pairs {high, low} (A = {R1, R0}, B = {R3, R2}, result = {R5, R4}).

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

13 instructions, **25 bytes, 38 cycles.** Carry chains automatically through all four ADC operations.

**RISCY-V02** — A = {R1, R0}, B = {R3, R2}, result = {R5, R4}

```
    ADD  R4, R0, R2     ;  2 cy   2 B    Rl = Al + Bl
    CLTU R4, R0         ;  2 cy   2 B    T = carry (result < input)
    ADD  R5, R1, R3     ;  2 cy   2 B    Rh = Ah + Bh
    BF   done           ;  3 cy   2 B    skip if no carry (T=0)
    ADDI R5, 1          ;  2 cy   2 B    Rh += carry
done:
```

5 instructions, **10 bytes, 9–10 cycles.** `CLTU` detects unsigned overflow (result < input), then a conditional `ADDI` propagates the carry. Constant-time variant: `SRR R0; ANDI R0, 1; ADD R5, R5, R0` (6 insns, 12 B, 12 cy).

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

13 instructions, **25 bytes, 38 cycles.** Mirror of ADD with SEC/SBC.

**RISCY-V02** — A = {R1, R0}, B = {R3, R2}, result = {R5, R4}

```
    CLTU R0, R2         ;  2 cy   2 B    T = borrow (Al < Bl)
    SUB  R4, R0, R2     ;  2 cy   2 B    Rl = Al - Bl
    SUB  R5, R1, R3     ;  2 cy   2 B    Rh = Ah - Bh
    BF   done           ;  3 cy   2 B    skip if no borrow (T=0)
    ADDI R5, -1         ;  2 cy   2 B    Rh -= borrow
done:
```

5 instructions, **10 bytes, 9–10 cycles.** `CLTU` must precede `SUB` to compare the original Al. Constant-time variant: `SRR R0; ANDI R0, 1; SUB R5, R5, R0` (6 insns, 12 B, 12 cy).

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

Identical structure for all three — no carry, no interaction between bytes/words.

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

The 6502 must loop one bit per iteration, chaining ASL+ROL across four bytes. RISCY-V02's barrel shifter enables an O(1) approach: split on N >= 16, shift both halves, merge the cross-word bits.

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

The 6502 is more compact but O(N). For typical shift amounts (4–12), the barrel version is 5–10× faster.

#### 32-bit SRL (Shift Right Logical)

Mirror of SLL. The 6502 chains LSR+ROR from the MSB down; RISCY-V02 reverses the halves.

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

Arithmetic right shift preserves the sign bit. The 6502 uses `LDA; ASL A` to copy the sign into carry, then chains `ROR` — but must loop. RISCY-V02 handles it in O(1), with `SRAI R1, 15` to sign-fill the high word in the large-shift case.

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

13 instructions, **26 bytes, 17–19 cycles** (constant).

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

ADD/SUB collapse from 4 byte additions to 2 word additions plus a lightweight carry chain. Bitwise ops: 2 instructions vs 12. Shifts are the clearest architectural win — the barrel shifter transforms the 6502's weakest operation (O(N) loops) into constant-time 17–19 cycles, a 10–13× speedup. The 6502 wins on shift code size (15–18 B vs 26 B); for code-size-sensitive contexts, a compact loop using SLLT/RLT (10 B, 9N cy) is available.

### Packed BCD Arithmetic

The 6502's hardware decimal mode (`SED`) makes BCD trivial — `ADC`/`SBC` apply nibble correction automatically. RISCY-V02 must do it in software via the Jones algorithm: pre-inject 6 into each nibble, add, detect which nibbles carried, subtract 6 from those that didn't.

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

4 instructions, **5 bytes, 9 cycles.**

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

14 instructions, **28 bytes, 28 cycles.** Branchless; BCD carry in bit 8.

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

9 instructions, **15 bytes, 24 cycles.** Two 8-bit BCD adds chained through carry.

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

15 instructions, **30 bytes, 30 cycles.** Same structure as 8-bit — the wider register handles all 4 digits in parallel.

| | 6502 | RISCY-V02 |
|---|---|---|
| Code size | 15 B | 30 B |
| Cycles | 24 cy | 30 cy |
| Speedup | 1.0× | 0.8× |

At 4 digits, the 6502's byte-serial approach starts to cost it. RISCY-V02's parallel nibble correction nearly closes the gap.

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

15 instructions, **25 bytes, 42 cycles.**

**RISCY-V02** — {R1, R0} + {R3, R2}, result in {R1, R0}, R4–R6 scratch

Two Jones corrections chained by a BCD carry detected via `CLTU` (unsigned overflow).

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

34 instructions, **68 bytes, 68 cycles.** In a subroutine, the constant loads (0x6666, 0x1110) could be hoisted, saving 8 instructions per call.

| | 6502 | RISCY-V02 |
|---|---|---|
| Code size | 25 B | 68 B |
| Cycles | 42 cy | 68 cy |
| Speedup | 1.0× | 0.6× |

The 6502's advantage continues to erode: it scales at 9 cy per byte, while RISCY-V02's Jones algorithm handles 4 nibbles in parallel per 16-bit word.

#### BCD Summary

| Operation | 6502 | | RISCY-V02 | | Speedup |
|---|---|---|---|---|---|
| | Bytes | Cycles | Bytes | Cycles | |
| 8-bit add (2 digits) | 5 | 9 | 28 | 28 | 0.3× |
| 16-bit add (4 digits) | 15 | 24 | 30 | 30 | 0.8× |
| 32-bit add (8 digits) | 25 | 42 | 68 | 68 | 0.6× |

Hardware BCD is the 6502's clearest architectural advantage. For 2-digit addition, `SED; CLC; ADC; CLD` is unbeatable. But the gap narrows with wider operands — the 6502 scales at 9 cy/byte while RISCY-V02's Jones algorithm handles 4 nibbles in parallel per word. At 4 digits the counts nearly converge.

The real question is whether BCD justifies the ~400 transistors the 6502 spends on decimal mode. In the 1970s context, BCD was used for scores, clocks, and financial calculations — common but not performance-critical. The transistor budget is better spent on features that accelerate hot loops (barrel shifter, wider ALU).


## Register File SRAM Analysis

Standard cell synthesis implements the register file with latches (~20T each) and mux trees, but a real chip would use SRAM cells (~8T each) — the 8×16-bit 2R1W array is perfectly regular. This over-counting inflates the RISCY-V02 transistor count by ~2,000T and makes the comparison with the 6502 misleading. This section designs an equivalent 8T SRAM register file from first principles, counts every transistor, and computes the adjusted figures.

### Why This Discount Is Fair

The discount applies only to **regular storage arrays** — identical bit cells in a grid with shared decode/sense logic. The same methodology applied to the 6502 yields zero discount: its registers (A, X, Y, SP) are asymmetric, each wired to different datapath elements, and would not use SRAM in any implementation.

### Standard Cell Register File (Synthesized)

The register file is a single Verilog module (`riscyv02_regfile`) marked `(* keep_hierarchy *)` so its cell counts appear as a sub-module in `stat.json`. It contains 20 leader latches (write staging), 128 follower latches (8 regs × 16 bits, the pure storage array), and combinational decode/mux trees. The `transistor_count.py` script reads the actual count from each build's `stat.json`.

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

The SRAM saves on both storage (8T vs 20T per bit) and peripherals (word-line decode replaces 8:1 mux trees). Write staging is present in both implementations.

### SRAM-Adjusted Figures

Computed by `transistor_count.py` from each build's `stat.json`.

| Metric | Value |
|---|---|
| Register file (8T SRAM equivalent) | 1,500 |
| Other values | (computed by `transistor_count.py`) |

### Methodology Notes

Transistor counts are exact: standard cell counts from the PDK's CDL SPICE netlist (one M-line = one MOSFET), SRAM counts from the circuit design above using textbook CMOS. The 8T cell count is definitional. No SRAM macro exists at this size for IHP sg13g2 — the smallest available (64×32, 2048 bits) is 16× larger than needed. This is a paper design representing what a custom chip would use.

## SRAM PCB Interface Design

TT IHP has no usable SRAM IP at this scale, so program/data memory needs an external SRAM chip. This section describes a complete PCB interface connecting the RISCY-V02 CPU to an IS61C256AL-10 32Kx8 asynchronous SRAM using the muxed bus protocol described in [Bus Protocol](#bus-protocol) above (address on clk LOW, data on clk HIGH).

### Components

| Ref | Part | Qty | Purpose |
|-----|------|-----|---------|
| U1 | 74HCT573 | 1 | Address latch, low byte (AB[7:0]) |
| U2 | 74HCT573 | 1 | Address latch, high byte (AB[15:8]) |
| U3 | 74LVC245 | 1 | Data bus transceiver (level shift 5V<->3.3V) |
| U4 | 74HCT00 | 1 | Quad NAND — all glue logic |
| U5 | IS61C256AL-10TL | 1 | 32Kx8 SRAM |
| | 100nF caps | 5 | Decoupling, one per IC |

74HCT (5V, VIH=2.0V) accepts 3.3V TT outputs as valid HIGH. 74LVC245 (3.3V, 5V-tolerant) bridges the voltage domains on the data bus.

### Glue Logic (U4: 74HCT00, quad NAND)

All control signals derived from `clk` and `uo_out[0]` (RWB during data phase):

```
Gate A:  !clk        = NAND(clk, clk)           → address latch LE
Gate B:  OE (SRAM)   = NAND(clk, uo_out[0])     → SRAM OE
Gate C:  !uo_out[0]  = NAND(uo_out[0], uo_out[0])  (inverter)
Gate D:  WE (SRAM)   = NAND(clk, !uo_out[0])    → SRAM WE
```

| Phase | clk | uo_out[0] | OE | WE | SRAM state |
|-------|-----|-----------|----|----|------------|
| Addr  | 0   | AB[0]     | 1  | 1  | High-Z (NAND(0,x)=1 always) |
| Read  | 1   | RWB=1     | 0  | 1  | Read (drives I/O) |
| Write | 1   | RWB=0     | 1  | 0  | Write (accepts I/O) |

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

DIR = Gate B output: reads → A-to-B (SRAM drives), writes → B-to-A (TT drives). The active-low OE disables the buffer during address phase, preventing contention.

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

CE tied LOW: tAA starts as soon as the address latch updates, maximizing read margin.

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

tAA (10ns) starts when the address latches update during the address phase. If the half-period is long enough, tAA is satisfied before data phase starts. The secondary constraint tDOE (6ns from OE LOW) determines earliest data-valid within the data phase.

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

Write terminates at negedge clk (WE rises). Key constraints: tAW=9ns (address setup, satisfied by latch), tSD=7ns (data setup), tPWE=8ns (WE pulse = half-period), tHD/tHA=0ns (no hold).

#### Practical Clock Speed

The TT IHP mux/demux adds ~10ns to each of the output and input paths (sky130 measured ~20ns round-trip; IHP not yet available). Read is the bottleneck: tAA + TT_output + TT_input ≈ 30ns, so full period > 60ns (~16 MHz). **Recommended starting clock: 4 MHz** (~10× margin). Tune up empirically.

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

No signal path puts 5V into a 3.3V-only input. The 74LVC245 bridges the voltage domains; all other 3.3V→5V paths work because 3.3V exceeds the HCT threshold of 2.0V.

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

All hold times are zero (tHA=0, tHD=0). No hold violations are possible; all timing constraints are setup-like, solvable by slowing the clock.
