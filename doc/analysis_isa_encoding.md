# ISA Encoding Analysis: Decode Cost and Optimization Opportunities

This document catalogs every decode expression in `riscyv02_execute.v`, maps where each property is used, identifies decode cost hotspots, and proposes specific encoding changes to reduce combinational logic.

## 1. Catalog of Every Decode Expression

### 1.1 Format Detection (ir[15:12])

| Wire | Expression | Bits Used | Logic |
|---|---|---|---|
| `fmt_u` | `!ir[15] && !ir[14]` | 15, 14 | 2-input NOR |
| `fmt_j` | `!ir[15] && ir[14] && !ir[13]` | 15, 14, 13 | 3-input AND (with inversions) |
| `fmt_c` | `ir[15] && (ir[14] \|\| (ir[13] && ir[12]))` | 15, 14, 13, 12 | AND-OR: 1 AND + 1 OR + 1 AND |

S-format is the residual (not U, not J, not C). It is never explicitly tested as a wire; only individual S-format opcodes are tested.

**Format bit patterns:**
```
ir[15:12]   Format
00xx        U  (4 opcodes: 0000-0011)
010x        J  (2 opcodes: 0100-0101)
0110        S  (LB)
0111        S  (LBU)
1000        S  (LW)
1001        S  (SB)
1010        S  (SW)
1011        C  (ALU-RR)
1100        C  (Shift)
1101        C  (Control)
1110        C  (ALU-Imm)
1111        C  (System)
```

### 1.2 Instruction Identity Wires

#### U-format (2 instructions, from ir[15:13])

| Wire | Expression | Bits | Logic Cost |
|---|---|---|---|
| `is_lui` | `fmt_u && !ir[13]` | 15, 14, 13 | NOR + AND-inv (~2 gates) |
| `is_auipc` | `fmt_u && ir[13]` | 15, 14, 13 | NOR + AND (~2 gates) |

#### J-format (2 instructions, from ir[15:12])

| Wire | Expression | Bits | Logic Cost |
|---|---|---|---|
| `is_j` | `fmt_j && !ir[12]` | 15, 14, 13, 12 | ~2 gates on top of fmt_j |
| `is_jal` | `fmt_j && ir[12]` | 15, 14, 13, 12 | ~2 gates on top of fmt_j |

#### S-format (5 instructions, each a 4-bit compare on ir[15:12])

| Wire | Expression | Bits | Logic Cost |
|---|---|---|---|
| `is_lb` | `ir[15:12] == 4'b0110` | 15:12 | 4-bit compare (~2 gates) |
| `is_lbu` | `ir[15:12] == 4'b0111` | 15:12 | 4-bit compare (~2 gates) |
| `is_lw` | `ir[15:12] == 4'b1000` | 15:12 | 4-bit compare (~2 gates) |
| `is_sb` | `ir[15:12] == 4'b1001` | 15:12 | 4-bit compare (~2 gates) |
| `is_sw` | `ir[15:12] == 4'b1010` | 15:12 | 4-bit compare (~2 gates) |

#### C-format group wires (from ir[15:12])

| Wire | Expression | Bits | Logic Cost |
|---|---|---|---|
| `is_alu_rr` | `ir[15:12] == 4'b1011` | 15:12 | 4-bit compare (~2 gates) |
| `is_shift` | `ir[15:12] == 4'b1100` | 15:12 | 4-bit compare (~2 gates) |
| `is_control` | `ir[15:12] == 4'b1101` | 15:12 | 4-bit compare (~2 gates) |
| `is_alu_imm_grp` | `ir[15:12] == 4'b1110` | 15:12 | 4-bit compare (~2 gates) |
| `is_system` | `ir[15:12] == 4'b1111` | 15:12 | 4-bit compare (~2 gates) |

**Note:** 10 separate 4-bit compares (5 S-format + 5 C-format groups). This is the most expensive section of decode. Synthesis can share partial decode (e.g., ir[15:14] == 2'b11 is shared across all C groups), but the 10 distinct values across the full 4-bit space limit sharing.

#### System group (ir[15:12] == 1111, sub from ir[11:9])

| Wire | Expression | Bits | Logic Cost |
|---|---|---|---|
| `is_sei` | `is_system && ir[11:9] == 3'b001` | 15:12, 11:9 | compare + AND (~3 gates) |
| `is_cli` | `is_system && ir[11:9] == 3'b010` | 15:12, 11:9 | compare + AND (~3 gates) |
| `is_reti` | `is_system && ir[11:9] == 3'b011` | 15:12, 11:9 | compare + AND (~3 gates) |
| `is_int` | `is_system && ir[11:9] == 3'b100` | 15:12, 11:9 | compare + AND (~3 gates) |
| `is_wai` | `is_system && ir[11:9] == 3'b101` | 15:12, 11:9 | compare + AND (~3 gates) |
| `is_stp` | `is_system && ir[11:9] == 3'b111` | 15:12, 11:9 | compare + AND (~3 gates) |

#### Control group (ir[15:12] == 1101, sub from ir[11:9])

| Wire | Expression | Bits | Logic Cost |
|---|---|---|---|
| `is_branch` | `is_control && !ir[11]` | 15:12, 11 | compare + AND-inv (~2.5 gates) |
| `is_li` | `is_control && ir[11:9] == 3'b100` | 15:12, 11:9 | compare + AND (~3 gates) |
| `is_jr_jalr` | `is_control && ir[11] && ir[10]` | 15:12, 11, 10 | compare + 2-AND (~2.5 gates) |

### 1.3 Behavioral Property Wires

| Wire | Expression | Inputs | Logic Cost |
|---|---|---|---|
| `is_load` | `is_lb \|\| is_lbu \|\| is_lw` | 3 compares | 3-OR (~1.5 gates) on top of 3 compares |
| `is_store` | `is_sb \|\| is_sw` | 2 compares | 2-OR (~1 gate) on top of 2 compares |
| `is_mem_addr` | `is_load \|\| is_store \|\| is_auipc` | 3 wires | 3-OR (~1.5 gates) |
| `is_byte_load` | `is_lb \|\| is_lbu` | 2 compares | 2-OR (~1 gate) |
| `is_byte_store` | `is_sb` | 1 compare | 0 gates (alias) |
| `is_jump_imm` | `fmt_j` | 1 wire | 0 gates (alias) |
| `is_linking` | `is_jal \|\| (is_jr_jalr && ir[9])` | complex | 2-OR + AND (~2 gates) |
| `is_sign_branch` | `is_branch && ir[10]` | | AND (~1 gate) |
| `branch_inv` | `ir[9]` | 9 | 0 gates (direct bit) |
| `is_shift_rr` | `is_shift && !ir[11]` | | AND-inv (~1 gate) |
| `is_right_shift` | `is_shift && ir[10]` | | AND (~1 gate) |
| `is_arith_shift` | `is_shift && ir[9]` | | AND (~1 gate) |
| `is_slt` | `is_alu_rr && ir[11] && (ir[10] ^ ir[9])` | 15:12, 11, 10, 9 | XOR + 2-AND (~2.5 gates) |
| `is_slt_imm` | `is_alu_imm_grp && ir[11] && (ir[10] ^ ir[9])` | 15:12, 11, 10, 9 | XOR + 2-AND (~2.5 gates) |
| `is_alu_imm` | `is_alu_imm_grp && !(ir[11] && (ir[10] ^ ir[9]))` | 15:12, 11, 10, 9 | NAND-XOR (~2.5 gates) |
| `is_fixed_dest` | `is_alu_imm_grp && ir[11] && \|ir[10:9]` | 15:12, 11, 10, 9 | OR + 2-AND (~2.5 gates) |
| `is_two_cycle` | `!is_system` | 15:12 | INV of 4-bit compare (~2 gates) |

### 1.4 Inline ir Bit Tests (not through is_* wires)

| Location | Expression | Bits Used | Purpose |
|---|---|---|---|
| ALU op select | `ir[11:9]` direct (when `!ir[11]`) | 11:9 | ADD/SUB/AND/OR selection |
| AUIPC lo immediate | `{off6[1:0], 6'b0}` = `{ir[4:3], 6'b0}` | 4:3 | (imm10 << 6) low byte |
| AUIPC hi immediate | `ir[12:5]` | 12:5 | (imm10 << 6) high byte |
| J/JAL lo offset | `{ir[6:0], 1'b0}` | 6:0 | off12[6:0] << 1 |
| J/JAL hi offset | `{{3{ir[11]}}, ir[11:7]}` | 11:7 | sext(off12[11:7]) |
| SLT sub[1] test | `ir[10]` | 10 | SLTU vs SLT |
| LUI lo byte | `{off6[1:0], 6'b0}` = `{ir[4:3], 6'b0}` | 4:3 | imm10 lower bits |
| LUI hi byte | `ir[12:5]` | 12:5 | imm10 upper bits |
| INT vector address | `off6[1:0] + 2'd1` = `ir[4:3] + 2'd1` | 4:3 | Vector ID |
| INT synthesized ir | `{7'b1111100, 5'b00000, take_nmi, 3'd6}` | literal | Synthetic instruction |
| BRK detect at dispatch | `fetch_ir[15:9] == 7'b1111100` | 15:9 | INT/BRK detection |
| Branch condition | `r[7]` (sign branch), `!tmp[8] && r == 8'h00` (zero check) | - | Not ir bits |
| RETI i_bit restore | `tmp[0]` | - | Not ir bits |
| r_sel C I-type test | `ir[14:12] != 3'b011 && !(ir[14:12] == 3'b100 && !ir[11])` | 14:12, 11 | Complex format test |
| w_sel_mux | `is_linking ? LINK_REG : is_fixed_dest ? T0_REG : ir[2:0]` | 2:0, plus decode wires | Dest override |
| r2_sel | `is_store ? ir[2:0] : ir[8:6]` | 2:0, 8:6 | Port 2 source select |
| r2_hi_mux | `is_shift ? 1'b0 : r_hi` | decode wire | Byte select override |

### 1.5 The r_sel Mux (Combinational Priority Logic)

```verilog
if (state == E_MEM_LO || state == E_MEM_HI)
    r_sel = ir[2:0];                              // MEM: rd/rs2
else if (is_reti)
    r_sel = 3'd6;                                 // RETI: banked R6
else if (fmt_c &&
         ir[14:12] != 3'b011 &&                   // not ALU-RR
         !(ir[14:12] == 3'b100 && !ir[11]))        // not shift-RR
    r_sel = ir[2:0];                              // C I-type: rd/rs
else if (fmt_c || fmt_u || fmt_j)
    r_sel = ir[5:3];                              // C R-type, U, J: rs1
else
    r_sel = ir[11:9];                             // S-format: rs1
```

This is a 5-way priority mux. The most complex condition is the C I-type test on line 3, which tests `fmt_c` AND (not ALU-RR) AND (not shift-register). Unpacking:
- `fmt_c` = `ir[15] && (ir[14] || (ir[13] && ir[12]))` (3 gates)
- `ir[14:12] != 3'b011` (3-bit compare, ~1.5 gates)
- `!(ir[14:12] == 3'b100 && !ir[11])` (3-bit compare + AND-inv + INV, ~2.5 gates)
- Combined: ~7 gates deep for the select condition

The "C I-type" condition identifies instructions where the register at [2:0] is the source (read port), not the destination. This is: all of C-format EXCEPT ALU-RR (grp=011) and shift-register (grp=100, sub[2]=0). So: control group, ALU-imm group, system group, shift-immediate, and the sub[2]=1 entries in shift group.

## 2. Property Usage Map

### 2.1 Properties Used in Multiple States

| Property | E_EXEC_LO | E_EXEC_HI | E_MEM_LO | E_MEM_HI | r_sel | w_sel | Other |
|---|---|---|---|---|---|---|---|
| `is_mem_addr` | Y (addr lo) | Y (addr hi) | - | - | - | - | FSM transition |
| `is_auipc` | Y (special) | Y (special) | - | - | - | - | - |
| `is_jr_jalr` | Y (addr lo) | Y (addr hi) | - | - | - | - | FSM transition |
| `is_linking` | Y (save pc) | Y (save pc) | - | - | - | Y (w_sel) | - |
| `is_alu_rr` | Y (ALU lo) | Y (ALU hi) | - | - | - | - | ALU op select |
| `is_alu_imm` | Y (ALU lo) | Y (ALU hi) | - | - | - | - | ALU op select |
| `is_slt` | Y (w_data) | Y (w_data) | - | - | - | - | ALU op select |
| `is_slt_imm` | Y (w_data) | Y (w_data) | - | - | - | - | ALU op select |
| `is_shift` | Y (shift lo) | Y (shift hi) | - | - | - | - | r2_hi_mux |
| `is_right_shift` | Y | Y | - | - | Y (r_hi) | - | - |
| `is_arith_shift` | Y | Y | - | - | - | - | - |
| `is_li` | Y (lo byte) | Y (hi byte) | - | - | - | - | - |
| `is_lui` | Y (lo byte) | Y (hi byte) | - | - | - | - | - |
| `is_branch` | Y (target lo) | Y (decide) | - | - | - | - | FSM: tmp[8] |
| `is_jump_imm` | Y (target lo) | Y (jump) | - | - | - | - | FSM transition |
| `is_store` | - | - | - | - | - | - | E_MEM: rwb, w_we |
| `is_byte_load` | - | - | - | Y (sign ext) | Y (r_hi) | - | - |
| `is_byte_store` | - | - | Y (complete) | - | - | - | FSM: skip HI |
| `is_reti` | Y (read R6) | Y (jump) | - | - | Y (r_sel=6) | - | FSM: i_bit |
| `is_int` | Y (save lo) | Y (save hi) | - | - | - | - | - |
| `is_fixed_dest` | - | - | - | - | - | Y (w_sel) | - |

### 2.2 Timing-Critical Properties

The critical path is `regfile read -> dout -> uio_out`. Properties on this path:
- `r2_sel = is_store ? ir[2:0] : ir[8:6]` -- `is_store` feeds the r2_sel mux which is on the critical dout path
- `r2_hi_mux = is_shift ? 1'b0 : r_hi` -- `is_shift` feeds r2_hi

The `r_sel` mux is also timing-sensitive since it determines which register is read for `r` (port 1), which feeds alu_a and many other paths.

### 2.3 Properties Used Only Once

| Property | Where Used |
|---|---|
| `is_j` | Nowhere directly (only through `fmt_j`/`is_jump_imm`) |
| `is_lbu` | E_MEM_HI: sign vs zero extend |
| `is_sei` | FSM E_EXEC_LO: i_bit <= 1 |
| `is_cli` | FSM E_EXEC_LO: i_bit <= 0 |
| `is_wai` | `waiting` output, E_EXEC_LO else-else |
| `is_stp` | `stopped` output, E_EXEC_LO else-else |
| `is_sign_branch` | E_EXEC_HI branch condition |
| `branch_inv` | E_EXEC_HI branch condition |
| `is_shift_rr` | `shamt` mux |
| `is_two_cycle` | FSM E_EXEC_LO transition |
| `is_lw`, `is_lb`, `is_sb`, `is_sw` | Only through behavioral composites |

## 3. Decode Cost Hotspots

### 3.1 The S-format Opcode Scatter (Highest Cost)

The five S-format opcodes (0110, 0111, 1000, 1001, 1010) span ir[15:12] values that have no clean bit pattern. Each requires a full 4-bit equality compare. These 5 compares produce `is_lb`, `is_lbu`, `is_lw`, `is_sb`, `is_sw`, which are then OR'd into behavioral properties:

```
is_load       = is_lb || is_lbu || is_lw      (3 compares -> 3-OR)
is_store      = is_sb || is_sw                (2 compares -> 2-OR)
is_mem_addr   = is_load || is_store || is_auipc  (adds another level)
is_byte_load  = is_lb || is_lbu               (2 compares -> 2-OR)
is_byte_store = is_sb                         (1 compare)
```

**Total cost: ~10 gates for individual compares + ~6 gates for composites = ~16 gates.**

The S-format range 0110..1010 straddles the ir[15] boundary (0110, 0111 have ir[15]=0; 1000, 1001, 1010 have ir[15]=1). This prevents any simple bit-range test.

### 3.2 The fmt_c Expression (Medium Cost)

`fmt_c = ir[15] && (ir[14] || (ir[13] && ir[12]))` needs to detect 1011..1111 while excluding 1000..1010 (which are S-format). This is inherently awkward because S-format instructions intrude into the ir[15]=1 space.

If S-format were not in the way, `fmt_c` could simply be `ir[15]` (1 gate) or `ir[15:14] == 2'b11` (1 gate). Instead it's ~3 gates.

### 3.3 The is_slt / is_slt_imm / is_alu_imm / is_fixed_dest Cluster (Medium Cost)

These four wires all test bits within the ALU-imm group (ir[15:12]=1110) or ALU-RR group (ir[15:12]=1011), combined with ir[11:9] sub-opcode bits. The SLT variants specifically test `ir[11] && (ir[10] ^ ir[9])`, which identifies sub-opcodes 101 and 110 (SLT/SLTU and SLTIF/SLTIUF).

The XOR test arises because SLT (sub=101) and SLTU (sub=110) are not contiguous in a simple bit pattern. If they were at sub=110 and sub=111, the test would be `ir[11] && ir[10]` (simpler).

**Cost: ~4 XOR gates + ~6 AND gates = ~10 gates total for the cluster.**

### 3.4 The r_sel Mux (Medium Cost)

The 5-way priority mux with complex C I-type condition:

```verilog
fmt_c && ir[14:12] != 3'b011 && !(ir[14:12] == 3'b100 && !ir[11])
```

This condition has to exclude ALU-RR (grp 011) and register-shift (grp 100, sub[2]=0). It's a 3-level AND with negations, total ~7 gates deep.

**The r_sel mux must resolve before the regfile read, so its depth matters for timing.**

### 3.5 The is_linking Wire (Low-Medium Cost)

`is_linking = is_jal || (is_jr_jalr && ir[9])` combines two different format contexts (J-format and C-format control group). is_jal requires fmt_j decode; is_jr_jalr requires is_control decode. These share no bit patterns. Cost: ~2 gates on top of the constituent wires, but those wires are themselves ~4-5 gates deep.

**Total depth: ~6-7 gates.** This feeds the w_sel_mux (dest register selection), which is on the write path.

## 4. Encoding Inefficiencies

### 4.1 S-format Opcode Scatter (Major)

**Current:** S-format opcodes are 0110, 0111, 1000, 1001, 1010 in ir[15:12]. This spans 5 consecutive values but straddles the ir[15]=0/1 boundary. There is no single bit pattern that matches exactly these 5 values.

**Impact:** Every S-format behavioral property (is_load, is_store, is_mem_addr, is_byte_load, is_byte_store) must be built from individual 4-bit compares OR'd together. The `fmt_c` expression is more complex because it must exclude the three S-format values in the ir[15]=1 range.

**Root cause:** The S-format uses opcodes 6-10, while U uses 0-3, J uses 4-5, and C uses 11-15. If S-format were entirely within ir[15]=0 or entirely within ir[15]=1, bit-level tests would be simpler.

### 4.2 SLT/SLTU Sub-Opcode Positions (Minor)

**Current:** In the ALU-RR group (ir[15:12]=1011), SLT is sub=101 and SLTU is sub=110. In ALU-imm (ir[15:12]=1110), SLTIF is sub=101 and SLTIUF is sub=110. The test for "is this an SLT variant" is `ir[11] && (ir[10] ^ ir[9])`, which requires an XOR.

**If SLT/SLTU were at sub=110/111:** The test would be `ir[11] && ir[10]` (2-input AND, no XOR needed). Similarly for SLTIF/SLTIUF.

**But:** This would displace XOR (currently sub=100) and XORI/XORIF (currently sub=100 and sub=111). The current layout has ADD=000, SUB=001, AND=010, OR=011, XOR=100, SLT=101, SLTU=110 — the first 5 share the ALU op directly via ir[11:9] when `!ir[11]`, and only when ir[11]=1 do we need special handling. Moving SLT/SLTU to 110/111 would break this property: XOR would need to move to 101, but then `ir[11]=1` would include both XOR (needing alu_op=4) and SLT (needing alu_op=1), complicating the ALU op select logic.

### 4.3 r_sel C I-type Condition (Medium)

**Current:** The r_sel mux tests `fmt_c && ir[14:12] != 3'b011 && !(ir[14:12] == 3'b100 && !ir[11])` to identify C-format instructions where [2:0] is the source register (not destination). This is complex because:

1. ALU-RR (grp=011) has rs1 at [5:3], not [2:0]
2. Shift-register (grp=100, sub[2]=0) has rs1 at [5:3]
3. All other C-format has rd/rs at [2:0]

The condition would be simpler if the groups were arranged so that "R-type" (rs1 at [5:3]) were a contiguous bit range of grp.

### 4.4 Non-Contiguous Bit Tests (Minor)

The AUIPC immediate uses `ir[12:5]` for the high byte — this is clean and contiguous. The J-format offset uses `ir[6:0]` (low) and `ir[11:7]` (high), which is also clean. Most field extractions use contiguous bit ranges, which is good.

The one notable non-contiguity is in `is_linking`, which combines two entirely separate format contexts (J-format and C-format control group). This is inherent in having linking behavior in two formats.

### 4.5 The is_mem_addr Composite (Medium)

`is_mem_addr = is_load || is_store || is_auipc` combines S-format loads/stores with U-format AUIPC. AUIPC uses the same address computation path (ALU add with PC), but otherwise has nothing in common with loads/stores. This composite forces three different format detections to be OR'd.

If AUIPC could share a bit pattern with loads/stores (e.g., if AUIPC were in S-format), this would simplify, but AUIPC needs 10 immediate bits which doesn't fit S-format's 6-bit offset.

## 5. Proposed Encoding Changes

### Proposal A: Consolidate S-format into ir[15]=0 space

**Current:**
```
0000-0011  U-format  (4 opcodes)
0100-0101  J-format  (2 opcodes)
0110-1010  S-format  (5 opcodes, straddles ir[15] boundary)
1011-1111  C-format  (5 groups)
```

**Proposed:** Move the three ir[15]=1 S-format opcodes (LW=1000, SB=1001, SW=1010) down into the ir[15]=0 range by swapping with unused U/J space or compressing formats.

**Option A1: Pack S into 0110..1010 but remap values**

This doesn't help because the problem is the 0/1 boundary at ir[15], not the specific values.

**Option A2: Move S-format entirely to 0100..1000**

```
0000-0011  U-format  (unchanged)
0100       J (was 0100, unchanged)
0101       JAL (was 0101, unchanged)
0110       LB   (unchanged)
0111       LBU  (unchanged)
1000       LW   (unchanged)
1001       SB   (unchanged)
1010       SW   (unchanged)
```

This is the current layout. The problem is that 1000-1010 have ir[15]=1.

**Option A3: Swap J and the ir[15]=1 S-format instructions**

Rearrange so all S-format have ir[15]=0:

```
0000-0011  U-format  (4 opcodes)
0100       LB
0101       LBU
0110       LW
0111       SB
1000       SW
1001       J     (was 0100)
1010       JAL   (was 0101)
1011-1111  C-format (5 groups, unchanged)
```

Now S-format = 0100..1000. Still straddles ir[15]. Not better.

**Option A4: Compress U-format to free low opcodes**

U-format currently uses a 3-bit prefix, giving 4 opcodes (0000-0011) for just 2 instructions (LUI, AUIPC). If U-format used a 4-bit prefix (2 opcodes: 0000, 0001), that frees 0010 and 0011, allowing S-format to fit entirely in the 0010..0110 range:

```
0000       LUI   (was 000x, loses 1 immediate bit)
0001       AUIPC (was 001x, loses 1 immediate bit)
0010       LB
0011       LBU
0100       LW
0101       SB
0110       SW
0111       J     (was 0100)
1000       JAL   (was 0101)
1001-1111  C-format (7 groups! Currently only 5 used)
```

**Problem:** LUI and AUIPC lose 1 immediate bit (imm10 -> imm9). The immediate becomes 9 bits, shifted by 6 = 15-bit range. This means AUIPC+LW can only address ±16 KB instead of ±32 KB. For a 16-bit address space (64 KB), this is a significant loss.

**Alternatively, keep U at 3-bit prefix but accept overlap:**

Can't; 3-bit prefix means the low 13 bits are payload, and opcode is bits [15:13]. This necessarily gives 8 possible prefixes (000-111), and you need the rest of the ir[15:12] space for everything else.

**Option A5: Reorder S and C so all S-format is in the ir[15:14]=01 range**

```
00xx       U-format  (4 opcodes, unchanged)
0100       J
0101       JAL
0110       LB
0111       LBU
10xx       ??? need to fit S remaining + C
```

Can't fit 3 more S-format + 5 C-format in 8 opcodes (1000-1111).

**Option A6: Assign S-format a decodable pattern within ir[15:12]**

What if S-format opcodes were arranged so they share a bit pattern?

Current S-format values: 0110, 0111, 1000, 1001, 1010.
If rearranged to: 0100, 0101, 0110, 0111, 1000 -- still straddles.
If rearranged to: 1000, 1001, 1010, 1011, 1100 -- all have ir[15]=1, but steals 2 C-format groups.

**Option A7: Move S-format to ir[15:14]=10**

```
00xx        U-format  (4 opcodes: LUI, AUIPC, unused, unused)
010x        J-format  (2 opcodes: J, JAL)
0110-0111   Available (2 opcodes for future expansion)
10000       LB
10001       LBU
10010       LW
10011       SB
10100       SW
```

Wait, we only have 4 bits (ir[15:12]), not 5. So ir[15:14]=10 gives 1000-1011 (4 values). We need 5 S-format instructions. That's one too many.

**Option A8: Merge LB and LBU**

Instead of separate LB (sign-extend) and LBU (zero-extend) opcodes, make sign/zero extension a bit in the offset field (e.g., sacrifice 1 bit of offset). This reduces S-format to 4 instructions (LB_x, LW, SB, SW), fitting in ir[15:14]=10.

```
00xx        U-format  (unchanged)
010x        J-format  (unchanged)
0110-0111   Free (2 opcodes)
1000        LB/LBU    (extension mode in offset bit)
1001        LW
1010        SB
1011        SW
1100-1111   C-format  (4 groups -- one fewer than current)
```

**Problem:** Losing a C-format group means one of the current 5 C-format groups (ALU-RR, Shift, Control, ALU-Imm, System) must merge with another. The most natural merge is System into Control (since System instructions have unused payload bits), but this may not save anything.

Also, merging LB/LBU reduces offset range from 6 bits to 5 bits for byte loads, which is a functionality loss.

**Option A9: Separate loads from stores by bit pattern**

Currently: loads = {0110, 0111, 1000}, stores = {1001, 1010}. What if we assigned:
- Loads: ir[12]=0 means load, ir[12]=1 means store (within S-format range)
- Or: ir[13]=0 means load, ir[13]=1 means store

If S-format were moved to opcode range where this works:

```
S-format at 0110-1010 (current). Let's try encoding load/store in ir[12]:
  0110 (LB, load, ir[12]=0)
  0111 (LBU, load, ir[12]=1) -- but ir[12]=1 would mean store!
```

That doesn't work because LBU is a load but has ir[12]=1.

**What if we reorder within the existing range?**

```
0110 = LB  (load)
0111 = LW  (load)  -- was 1000
1000 = LBU (load)  -- was 0111
1001 = SB  (store, unchanged)
1010 = SW  (store, unchanged)
```

Now loads are {0110, 0111, 1000} and stores are {1001, 1010}. Still scattered.

Actually, let's look at what bit patterns would make is_load and is_store simpler:

**is_store currently needs:** `ir[15:12] == 1001 || ir[15:12] == 1010`. These share ir[15:13]=100 and ir[12] distinguishes them. So `is_store = ir[15:13] == 3'b100 && ir[12:12] != 2'b11`... no, that's not simpler.

Actually: SB=1001, SW=1010. They share: ir[15]=1, ir[14]=0, ir[13]=0. The difference is ir[12:11]. So `is_store = (ir[15:13] == 3'b100) && (ir[12] || ir[11])`. Hmm, that's not right.

Wait: SB=1001, SW=1010. In binary:
- SB: 1-0-0-1
- SW: 1-0-1-0

`is_store = ir[15] && !ir[14] && (ir[13] ^ ir[12])` -- no, SB has 13=0,12=1 and SW has 13=1,12=0. So `ir[13] ^ ir[12]` = 1 for both. And `ir[15] && !ir[14] && (ir[13] ^ ir[12])` -- but 1000 (LW) has ir[13]=0, ir[12]=0, so XOR=0, excluded. And 1011 (ALU-RR) has ir[13]=1, ir[12]=1, so XOR=0, excluded. So this works!

`is_store = ir[15] && !ir[14] && (ir[13] ^ ir[12])` -- 3 gates. This is the CURRENT encoding, just not exploited!

Let's verify:
- 1001 (SB): 1 && !0 && (0^1)=1 -> yes
- 1010 (SW): 1 && !0 && (1^0)=1 -> yes
- 1000 (LW): 1 && !0 && (0^0)=0 -> no, correct
- 1011 (ALU-RR): 1 && !0 && (1^1)=0 -> no, correct
- 0110 (LB): 0 -> no, correct

This is a valid simplification of the existing encoding! Currently `is_store = is_sb || is_sw` requires two 4-bit compares and an OR (~5 gates). The XOR formulation uses 3 gates.

Similarly, can we find a pattern for `is_load`? Loads are 0110, 0111, 1000.
- 0110: ir[15:12] = 0-1-1-0
- 0111: ir[15:12] = 0-1-1-1
- 1000: ir[15:12] = 1-0-0-0

These have no obvious shared bit pattern. The first two share ir[15:13]=011, but LW (1000) is completely different.

**This is the core problem:** LB/LBU sit in the 0-range (0110, 0111) while LW sits in the 1-range (1000). If LW were moved to 0101 and J/JAL were moved to 1000/1001... but then J/JAL would conflict with other things.

### Proposal B: Exploit XOR Pattern for is_store (Quick Win)

**Current:** `is_store = is_sb || is_sw` requires two 4-bit equality compares OR'd.

**Proposed:** `is_store = ir[15] && !ir[14] && (ir[13] ^ ir[12])`.

This tests a 4-bit pattern with cheaper logic (no equality compare, just AND/XOR). The expression uses 3 simple gates instead of ~5 gates for two compares + OR.

**Savings: ~2 gates.**

**Wires that simplify:**
- `is_store` itself (direct replacement)
- `is_mem_addr = is_load || is_store || is_auipc` (cheaper is_store input)

**Wires that get worse:** None.

**Constraint:** This is a pure decode optimization; no encoding change needed. The current encoding already has this property.

**However**, synthesis may already find this optimization. To verify, one would need to check the synthesized netlist. If synthesis already exploits it, explicitly writing it won't help. But making it explicit may help synthesis in context.

### Proposal C: Exploit Bit Patterns for is_load

**Current:** `is_load = is_lb || is_lbu || is_lw` (three 4-bit compares + 3-OR).

Loads are 0110 (LB), 0111 (LBU), 1000 (LW). Let's look for shared patterns:

- `!fmt_u && !fmt_j && !fmt_c && !is_store` -- this is the residual approach. Cost: ~4 NOR/NAND of existing wires.
- But `is_store` itself is derived, creating a circular dependency.

An alternative approach: S-format is the residual format. We know `fmt_s = !fmt_u && !fmt_j && !fmt_c`. Let's compute this:
- `!fmt_u = ir[15] || ir[14]` (NOR inverted = OR)
- `!fmt_j`: fmt_j = `!ir[15] && ir[14] && !ir[13]`, so !fmt_j = `ir[15] || !ir[14] || ir[13]`
- `!fmt_c`: fmt_c = `ir[15] && (ir[14] || (ir[13] && ir[12]))`, so !fmt_c = `!ir[15] || (!ir[14] && !(ir[13] && ir[12]))`

Then `is_load = fmt_s && !is_store`. But computing fmt_s explicitly is expensive (~6-8 gates), and then we need is_store on top of it. Not clearly better.

**Better approach:** is_load includes the only three S-format values where ir[15:13] matters differently than stores. Since stores are {1001, 1010} and loads are {0110, 0111, 1000}, we can note:

`is_load = (ir[15:13] == 3'b011) || (ir[15:12] == 4'b1000)`

This combines a 3-bit compare (LB/LBU share prefix 011) with a single 4-bit compare (LW). Cost: ~1.5 + 2 + 1 = ~4.5 gates. Current cost: ~3*2 + 1.5 = ~7.5 gates. **Savings: ~3 gates.**

**is_byte_load** would also simplify: `is_byte_load = ir[15:13] == 3'b011` (~1.5 gates, currently ~2*2 + 1 = ~5 gates). **Savings: ~3.5 gates.**

**But:** ir[15:13]==011 is specifically `!ir[15] && ir[14] && ir[13]`. Note that J-format is `!ir[15] && ir[14] && !ir[13]`. So byte loads differ from J-format only in ir[13]. This means `is_byte_load = !ir[15] && ir[14] && ir[13]` (3-input AND with one inversion, ~1.5 gates). This is very cheap.

And `is_load = is_byte_load || is_lw`. Since is_lw is a single 4-bit compare, this is ~3.5 gates total. Better than 3 separate compares.

**This is also a pure decode optimization requiring no encoding change.** The current encoding already supports it.

### Proposal D: Rearrange S-format to Enable Simple Load/Store Detection

**Goal:** Make is_load, is_store, and is_mem_addr detectable via simple bit tests.

**Proposed encoding:**
```
0000-0011  U-format (unchanged)
0100-0101  J-format (unchanged)
0110       LB    (unchanged)
0111       LBU   (unchanged)
1000       SB    (was 1001) -- stores get ir[15]=1, loads get ir[15]=0
1001       SW    (was 1010) -- stores
1010       LW    (was 1000) -- loads: 0110, 0111, 1010
1011-1111  C-format (unchanged)
```

Now:
- `is_store = ir[15:14] == 2'b10 && !ir[13]` -- SB=1000, SW=1001
  - Actually: SB=1000, SW=1001: `ir[15] && !ir[14] && !ir[13]` (3-AND, ~1.5 gates)
- `is_load = (ir[15:13] == 3'b011) || ir[15:12] == 4'b1010`
  - Not much better than current.

**Alternative: group all loads together and all stores together:**
```
0110  LB
0111  LBU
1000  LW    -- all loads are 0110, 0111, 1000 (current: same)
1001  SB    -- unchanged
1010  SW    -- unchanged
```

This is the current encoding. The problem is structural: with 3 loads and 2 stores across 5 consecutive opcodes, there's no bit arrangement that separates them cleanly unless we're willing to make them non-consecutive.

**What if stores came first?**
```
0110  SB
0111  SW
1000  LB
1001  LBU
1010  LW
```

Now:
- `is_store = ir[15:13] == 3'b011` (same as current is_byte_load pattern!) = `!ir[15] && ir[14] && ir[13]` (~1.5 gates)
- `is_load = ir[15] && !ir[14]` ... but this includes 1011 (ALU-RR). So `is_load = ir[15] && !ir[14] && !ir[13]` when checking 1000, 1001, but 1010 has ir[13]=1.

Not clean either. The problem is 3+2 = 5 values in 4 bits -- the pigeonhole principle means at least one group won't have a single bit pattern.

**What if we sacrifice one opcode to make groups of 4 + 4?**
```
0100  LB
0101  LBU
0110  LW
0111  SB (or J)
1000  SW (or JAL)
```

This would require moving J/JAL, which disrupts J-format's 12-bit offset (they need full 12-bit payload = 4-bit prefix). Can't easily move them.

### Proposal E: Move SLT/SLTU to sub=110/111 (Minor Savings)

**Current:** ALU-RR sub-opcodes: ADD=000, SUB=001, AND=010, OR=011, XOR=100, SLT=101, SLTU=110.

The SLT test is `ir[11] && (ir[10] ^ ir[9])` because SLT=101 and SLTU=110 both satisfy this. If instead:

**Proposed:** ADD=000, SUB=001, AND=010, OR=011, SLT=100, SLTU=101, XOR=110.

Then:
- `is_slt = is_alu_rr && ir[11] && !ir[10]` (no XOR needed)
- XOR detection changes: was `ir[11:9] == 3'b100`, now `ir[11:9] == 3'b110`
- ALU op select: currently `if (!ir[11]) alu_op = ir[11:9]` works for ADD(000), SUB(001), AND(010), OR(011). XOR at 100 is handled by `else alu_op = 3'd4`. With XOR at 110, `!ir[11]` would still handle ADD/SUB/AND/OR (000-011), and XOR at 110 would need `alu_op = 3'd4` in the else. But SLT at 100 also has ir[11]=1, and needs alu_op=1 (SUB). This is currently handled by the `is_slt` override that sets alu_op=1.

**Net effect:** The `is_slt` test loses 1 XOR gate (~1 gate), but the ALU op override logic is unchanged. The `is_slt_imm` test also loses 1 XOR gate. `is_alu_imm` and `is_fixed_dest` also simplify by the same amount.

**However,** the same change must apply to the ALU-imm group: SLTIF and SLTIUF would move to sub=100/101, and XORIF would move to sub=110 or 111.

**Current ALU-imm sub-opcodes:**
```
000 ADDI
010 ANDI
011 ORI
100 XORI
101 SLTIF
110 SLTIUF
111 XORIF
```

**Proposed ALU-imm sub-opcodes:**
```
000 ADDI
010 ANDI
011 ORI
100 SLTIF
101 SLTIUF
110 XORI     (was 100)
111 XORIF    (unchanged)
```

The `is_fixed_dest` test (identifies instructions writing to T0 instead of rd at [2:0]) currently catches sub=101,110,111 via `ir[11] && |ir[10:9]`. With the proposed layout, fixed-dest instructions are: SLTIF=100, SLTIUF=101, XORIF=111. This is `ir[11] && (!ir[10] || (ir[10] && ir[9]))` = `ir[11] && (!ir[10] || ir[9])` = `ir[11] && (ir[10] -> ir[9])` which can also be written `ir[11] && !(ir[10] && !ir[9])`. That's the same complexity as before.

Hmm wait: the proposed has XORI at sub=110 and it is NOT fixed-dest (it writes to rd/rs at [2:0], modifying in-place). And XORIF at sub=111 IS fixed-dest. So fixed-dest = {100, 101, 111} = `ir[11] && !(ir[10] && !ir[9])` -- same as `ir[11] && (!ir[10] || ir[9])`. Cost: AND-inv + OR + AND = ~2.5 gates, same as current `ir[11] && |ir[10:9]` (OR + AND = 2 gates).

Actually the current `is_fixed_dest = ir[11] && |ir[10:9]` catches sub={101, 110, 111} -- all three have ir[11]=1 and at least one of ir[10:9]=1. Cost: OR + AND = 2 gates. The proposed version catches {100, 101, 111}: `ir[11] && (!ir[10] || ir[9])`. Cost: INV + OR + AND = 2.5 gates. **Worse by 0.5 gates.**

The `is_alu_imm` test (identifies non-SLT ALU-imm instructions that write to rd) currently is `is_alu_imm_grp && !(ir[11] && (ir[10] ^ ir[9]))`. With the proposed change, non-SLT is {000, 010, 011, 110, 111}: `is_alu_imm_grp && !(ir[11] && !ir[10])`. This is simpler: NAND + AND = 2 gates vs NAND + XOR + AND = 2.5 gates. **Better by 0.5 gates.**

**Net for Proposal E:**
- `is_slt`: saves 1 XOR gate (~1 gate)
- `is_slt_imm`: saves 1 XOR gate (~1 gate)
- `is_alu_imm`: saves ~0.5 gates
- `is_fixed_dest`: costs ~0.5 gates
- **Net savings: ~2 gates**
- **Risk: Low.** Only sub-opcode reordering within ALU groups; no format changes.
- **Constraint:** Tests and assembler must be updated to match new sub-opcode assignments.

### Proposal F: Simplify the r_sel C I-type Condition

The current condition for "C-format, read from [2:0]" is:

```verilog
fmt_c && ir[14:12] != 3'b011 && !(ir[14:12] == 3'b100 && !ir[11])
```

This excludes ALU-RR (grp=011, R-type with rs1 at [5:3]) and shift-register (grp=100 with sub[2]=0, R-type with rs1 at [5:3]).

**If ALU-RR and shift-register were in adjacent groups, the exclusion would be simpler.** Currently ALU-RR is grp=011 and shift is grp=100 -- they are adjacent! The test could be:

```verilog
fmt_c && !((ir[14:12] == 3'b011 || (ir[14:12] == 3'b100 && !ir[11])))
```

Alternatively, note that the R-type instructions (ALU-RR and shift-register) are exactly those where grp={011, 100} and sub[2]=0 for shifts. Let's tabulate which C-format grp values use [5:3] vs [2:0] for r_sel:

| grp (ir[14:12]) | Instruction Class | r_sel source |
|---|---|---|
| 011 | ALU-RR | ir[5:3] (rs1, R-type) |
| 100, sub[2]=0 | Shift-register | ir[5:3] (rs1, R-type) |
| 100, sub[2]=1 | Shift-immediate | ir[2:0] (rd, I-type) |
| 101 | Control | ir[2:0] (rs, I-type) or ir[5:3] (for JR/JALR rs1) |
| 110 | ALU-Imm | ir[2:0] (rd/rs, I-type) |
| 111 | System | ir[2:0] (rd, I-type) -- but unused for most |

Wait, looking at control group: JR/JALR have rs at [2:0] and read it. Actually no -- looking at the r_sel logic more carefully:

For the control group (grp=101):
- Branches (sub 0xx): rs at [2:0], reads via r_sel=ir[2:0] (I-type path)
- LI (sub 100): rd at [2:0], no read needed
- JR/JALR (sub 11x): rs at [2:0], reads via r_sel=ir[5:3] path? No!

Wait, let me re-read the r_sel logic:

```verilog
if (state == E_MEM_LO || state == E_MEM_HI)
    r_sel = ir[2:0];
else if (is_reti)
    r_sel = 3'd6;
else if (fmt_c && ir[14:12] != 3'b011 && !(ir[14:12] == 3'b100 && !ir[11]))
    r_sel = ir[2:0];         // C I-type: rd/rs at [2:0]
else if (fmt_c || fmt_u || fmt_j)
    r_sel = ir[5:3];         // C R-type, U, J: rs1 at [5:3]
else
    r_sel = ir[11:9];        // S-format: rs1 at [11:9]
```

JR/JALR: grp=101 (control), sub=11x. The C I-type condition is: `fmt_c && ir[14:12] != 011 && !(ir[14:12] == 100 && !ir[11])`. For control group (101), first check: 101 != 011 -> true. Second check: 101 == 100 -> false. So the C I-type condition is TRUE for control group -> r_sel = ir[2:0].

But JR/JALR needs rs at [2:0] to be read... and it IS read via ir[2:0]. Looking at the JR encoding: `[1101110][off6:6][rs:3]`. rs is at [2:0]. And in E_EXEC_LO for is_jr_jalr, `alu_a = r` -- reads from r_sel which is ir[2:0] for C I-type. The payload is [off6:6] at [8:3]. OK so JR/JALR does read from [2:0] -- it's an I-type instruction. Good.

Now, looking at ALU-RR: `[1011][sub:3][rs2:3][rs1:3][rd:3]`. In E_EXEC_LO, `alu_a = r` reads rs1 which is at [5:3]. The r_sel logic gives ir[5:3] for ALU-RR because the C I-type condition excludes grp=011.

For shift-register: `[1100][0xx][rs2:3][rs1:3][rd:3]`. rs1 at [5:3], same R-type layout. The C I-type condition excludes grp=100 when sub[2]=0 (!ir[11]).

So the complex r_sel condition is specifically distinguishing:
- R-type (rs1 at [5:3]): ALU-RR (grp=011) and shift-register (grp=100, sub[2]=0)
- I-type (rd/rs at [2:0]): everything else in C-format

**Proposed simplification via encoding change:** If ALU-RR were assigned grp=100 and shifts were grp=011, then the R-type groups would be {100 (all), 011 (sub[2]=0)}. That's the same complexity.

**Alternative:** If shift-register and shift-immediate were in separate groups, the condition would simplify. But that wastes a group.

**Alternative:** If all R-type C-format instructions had grp[2]=0 (ir[14]=0), we could test `fmt_c && ir[14]` for I-type (or `fmt_c && !ir[14]` for R-type). Currently:
- R-type groups: 011 (ir[14]=0), 100 sub[2]=0 (ir[14]=1) -- they span both ir[14] values.

If instead:
- R-type groups: 000, 001 (ir[14]=0)
- I-type groups: 1xx (ir[14]=1)

This would be a major rearrangement. Current grp assignments: 011=ALU-RR, 100=Shift, 101=Control, 110=ALU-Imm, 111=System. But grp=000, 001, 010 are unused (they correspond to ir[15:12]=1000, 1001, 1010 which are currently S-format). Wait no -- C-format has ir[15]=1 AND the fmt_c condition. Let me re-examine.

C-format = ir[15:12] in {1011, 1100, 1101, 1110, 1111}. The grp field is ir[14:12]:
- 1011: grp = 011
- 1100: grp = 100
- 1101: grp = 101
- 1110: grp = 110
- 1111: grp = 111

Grp values 000, 001, 010 would require ir[15:12] = {1000, 1001, 1010}, but those are S-format (LW, SB, SW). So we can't use grp 000-010 for C-format without moving S-format.

**Conclusion:** The r_sel condition's complexity is fundamentally tied to the S-format/C-format boundary. Without moving S-format (which is expensive, see Proposal D), we can't simplify the r_sel mux through encoding changes alone.

### Proposal G: Simplify is_mem_addr by Encoding AUIPC in S-Format

**Current:** `is_mem_addr = is_load || is_store || is_auipc`. AUIPC is in U-format (00xx) while loads/stores are S-format (0110-1010). This composite ORs three different format detections.

**Proposed:** If AUIPC were removed from the is_mem_addr composite (i.e., if its address computation didn't share the same execute path as loads/stores), the composite would just be `fmt_s` (the residual format), detectable as `!fmt_u && !fmt_j && !fmt_c`.

But AUIPC genuinely shares the address computation path (add offset to base), so removing it from is_mem_addr would require duplicating the address computation logic, which would cost more gates than the decode savings.

**Not recommended.**

### Proposal H: Direct Bit Tests for Behavioral Properties (No Encoding Change)

Several behavioral properties can be computed with simpler expressions than the current OR-of-compares, using the existing encoding.

#### H1: is_byte_load

**Current:** `is_byte_load = is_lb || is_lbu` (two 4-bit compares + OR, ~5 gates).

**Proposed:** `is_byte_load = !ir[15] && ir[14] && ir[13]` (tests ir[15:13]==011, ~1.5 gates).

This matches exactly LB (0110) and LBU (0111), since these are the only two opcodes with ir[15:13]=011.

**Savings: ~3.5 gates.**

#### H2: is_store

**Current:** `is_store = is_sb || is_sw` (two 4-bit compares + OR, ~5 gates).

**Proposed:** `is_store = ir[15] && !ir[14] && (ir[13] ^ ir[12])`.

SB=1001: 1,0,0,1 -> 1 && 1 && (0^1)=1 -> yes
SW=1010: 1,0,1,0 -> 1 && 1 && (1^0)=1 -> yes
LW=1000: 1,0,0,0 -> 1 && 1 && (0^0)=0 -> no
ALU-RR=1011: 1,0,1,1 -> 1 && 1 && (1^1)=0 -> no

**Savings: ~2 gates.** (XOR + 2-AND = 3 gates vs 2 compares + OR = 5 gates)

#### H3: is_load

**Current:** `is_load = is_lb || is_lbu || is_lw` (three 4-bit compares + 3-OR, ~7.5 gates).

**Proposed:** `is_load = is_byte_load || is_lw` where `is_byte_load = !ir[15] && ir[14] && ir[13]`.

Cost: ~1.5 (byte_load) + 2 (is_lw compare) + 1 (OR) = ~4.5 gates.

**Savings: ~3 gates.**

Or, even more directly: `is_load = (!ir[15] && ir[14] && ir[13]) || (ir[15:12] == 4'b1000)`.

#### H4: fmt_c (verification)

`fmt_c = ir[15] && (ir[14] || (ir[13] && ir[12]))` -- this is already fairly minimal. Can we do better?

C-format = {1011, 1100, 1101, 1110, 1111}. This is "ir[15]=1 AND NOT {1000, 1001, 1010}".

"NOT {1000, 1001, 1010}" given ir[15]=1 is "NOT (ir[14:13]==00 AND ir[12:11]!=11... no, that's 5-bit.

Actually, {1000, 1001, 1010} have ir[14]=0 and ir[13:12] != 11. Wait:
- 1000: ir[14]=0, ir[13:12]=00
- 1001: ir[14]=0, ir[13:12]=01
- 1010: ir[14]=0, ir[13:12]=10

They all have ir[14]=0. And 1011 also has ir[14]=0 but ir[13:12]=11. So "ir[15]=1 AND NOT S-format" = "ir[15]=1 AND (ir[14]=1 OR (ir[13]=1 AND ir[12]=1))". This is exactly the current expression.

Alternative: `ir[15] && (ir[14] || ir[13])` would include 1010 (SW, ir[14]=0, ir[13]=1), so that doesn't work. The current expression is optimal.

## 6. Inline ir Bit Test Analysis

### 6.1 ALU Op Select

```verilog
if (is_alu_rr || is_alu_imm_grp) begin
    if (is_slt || is_slt_imm)
        alu_op = 3'd1;                // SUB for comparison
    else if (!ir[11])
        alu_op = ir[11:9];            // ADD=0, SUB=1, AND=2, OR=3
    else
        alu_op = 3'd4;                // XOR
end
```

This is clean: when ir[11]=0, the sub-opcode directly maps to the ALU operation code (ADD=000, SUB=001, AND=010, OR=011). When ir[11]=1, the SLT variants override to SUB, and XOR gets its own code. This direct mapping of ir[11:9] to alu_op avoids decode logic -- very efficient.

### 6.2 AUIPC / LUI Immediate Extraction

```verilog
// AUIPC/LUI lo: {off6[1:0], 6'b0} = {ir[4:3], 6'b0}
// AUIPC/LUI hi: ir[12:5]
```

The 10-bit immediate in U-format is ir[12:3], and it's shifted left by 6 to form a 16-bit value. The low byte is `{ir[4:3], 6'b0}` (only 2 bits contribute) and the high byte is `ir[12:5]`. These are contiguous bit extractions -- very clean.

### 6.3 J-Format Offset

```verilog
// J/JAL lo: {ir[6:0], 1'b0}         // off12[6:0] << 1
// J/JAL hi: {{3{ir[11]}}, ir[11:7]}  // sext(off12[11:7])
```

The 12-bit offset is ir[11:0], split as ir[11:7] (high) and ir[6:0] (low), both contiguous. The split at bit 7 aligns with the byte boundary after the <<1 shift. This is clean and efficient.

### 6.4 BRK/INT Detection at Dispatch

```verilog
if (fetch_ir[15:9] == 7'b1111100) begin  // INT opcode
    pc[0] <= i_bit;
    i_bit <= 1'b1;
end
```

This 7-bit compare is expensive (~3-4 gates). It detects the INT instruction at dispatch time to stash the I bit before it's overwritten. This is on the dispatch path (ir_accept), not the execute path, so it's less timing-critical.

The expression tests: ir[15:12]=1111 (system group) AND ir[11:9]=100 (INT sub-opcode). This is equivalent to `is_system && is_int`, but computed from `fetch_ir` rather than `ir` (since ir hasn't been loaded yet at dispatch). If the INT sub-opcode were 000 instead of 100, this would test `fetch_ir[15:9] == 7'b1111000`, which is `fetch_ir[15:12]==1111 && fetch_ir[11:9]==000`, i.e., all seven bits low except ir[15:12]. This would be: `&fetch_ir[15:12] && ~|fetch_ir[11:9]` (AND-reduce + NOR), which is simpler.

### 6.5 INT Vector Address Computation

```verilog
next_pc = {12'b0, off6[1:0] + 2'd1, 2'b00};  // Vector = (id+1)*4
```

This uses `off6[1:0] = ir[4:3]` -- clean contiguous extraction plus a 2-bit increment.

## 7. r_sel Mux Simplification

As analyzed in Proposal F, the r_sel mux's complexity comes from distinguishing R-type vs I-type within C-format. The current condition:

```verilog
fmt_c && ir[14:12] != 3'b011 && !(ir[14:12] == 3'b100 && !ir[11])
```

can be read as "C-format, not ALU-RR, not shift-register." It identifies the case where [2:0] is the source/dest register for r_sel.

**Without encoding changes,** this could potentially be reformulated. The R-type C-format instructions are exactly those with `ir[14:12] == 3'b011 || (ir[14:12] == 3'b100 && !ir[11])`. This is the same as `ir[14:13] == 2'b01 && ir[12:11] != 2'b01`. Let's check:

- ALU-RR (011): ir[14:13]=01, ir[12:11]=1x -> 10,11. ir[12:11] != 01 -> true for 10,11. Hmm, this would match all sub-opcodes of ALU-RR, which is correct.
- Shift-register (100, sub[2]=0): ir[14:13]=10. Doesn't match ir[14:13]==01. Fail.

That reformulation doesn't work. The R-type instructions span grp=011 and grp=100, which differ in ir[14].

**Another approach:** Instead of testing "not R-type", test the positive condition for I-type. C-format I-type groups are: 101 (control), 110 (ALU-imm), 111 (system), 100 with sub[2]=1 (shift-imm). These share ir[14:12] >= 101 OR (ir[14:12]==100 AND ir[11]). That's: `ir[14] && (ir[13] || ir[12]) || (ir[14:12]==100 && ir[11])`.

Actually: `ir[14] && (ir[13] || ir[12])` catches 101, 110, 111 (control, ALU-imm, system). Then `ir[14:12]==100 && ir[11]` catches shift-imm. Total: OR of two expressions.

But `ir[14] && (ir[13] || ir[12])` = `ir[14] && (ir[13] || ir[12])`. And the full C I-type test is `fmt_c && (ir[14] && (ir[13] || ir[12]) || (!ir[14] && ir[13] && ir[12] ... no, getting complicated.

**Conclusion:** The r_sel mux condition is essentially irreducible given the current group assignments. Any simplification would require moving ALU-RR or shift-register to a different group, which ripples through the entire FSM. **Not recommended for the expected savings.**

## 8. w_sel Mux Simplification

```verilog
wire [2:0] w_sel_mux = is_linking ? LINK_REG :
                       is_fixed_dest ? T0_REG : ir[2:0];
```

This 3-way priority mux overrides the destination register for:
- Linking instructions (JAL, JALR): write to R6 (link register)
- Fixed-dest ALU-imm instructions (SLTIF, SLTIUF, XORIF): write to R2 (t0)

**Encoding-based simplification:** If linking instructions had ir[2:0]=110 (R6) encoded directly, the is_linking override would be unnecessary. Let's check:
- JAL: J-format, `[0101][off12:12]` -- ir[2:0] is part of the 12-bit offset, not a register field. Fixing ir[2:0]=110 would constrain the offset.
- JALR: C-format, `[1101111][off6:6][rs:3]` -- ir[2:0] is rs (the source register). JALR writes to R6 but reads from rs at [2:0]. The write destination is overridden by is_linking.

For JALR, if we made the write destination explicit (separate from rs), we'd need more bits. The current encoding overloads [2:0] as both rs (read) and rd (via override). This is fundamentally a 2-operand encoding limitation -- can't encode both rs and rd in a 6-bit payload.

For JAL, [2:0] is part of off12 and there's no register field at all. The override is necessary.

**The w_sel_mux overrides are inherent in the instruction format constraints.** No practical encoding change would eliminate them.

For `is_fixed_dest`, the override writes to T0 (R2). If the SLTIF/SLTIUF/XORIF instructions encoded T0 directly in ir[2:0] (always 010), we could eliminate the override. But these instructions use [2:0] as the source register -- the whole point of "fixed-dest" is to write to a different register than the source, enabling `SLTIF a0, 10; BNZ t0, target` without destroying a0. The override is fundamental to the instruction semantics.

**No recommended change.**

## 9. Summary of Proposals Ranked by Expected Gate Savings

| Rank | Proposal | Type | Est. Savings | Risk | Notes |
|---|---|---|---|---|---|
| 1 | H1: is_byte_load bit test | Decode only | ~3.5 gates | None | `!ir[15] && ir[14] && ir[13]` |
| 2 | H3: is_load simplification | Decode only | ~3 gates | None | Combine H1 with is_lw |
| 3 | H2: is_store XOR pattern | Decode only | ~2 gates | None | `ir[15] && !ir[14] && (ir[13] ^ ir[12])` |
| 4 | E: SLT/SLTU to sub=100/101 | Encoding change | ~2 gates | Low | Sub-opcode reorder in ALU groups |
| 5 | B: Explicit is_store XOR | Decode only | ~2 gates | None | Same as H2 (merged) |

**Total estimated savings from decode-only optimizations (H1+H3+H2): ~8.5 gates (~17 transistors).**

**Total with encoding change (add E): ~10.5 gates (~21 transistors).**

### Proposals Not Recommended

| Proposal | Reason |
|---|---|
| A (S-format consolidation) | All variants either lose U-format immediate bits or require moving J-format, with cascading disruptions exceeding the decode savings |
| D (S-format reorder) | 3+2 load/store split prevents clean binary separation regardless of arrangement |
| F (r_sel simplification) | R-type groups span ir[14], making bit-pattern separation impossible without major group reassignment |
| G (AUIPC in S-format) | Would duplicate address computation logic, costing more than decode savings |
| w_sel simplification | Overrides are inherent in 2-operand format constraints |

## 10. Caveats

1. **Synthesis may already optimize these.** The Yosys/OpenLane synthesis flow performs Boolean optimization that may already find the bit-pattern shortcuts identified in H1-H3. Explicitly writing simpler expressions in RTL guides synthesis but may produce identical gates.

2. **Gate count estimates are approximate.** Actual gate savings depend on the synthesis tool's ability to share subexpressions, technology mapping (IHP sg13g2 cell library specific gate sizes), and routing effects. The estimates assume generic 2-input gates.

3. **Timing impact may differ from area impact.** A change that saves 2 gates of area may save 0 gates on the critical path (if those gates aren't on it) or may save significant timing (if they are). The is_store and is_byte_load properties are used in E_MEM states, which are on the critical dout timing path -- optimizing them has timing as well as area benefit.

4. **Encoding changes (Proposal E) require updating tests, assembler, and documentation.** The decode-only optimizations (H1-H3) require only RTL changes with no ISA-visible effects.

5. **20 transistors is ~0.13% of the total transistor count** (~16,000). These are marginal improvements. The analysis confirms that the current ISA encoding is reasonably well-optimized: the major inefficiency (S-format opcode scatter) is structural and cannot be fixed without unacceptable tradeoffs.
