# Dispatch Mux Analysis: fetch_ir → Destination Registers

## Overview

At dispatch (ir_accept), bits from fetch_ir are routed to several destination
registers. The routing is format-dependent: different instruction formats place
different fields at different bit positions, requiring muxes to steer the right
bits to the right registers.

This analysis maps every dispatch mux, estimates its cost, and identifies ISA
encoding rearrangements that could reduce muxing.

## fetch_ir Field Positions by Format

```
Bit position:  [15:12]    [11:9]      [8:6]       [5:3]      [2:0]

U-format:      prefix:3+  imm10[8:6]  imm10[5:3]  imm10[2:0]  rd
               bit[12]=imm10[9]

J-format:      prefix:4   off12[11:9] off12[8:6]  off12[5:3]  off12[2:0]

S-format:      prefix:4   off6[2:0]   off6[5:3]   rs1         rd/rs2

C R-type:      1+grp:3    sub:3       rs2         rs1         rd
C I-type:      1+grp:3    sub:3       imm6[5:3]   imm6[2:0]  rd/rs
```

## Destination Registers and Their Sources

### 1. op_r[5:0] — Opcode identity register (lines 714-755)

A priority encoder driven by opcode bits. For C-format, it's mostly a direct
read of fetch_ir[14:9]. For U/J/S formats, it's a constant determined by
the 3-4 bit opcode prefix.

| Format | Source | Note |
|---|---|---|
| U (000x) | OP_LUI constant | From 3-bit prefix |
| U (001x) | OP_AUIPC constant | From 3-bit prefix |
| J (0100) | OP_J constant | From 4-bit opcode |
| J (0101) | OP_JAL constant | From 4-bit opcode |
| S (0110-1010) | OP_LB..OP_SW constants | One per opcode |
| C system | {3'b000, fetch_ir[11:9]} | Remapped group |
| C normal | fetch_ir[14:9] | Direct pass-through |

**Cost**: ~7→6 bit combinational function. Synthesis implements efficiently as
flattened logic or ROM — the priority chain in RTL does NOT synthesize to
cascaded muxes.

**ISA leverage**: Very low. Making opcode→op_r mapping simpler won't save
meaningful area; synthesis already optimizes this well.

### 2. off6_r[5:0] — 6-bit offset/immediate (lines 707-709, 722-742, 788)

| Bits | Default | Override | Condition |
|---|---|---|---|
| [5:3] | fetch_ir[8:6] | — | (always) |
| [2:0] | fetch_ir[5:3] | fetch_ir[11:9] | S-format (loads/stores) |
| [5:0] | — | 6'd3 | BRK |
| [5:0] | — | 6'd1 or 6'd2 | IRQ/NMI entry |

**Mux cost**: 3-bit 2:1 mux for off6_r[2:0] S-format override + 6-bit constant
override for BRK/IRQ/NMI (rare, likely optimized by synthesis).

The S-format override is the only significant cost. It exists because S-format
scrambles the immediate to keep rs1 at [5:3]: the low 3 offset bits land at
[11:9] instead of the default [5:3].

### 3. r2_sel_r[2:0] — Read port 2 select (lines 712, 737, 742)

| Default | Override | Condition |
|---|---|---|
| fetch_ir[8:6] | fetch_ir[2:0] | Stores (SB, SW) |

**Mux cost**: 3-bit 2:1 mux. Stores need rs2, which sits at [2:0] in S-format.
The default [8:6] position holds off6[5:3] for S-format, not a register select.

### 4. r_sel_r[2:0] — Read port 1 select (lines 771-776, 784-785)

| Source | Condition |
|---|---|
| fetch_ir[2:0] | C I-type (imm group, not ALU-RR, not shift-RR) |
| fetch_ir[5:3] | C R-type, S, U, J formats |
| 3'd6 | RETI |

**Mux cost**: 3-bit 3:1 mux with complex select logic.

The C I-type condition is:
```
is_fmt_c && grp != 011 && !(grp == 100 && !sub[2])
```
This distinguishes R-type (ALU-RR group + shift-register subgroup) from I-type
(everything else in C-format).

### 5. tmp[11:8] — Upper temporary bits (lines 758-764)

| Source | Condition | Consumed by |
|---|---|---|
| fetch_ir[12:9] | U-format | LUI, AUIPC (lines 443, 537) |
| {1'b0, fetch_ir[11:9]} | J-format | J/JAL (line 550) |
| {1'b0, fetch_ir[5:3]} | S/C formats | **Don't-care** (never consumed) |

**Mux cost**: Effectively a 2:1 mux (U vs J). The S/C assignment is don't-care —
non-AUIPC memory ops overwrite tmp[15:8] at E_EXEC_HI before consuming it,
and C-format execute paths never read tmp[11:8]. Synthesis can exploit this.

For U-format, tmp[11] gets fetch_ir[12] (the 10th immediate bit). For J and
S/C, tmp[11] is 0. This is a 1-bit 2:1 mux (or AND gate).

### 6. tmp[2:0] — J-format lower offset (line 762)

| Source | Condition |
|---|---|
| fetch_ir[2:0] | J-format |
| (not written) | All other formats |

**Cost**: Write-enable gate (conditional write), not a mux. Very cheap.

### 7. rd_rs2_sel_r[2:0] — Destination/rs2 select (line 766, 787)

| Source | Condition |
|---|---|
| fetch_ir[2:0] | Always |
| 3'd6 | BRK (override) |

**Cost**: Constant override for one rare instruction. Negligible.

### 8. r_hi_r — Read high byte first (lines 779-780)

| Source | Condition |
|---|---|
| 1 | C-format shift group, right shift (grp==100 && fetch_ir[10]) |
| 0 | All others |

**Cost**: AND gate. Negligible.

## Total Dispatch Mux Overhead Estimate

| Mux | Width | Depth | Est. TX |
|---|---|---|---|
| off6_r[2:0] S-format | 3-bit 2:1 | 1 level | ~6-12 |
| r2_sel_r stores | 3-bit 2:1 | 1 level | ~6-12 |
| r_sel_r format | 3-bit 3:1 | 2 levels | ~12-18 |
| tmp[11:8] U/J | 4-bit 2:1 (1 real + 3 don't-care) | 1 level | ~2-4 |
| op_r encoder | 7→6 function | flattened | ~20-40 |
| Others (tmp[2:0] WE, rd_rs2_sel_r BRK, r_hi_r) | trivial | — | ~4-6 |
| **Total** | | | **~50-90** |

This is 0.3–0.6% of the ~16,000 tx design. Within synthesis non-determinism
(~400 tx between runs).

## ISA Rearrangements: Ranked by Expected Impact

### Tier 1: Most Promising

#### A. S-format unscramble: rs1 → [11:9], off6 contiguous at [8:3]

**Current S-format**: `[prefix:4][off6[2:0]:3][off6[5:3]:3][rs1:3][rd/rs2:3]`
**Proposed**:         `[prefix:4][rs1:3][off6[5:3]:3][off6[2:0]:3][rd/rs2:3]`

**Saves**: off6_r[2:0] 3-bit 2:1 mux — S-format no longer scrambles the
immediate, so off6_r = fetch_ir[8:3] works for ALL formats.

**Costs**: r_sel_r expands from 3 to 4 sources. S-format rs1 is now at [11:9]
instead of [5:3], requiring a new r_sel_r override. But r_sel_r already has a
3:1 mux; going to 4:1 stays at 2 mux levels.

**Net**: Eliminate one 3-bit 2:1 mux, expand another by 1 input. ~4-10 tx savings.

**Side effects**: Store r2_sel_r override is unchanged (rs2 still at [2:0]).
rd_rs2_sel_r unchanged (rd/rs2 still at [2:0]). The assembler/linker must
emit the new encoding.

**Risk**: Low. r_sel_r timing is not on the critical path (regfile read happens
well before data is needed).

#### B. Combine A with store rs2 → [8:6] (separate store layout)

If stores additionally moved rs2 from [2:0] to [8:6], the r2_sel_r override
would also be eliminated:

**Store layout**: `[prefix:4][rs1:3][rs2:3][off6[2:0]:3][off6[5:3]:3]`

But this scrambles off6 for stores only — off6_r[5:3] = fetch_ir[5:3] = off6[5:3]
is OK, but off6_r[2:0] = fetch_ir[5:3] = off6[5:3] for default, and the actual
off6[2:0] is at fetch_ir[2:0]. Now off6_r needs override for stores: both halves.

**Verdict**: WORSE. Trading one 3-bit 2:1 mux for two 3-bit 2:1 muxes. The S-format
constraint (4 prefix + 3 rs1 + 3 rs2 + 6 off6 = 16 bits) means any layout that
fixes one override creates another. The current layout already minimizes total
overrides within S-format.

### Tier 2: Minor / Uncertain

#### C. Simplify r_sel_r C I-type detection

The r_sel_r I-type/R-type condition is complex:
```
is_fmt_c && grp != 011 && !(grp == 100 && !sub[2])
```

This could be simplified if shift-register instructions (SLL/SRL/SRA) used a
different group encoding so that "R-type" = "group 011" (simple 3-bit compare).

**Problem**: Shift-register instructions need a group with shift-specific
properties (is_right_shift, is_arith_shift from sub bits). Moving them to
group 011 would conflict with ALU-RR instructions (7 + 3 = 10 > 8 sub-slots).

**Partial fix**: If R-type detection could be expressed as a single bit test
(e.g., `grp[2:1] == 2'b01`), the mux select logic would be simpler. But
groups 010 (jump_imm) and 011 (ALU-RR) both have `grp[2:1]==01`, and only
011 is R-type. So this doesn't work.

**Net**: No feasible rearrangement found. ~2-4 tx possible if a clean encoding
exists, but none does within current group constraints.

#### D. Eliminate system group remapping (grp 111 → internal group 000)

System instructions (SEI, CLI, RETI, BRK, WAI, STP) have ISA group 111 but
internal op_r group 000. The remapping `op_r <= {3'b000, fetch_ir[11:9]}`
costs a few gates in the op_r priority chain.

If system instructions could have ISA group 000, no remapping needed. But
ISA group 000 corresponds to U-format (opcode 0000-0011), not C-format.
System instructions must be in C-format (opcode 1011-1111) to use the
grp+sub encoding. ISA group 111 is forced by the format structure.

**Net**: Not feasible without restructuring format boundaries. The remapping
cost (~4-8 tx) is inherent to the format system.

### Tier 3: Break-Even or Negative

#### E. Move C I-type rd to [5:3] (unify r_sel_r to always [5:3])

Swap rd and imm6[2:0] positions in C I-type: `[1][grp:3][sub:3][imm6[5:3]:3][rd:3][imm6[2:0]:3]`

**Saves**: r_sel_r always reads [5:3] (no I-type override).

**Costs**:
- off6_r[2:0] needs override for I-type (fetch_ir[2:0] instead of [5:3])
- rd_rs2_sel_r needs override for I-type (fetch_ir[5:3] instead of [2:0])

**Net**: Replace 1 mux with 2 muxes. NEGATIVE.

#### F. Move C R-type rs1 to [2:0] (unify r_sel_r to always [2:0])

Swap rs1 and rd in R-type: `[1][grp:3][sub:3][rs2:3][rd:3][rs1:3]`

**Saves**: r_sel_r always reads [2:0] for C-format.

**Costs**: rd_rs2_sel_r needs R-type override (fetch_ir[5:3] instead of [2:0]).
This feeds w_sel_mux, which is on the write path — adding a serial mux
before the existing 3:1 w_sel_mux.

**Net**: Same mux count, possibly worse timing. BREAK-EVEN to NEGATIVE.

#### G. Change off6_r default to better match more formats

off6_r = fetch_ir[8:3] is already optimal: matches C I-type (imm6 at [8:3]),
branches, JR/JALR, and J-format partial. Only S-format needs override.
No better default exists.

## Assessment

**The dispatch muxing is already well-optimized** for the current ISA structure.
The total overhead is ~50-90 tx (0.3-0.6% of the design). The only ISA
rearrangement with clear positive expected value is:

**A. S-format unscramble** (~4-10 tx savings, low risk)

All other rearrangements are either infeasible, break-even, or negative.

The biggest gains in transistor reduction are likely NOT in ISA encoding but in:
- Removing instructions or features
- Simplifying execute-side logic (e.g., the alu_b mux tree has ~8 distinct
  expressions — this costs far more than dispatch)
- Reducing FSM states or merging instruction execution paths
- Architectural changes (e.g., simpler shift implementation)

ISA encoding changes are essentially free to implement (assembler/linker
changes only) and do compound, so trying the S-format unscramble is
worthwhile even if the expected savings are small.
