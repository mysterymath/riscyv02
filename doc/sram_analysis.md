# Register File SRAM Analysis

RISCY-V02's register file is a 9-word x 16-bit regular array with 2 read ports and 1 write port (2R1W). Eight words are general-purpose registers (R0–R7), and the 9th word is the Exception PC (EPC) register. EPC is accessible via 4-bit select lines on port 1 and the write port; port 2 remains 3-bit (GP registers only). Standard cell synthesis implements it using latches and mux trees, but a real chip would use SRAM — the array is perfectly regular and far too large for individual register cells.

This document designs an equivalent 8T SRAM register file from first principles, counts every transistor, and computes the SRAM-adjusted transistor count for fair comparison with the Arlet 6502.

## Why This Discount Is Fair

The discount applies only to **regular storage arrays** — structures where identical bit cells are arranged in a grid with shared decode/sense logic. The methodology is:

1. Identify separately-synthesizable modules that are pure regular arrays
2. Count their standard cell transistors exactly (from standalone synthesis)
3. Design an equivalent SRAM from first principles, counting every transistor
4. Apply the same methodology to the comparison target (Arlet 6502)

The Arlet 6502 has no regular arrays — its registers (A, X, Y, SP) are asymmetric, each wired to different parts of the datapath. The same methodology applied to the 6502 yields zero discount.

## Standard Cell Register File (Synthesized)

The register file is a single Verilog module (`riscyv02_regfile`) that synthesizes standalone. All 158 latches in the full design belong to this module. Standalone synthesis (Yosys 0.55, IHP sg13g2 typ 1.20V 25C) gives:

| Cell Type | Description | Count | Tx/Cell | Transistors |
|---|---|---|---|---|
| sg13g2_dlhrq_1 | D-latch (high, w/ reset) | 158 | 20 | 3,160 |
| sg13g2_a22oi_1 | AOI22 | 102 | 8 | 816 |
| sg13g2_and3_1 | AND3 | 39 | 8 | 312 |
| sg13g2_and2_1 | AND2 | 36 | 6 | 216 |
| sg13g2_nand2_1 | NAND2 | 52 | 4 | 208 |
| sg13g2_and4_1 | AND4 | 17 | 10 | 170 |
| sg13g2_a21oi_1 | AOI21 | 24 | 6 | 144 |
| sg13g2_a221oi_1 | AOI221 | 11 | 10 | 110 |
| sg13g2_o21ai_1 | OAI21 | 18 | 6 | 108 |
| sg13g2_nor2b_1 | NOR2 (1 inv) | 7 | 6 | 42 |
| sg13g2_nor3_1 | NOR3 | 6 | 6 | 36 |
| sg13g2_inv_1 | Inverter | 6 | 2 | 12 |
| sg13g2_nand2b_1 | NAND2 (1 inv) | 2 | 6 | 12 |
| sg13g2_nand3_1 | NAND3 | 2 | 6 | 12 |
| sg13g2_nor2_1 | NOR2 | 3 | 4 | 12 |
| sg13g2_or3_1 | OR3 | 1 | 8 | 8 |
| sg13g2_or2_1 | OR2 | 1 | 6 | 6 |
| **Total** | | **485** | | **5,384** |

Synthesis area: 3,324 um² (combinational only; latch area unknown to this liberty).

Tx/cell counts are from the PDK's CDL SPICE netlist (one M-line = one MOSFET), the same source used for all transistor count estimates in this project.

### Functional Breakdown

- **144 follower latches** (8 regs x 16 bits + 1 EPC x 16 bits): pure storage array, perfectly regular
- **14 leader latches**: write pipeline (captures w_data[7:0], w_sel[3:0], w_hi, w_we at negedge)
- **327 combinational cells**: write decode (3-to-8 + sel[3] gating + enable + byte select) and 2 read mux trees (9:1 x 16 bits on port 1, 8:1 x 16 bits on port 2, then 2:1 byte select)

## 8T SRAM Register File Design

### Why 8T

6T SRAM provides 1 port. Our register file requires 2 simultaneous reads (the ALU needs both operands in the same cycle). The minimum cell for 2 ports is 8T:

- **6T** = 4T storage + 2T access = 1 port
- **8T** = 4T storage + 2T RW access + 2T read-only = 2 ports (1RW + 1R)

We time-share the RW port: reads during clk=1, writes at negedge. The R-only port provides the second simultaneous read. This matches our pipeline exactly.

### 8T Bit Cell

```
Storage:   P1 P2 N1 N2  (cross-coupled inverters)     = 4T
RW port:   N3 N4        (access NMOS, gated by WL_rw)  = 2T
R port:    N5 N6        (N5=access gated by WL_r,      = 2T
                         N6=driver gated by QB)
                                                       ────
                                                         8T
```

The read-only port connects N6's gate to QB (complement of stored value), so the read bit line gives the non-inverted value Q — no output inversion needed.

### Storage Array

9 rows x 16 columns = 144 cells x 8T = **1,152T**

### Write Path

Writes occur during clk=0 through the RW port. We write 8 bits at a time (one byte of one register), requiring row decode, write enable, byte select, and data drivers.

#### Row Decoder (w_sel -> 9 one-hot lines)

A 3-to-8 decoder for GP rows using `w_sel[2:0]`, with `w_sel[3]` gating all 8 GP rows (only GP when sel[3]=0) and directly enabling the EPC row (when sel[3]=1):

| Component | Count | Tx/each | Transistors |
|---|---|---|---|
| INV (complement w_sel[2:0]) | 3 | 2 | 6 |
| AND3 (NAND3 + INV, one per GP row) | 8 | 8 | 64 |
| INV (~w_sel[3]) | 1 | 2 | 2 |
| AND2 (gp_row[i] = row[i] AND ~sel[3]) | 8 | 6 | 48 |
| **Subtotal** | | | **120** |

EPC row enable comes directly from `w_sel[3]` — no additional gate needed.

#### Write Enable + Byte Select

Precompute two control signals that gate the write word lines:

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| INV | ~w_hi | 1 | 2 | 2 |
| AND2 | write_lo = w_we AND ~w_hi | 1 | 6 | 6 |
| AND2 | write_hi = w_we AND w_hi | 1 | 6 | 6 |
| **Subtotal** | | | | **14** |

#### Word Line Gating

Each decoded row line (8 GP + 1 EPC) is ANDed with the byte-select control:

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| AND2 | WL_lo[i] = row[i] AND write_lo | 9 | 6 | 54 |
| AND2 | WL_hi[i] = row[i] AND write_hi | 9 | 6 | 54 |
| **Subtotal** | | | | **108** |

#### Write Drivers

Generate complementary data for the bit lines. The same 8 data/complement pairs drive both byte halves; the word line byte select controls which cells latch:

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| INV | ~w_data[i] (complement) | 8 | 2 | 16 |
| **Subtotal** | | | | **16** |

**Write path total: 258T**

### Read Path 1 (RW Port, Differential)

During clk=1, the RW port reads r1_sel. This is a 4-bit address: r1_sel[2:0] decodes the GP row, r1_sel[3] selects EPC instead. Differential bit lines (BL/BLB) give correct polarity directly.

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| INV | complement r1_sel[2:0] | 3 | 2 | 6 |
| AND3 | GP row decode (one per row) | 8 | 8 | 64 |
| INV | ~r1_sel[3] | 1 | 2 | 2 |
| AND2 | gp_row[i] = row[i] AND ~sel[3] | 8 | 6 | 48 |
| PMOS | precharge BL[0..15] | 16 | 1 | 16 |
| PMOS | precharge BLB[0..15] | 16 | 1 | 16 |
| PMOS | equalize BL=BLB | 16 | 1 | 16 |
| MUX2 | byte select (r1_hi) | 8 | 6 | 48 |
| INV | ~r1_hi | 1 | 2 | 2 |
| **Subtotal** | | | | **218** |

EPC read word line comes directly from `r1_sel[3]` — no additional gate. For a 9-deep array the bit-line swing is large and fast — no sense amplifiers are needed.

### Read Path 2 (R-Only Port, Single-Ended)

The 8T cell's dedicated read port: N5 (access, gated by read word line) in series with N6 (driver, gated by QB). Read bit line (RBL) is pulled high by a keeper; selected cell conditionally discharges it. Port 2 uses a 3-bit address and accesses only GP registers (no EPC).

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| INV | complement r2_sel inputs | 3 | 2 | 6 |
| AND3 | row decode (one per row) | 8 | 8 | 64 |
| PMOS | pull-up keeper RBL[0..15] | 16 | 1 | 16 |
| MUX2 | byte select (r2_hi) | 8 | 6 | 48 |
| INV | ~r2_hi | 1 | 2 | 2 |
| **Subtotal** | | | | **136** |

Only 8 GP rows are accessed — the EPC row's read-only port word line is tied low. No banking or sel[3] gating needed.

### Grand Total

| Component | Transistors | % |
|---|---|---|
| Storage array (144 x 8T) | 1,152 | 65.3% |
| Write path (decode + enable + byte + drivers) | 258 | 14.6% |
| Read path 1 (RW, differential) | 218 | 12.4% |
| Read path 2 (R, single-ended) | 136 | 7.7% |
| **Total** | **1,764** | **100%** |

### Gate Transistor Counts Used

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

## Comparison

| | Standard Cell | 8T SRAM |
|---|---|---|
| Storage | 158 latches x 20T = 3,160 | 144 cells x 8T = 1,152 |
| Peripherals | 327 combo cells = 2,224 | Decode + drivers + mux = 612 |
| **Total** | **5,384** | **1,764** |
| **Discount** | | **3,620** |

The SRAM saves on both storage (8T vs 20T per bit) and peripherals (word-line decode replaces explicit mux trees — asserting one word line selects all 16 bits of one register, eliminating the 8:1 mux per bit that standard cells require).

## SRAM-Adjusted Figures

| Metric | Value |
|---|---|
| Standard cell (synthesis) | 17,104 |
| Register file (standard cell) | 5,384 |
| Register file (8T SRAM equivalent) | 1,764 |
| SRAM discount | -3,620 |
| **SRAM-adjusted total** | **13,484** |
| vs Arlet 6502 (13,176) | +2.3% |

## Methodology Notes

1. **Transistor counts are exact**, not estimates. Standard cell counts come from the PDK's CDL SPICE netlist (one M-line = one MOSFET). SRAM counts come from the circuit design above, using textbook CMOS gate structures.

2. **The 8T cell transistor count is definitional.** An 8T SRAM cell has 8 transistors by definition — that's what "8T" means. This is not a process-specific or PDK-specific number.

3. **The same methodology applies to both designs.** The Arlet 6502's registers (A, X, Y, SP) are asymmetric special-purpose registers wired to different datapath elements. They are not a regular array and would not use SRAM in any implementation. Applying this methodology to the 6502 yields zero discount.

4. **No SRAM macro exists** at this size for IHP sg13g2. The smallest available macro (64x32, 2048 bits) stores 16x more than needed and is physically larger than the entire RISCY-V02 design. The SRAM analysis here is a paper design representing what a custom chip would use.
