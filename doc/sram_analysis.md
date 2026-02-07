# Register File SRAM Analysis

RISCY-V02's register file is an 8-word x 16-bit regular array with 2 read ports and 1 write port (2R1W). Standard cell synthesis implements it using latches and mux trees, but a real chip would use SRAM — the array is perfectly regular and far too large for individual register cells.

This document designs an equivalent 8T SRAM register file from first principles, counts every transistor, and computes the SRAM-adjusted transistor count for fair comparison with the Arlet 6502.

## Why This Discount Is Fair

The discount applies only to **regular storage arrays** — structures where identical bit cells are arranged in a grid with shared decode/sense logic. The methodology is:

1. Identify separately-synthesizable modules that are pure regular arrays
2. Count their standard cell transistors exactly (from standalone synthesis)
3. Design an equivalent SRAM from first principles, counting every transistor
4. Apply the same methodology to the comparison target (Arlet 6502)

The Arlet 6502 has no regular arrays — its registers (A, X, Y, SP) are asymmetric, each wired to different parts of the datapath. The same methodology applied to the 6502 yields zero discount.

## Standard Cell Register File (Synthesized)

The register file is a single Verilog module (`riscyv02_regfile`) that synthesizes standalone. All 141 latches in the full design belong to this module. Standalone synthesis (Yosys, IHP sg13g2 typ 1.20V 25C) gives:

| Cell Type | Description | Count | Tx/Cell | Transistors |
|---|---|---|---|---|
| sg13g2_dlhrq_1 | D-latch (high, w/ reset) | 141 | 20 | 2,820 |
| sg13g2_a22oi_1 | AOI22 | 98 | 8 | 784 |
| sg13g2_and4_1 | AND4 | 27 | 10 | 270 |
| sg13g2_nand2_1 | NAND2 | 41 | 4 | 164 |
| sg13g2_a221oi_1 | AOI221 | 14 | 10 | 140 |
| sg13g2_and2_1 | AND2 | 17 | 6 | 102 |
| sg13g2_a21oi_1 | AOI21 | 16 | 6 | 96 |
| sg13g2_and3_1 | AND3 | 11 | 8 | 88 |
| sg13g2_nor2_1 | NOR2 | 12 | 4 | 48 |
| sg13g2_nor3_1 | NOR3 | 8 | 6 | 48 |
| sg13g2_nor4_1 | NOR4 | 4 | 8 | 32 |
| sg13g2_nor2b_1 | NOR2 (1 inv) | 5 | 6 | 30 |
| sg13g2_nand3_1 | NAND3 | 3 | 6 | 18 |
| sg13g2_nand2b_1 | NAND2 (1 inv) | 2 | 6 | 12 |
| sg13g2_inv_1 | Inverter | 4 | 2 | 8 |
| sg13g2_nand4_1 | NAND4 | 1 | 8 | 8 |
| sg13g2_or2_1 | OR2 | 1 | 6 | 6 |
| **Total** | | **405** | | **4,674** |

Synthesis area: 6,568 um² (26.3% of the 25,023 um² full design).

Tx/cell counts are from the PDK's CDL SPICE netlist (one M-line = one MOSFET), the same source used for all transistor count estimates in this project.

### Functional Breakdown

- **128 follower latches** (8 regs x 16 bits): pure storage array, perfectly regular
- **13 leader latches**: write pipeline (captures w_data, w_sel, w_hi, w_we at negedge)
- **264 combinational cells**: write decode (3-to-8 + enable + byte select) and 2 read mux trees (8:1 x 16 bits x 2 ports, then 2:1 byte select)

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

8 rows x 16 columns = 128 cells x 8T = **1,024T**

### Write Path

Writes occur during clk=0 through the RW port. We write 8 bits at a time (one byte of one register), requiring row decode, write enable, byte select, and data drivers.

#### Row Decoder (w_sel -> 8 one-hot lines)

A 3-to-8 decoder using the 3 address bits and their complements:

| Component | Count | Tx/each | Transistors |
|---|---|---|---|
| INV (complement inputs) | 3 | 2 | 6 |
| AND3 (NAND3 + INV, one per row) | 8 | 8 | 64 |
| **Subtotal** | | | **70** |

#### Write Enable + Byte Select

Precompute two control signals that gate the write word lines:

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| INV | ~w_hi | 1 | 2 | 2 |
| AND2 | write_lo = w_we AND ~w_hi | 1 | 6 | 6 |
| AND2 | write_hi = w_we AND w_hi | 1 | 6 | 6 |
| **Subtotal** | | | | **14** |

#### Word Line Gating

Each decoded row line is ANDed with the byte-select control:

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| AND2 | WL_lo[i] = row[i] AND write_lo | 8 | 6 | 48 |
| AND2 | WL_hi[i] = row[i] AND write_hi | 8 | 6 | 48 |
| **Subtotal** | | | | **96** |

#### Write Drivers

Generate complementary data for the bit lines. The same 8 data/complement pairs drive both byte halves; the word line byte select controls which cells latch:

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| INV | ~w_data[i] (complement) | 8 | 2 | 16 |
| **Subtotal** | | | | **16** |

**Write path total: 196T**

### Read Path 1 (RW Port, Differential)

During clk=1, the RW port reads r_sel. Differential bit lines (BL/BLB) give correct polarity directly.

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| INV | complement r_sel inputs | 3 | 2 | 6 |
| AND3 | row decode (one per row) | 8 | 8 | 64 |
| PMOS | precharge BL[0..15] | 16 | 1 | 16 |
| PMOS | precharge BLB[0..15] | 16 | 1 | 16 |
| PMOS | equalize BL=BLB | 16 | 1 | 16 |
| MUX2 | byte select (r_hi) | 8 | 6 | 48 |
| INV | ~r_hi | 1 | 2 | 2 |
| **Subtotal** | | | | **168** |

For an 8-deep array the bit-line swing is large and fast — no sense amplifiers are needed. Direct bit-line sensing through the byte mux is sufficient.

### Read Path 2 (R-Only Port, Single-Ended)

The 8T cell's dedicated read port: N5 (access, gated by read word line) in series with N6 (driver, gated by QB). Read bit line (RBL) is pulled high by a keeper; selected cell conditionally discharges it.

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| INV | complement r2_sel inputs | 3 | 2 | 6 |
| AND3 | row decode (one per row) | 8 | 8 | 64 |
| PMOS | pull-up keeper RBL[0..15] | 16 | 1 | 16 |
| MUX2 | byte select (r2_hi) | 8 | 6 | 48 |
| INV | ~r2_hi | 1 | 2 | 2 |
| **Subtotal** | | | | **136** |

### Grand Total

| Component | Transistors | % |
|---|---|---|
| Storage array (128 x 8T) | 1,024 | 67.2% |
| Write path (decode + enable + byte + drivers) | 196 | 12.9% |
| Read path 1 (RW, differential) | 168 | 11.0% |
| Read path 2 (R, single-ended) | 136 | 8.9% |
| **Total** | **1,524** | **100%** |

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
| Storage | 141 latches x 20T = 2,820 | 128 cells x 8T = 1,024 |
| Peripherals | 264 combo cells = 1,854 | Decode + drivers + mux = 500 |
| **Total** | **4,674** | **1,524** |
| **Discount** | | **-3,150** |

The SRAM saves on both storage (8T vs 20T per bit) and peripherals (word-line decode replaces explicit mux trees — asserting one word line selects all 16 bits of one register, eliminating the 8:1 mux per bit that standard cells require).

## SRAM-Adjusted Figures

| Metric | Standard Cell | SRAM-Adjusted | Arlet 6502 |
|---|---|---|---|
| Transistors | 16,330 | **13,180** | 13,082 |
| vs 6502 | +24.8% | **+0.7%** | baseline |
| Synthesis area (est.) | 25,023 um² | ~20,601 um² | — |

Area estimate uses average transistor density from the full design (0.653 tx/um²) to convert the 3,150 transistor discount to ~4,822 um² of area savings. This is conservative — SRAM cells are denser than standard cells, so actual area savings would be larger.

## Methodology Notes

1. **Transistor counts are exact**, not estimates. Standard cell counts come from the PDK's CDL SPICE netlist (one M-line = one MOSFET). SRAM counts come from the circuit design above, using textbook CMOS gate structures.

2. **The 8T cell transistor count is definitional.** An 8T SRAM cell has 8 transistors by definition — that's what "8T" means. This is not a process-specific or PDK-specific number.

3. **The same methodology applies to both designs.** The Arlet 6502's registers (A, X, Y, SP) are asymmetric special-purpose registers wired to different datapath elements. They are not a regular array and would not use SRAM in any implementation. Applying this methodology to the 6502 yields zero discount.

4. **No SRAM macro exists** at this size for IHP sg13g2. The smallest available macro (64x32, 2048 bits) stores 16x more than needed and is physically larger than the entire RISCY-V02 design. The SRAM analysis here is a paper design representing what a custom chip would use.
