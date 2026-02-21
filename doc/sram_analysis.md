# Register File SRAM Analysis

RISCY-V02's register file is an 8-word x 16-bit regular array of general-purpose registers (R0–R7) with 2 read ports and 1 write port (2R1W). All ports are 16 bits wide and use 3-bit select lines. Both bytes of a register are read/written simultaneously. Standard cell synthesis implements it using latches and mux trees, but a real chip would use SRAM — the array is perfectly regular and far too large for individual register cells.

This document designs an equivalent 8T SRAM register file from first principles, counts every transistor, and computes the SRAM-adjusted transistor count for fair comparison with the Arlet 6502.

## Why This Discount Is Fair

The discount applies only to **regular storage arrays** — structures where identical bit cells are arranged in a grid with shared decode/sense logic. The methodology is:

1. Identify separately-synthesizable modules that are pure regular arrays
2. Count their standard cell transistors exactly (from standalone synthesis)
3. Design an equivalent SRAM from first principles, counting every transistor
4. Apply the same methodology to the comparison target (Arlet 6502)

The Arlet 6502 has no regular arrays — its registers (A, X, Y, SP) are asymmetric, each wired to different parts of the datapath. The same methodology applied to the 6502 yields zero discount.

## Standard Cell Register File (Synthesized)

The register file is a single Verilog module (`riscyv02_regfile`) marked `(* keep_hierarchy *)`. This prevents the synthesizer from flattening it into the parent module, so its cell counts appear as a sub-module in `stat.json` — extracted from the same synthesis run as the total, eliminating cross-run non-determinism.

It contains leader latches (20, transparent-high: 16 data + 3 sel + 1 we), follower latches (128, gated by ~clk & decoded wen), and read mux trees. Write inputs are combinational from execute; the leader-follower pair acts as a negedge-triggered write.

The exact cell counts vary slightly between synthesis runs (Yosys ABC optimization is non-deterministic), but the 148 latches are always present. The `transistor_count.py` script reads the actual count from each build's `stat.json`.

### Functional Breakdown

- **20 leader latches** (16 w_data + 3 w_sel + 1 w_we): write port staging, transparent during clk=1
- **128 follower latches** (8 regs × 16 bits): pure storage array, perfectly regular
- **Combinational cells**: write decode (3-to-8) and 2 read mux trees (8:1 × 16 bits each)

Tx/cell counts are from the PDK's CDL SPICE netlist (one M-line = one MOSFET), the same source used for all transistor count estimates in this project.

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

Writes occur during clk=0 through the RW port. Both bytes are written simultaneously (16-bit write port), requiring row decode, write enable, and data drivers.

#### Row Decoder (w_sel -> 8 one-hot lines)

A 3-to-8 decoder for all 8 rows using `w_sel[2:0]`:

| Component | Count | Tx/each | Transistors |
|---|---|---|---|
| INV (complement w_sel[2:0]) | 3 | 2 | 6 |
| AND3 (NAND3 + INV, one per row) | 8 | 8 | 64 |
| **Subtotal** | | | **70** |

#### Word Line Gating

Each decoded row line is ANDed with w_we to produce the write word line. Both byte halves share one word line (no byte select):

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| AND2 | WL[i] = row[i] AND w_we | 8 | 6 | 48 |
| **Subtotal** | | | | **48** |

#### Write Drivers

Generate complementary data for the bit lines. 16 data/complement pairs drive all 16 columns:

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| INV | ~w_data[i] (complement) | 16 | 2 | 32 |
| **Subtotal** | | | | **32** |

**Write decode + drivers total: 150T**

#### Write Staging

Both the standard cell regfile and the SRAM equivalent need write staging. The standard cell version uses leader latches (included in the module). The SRAM equivalent uses input latches to hold w_data/w_sel/w_we stable during the write pulse:

| Component | Count | Tx/each | Transistors |
|---|---|---|---|
| Data latch (TG + inverter loop) | 16 | 6 | 96 |
| Address latch | 3 | 6 | 18 |
| Enable latch (with reset) | 1 | 8 | 8 |
| **Subtotal** | | | **122** |

**Write path total: 150 + 122 = 272T**

### Read Path 1 (RW Port, Differential)

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

### Read Path 2 (R-Only Port, Single-Ended)

The 8T cell's dedicated read port: N5 (access, gated by read word line) in series with N6 (driver, gated by QB). Read bit line (RBL) is pulled high by a keeper; selected cell conditionally discharges it. Port 2 uses a 3-bit address. Full 16-bit output (no byte select).

| Component | Purpose | Count | Tx/each | Transistors |
|---|---|---|---|---|
| INV | complement r2_sel inputs | 3 | 2 | 6 |
| AND3 | row decode (one per row) | 8 | 8 | 64 |
| PMOS | pull-up keeper RBL[0..15] | 16 | 1 | 16 |
| **Subtotal** | | | | **86** |

### Grand Total

| Component | Transistors | % |
|---|---|---|
| Storage array (128 x 8T) | 1,024 | 68.3% |
| Write path (decode + drivers + staging) | 272 | 18.1% |
| Read path 1 (RW, differential) | 118 | 7.9% |
| Read path 2 (R, single-ended) | 86 | 5.7% |
| **Total** | **1,500** | **100%** |

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
| Write staging | 20 leader latches × 20T = 400 | 20 latches × 6T = 122 (TG-based) |
| Storage | 128 follower latches × 20T = 2,560 | 128 cells × 8T = 1,024 |
| Peripherals | decode + read mux trees | Decode + drivers = 354 |
| **Total** | **(from synthesis)** | **1,500** |

Standard cell counts vary slightly between synthesis runs. The exact count for each build is extracted automatically from `stat.json`.

The SRAM saves on both storage (8T vs 20T per bit) and peripherals (word-line decode replaces explicit mux trees — asserting one word line selects all 16 bits of one register, eliminating the 8:1 mux per bit that standard cells require). Write staging is present in both: leader latches in standard cells, input latches in SRAM.

## SRAM-Adjusted Figures

These figures are computed automatically by `transistor_count.py` from each build's `stat.json`. The regfile standard cell count is extracted from the `riscyv02_regfile` sub-module (preserved by `keep_hierarchy`), ensuring consistency with the total.

| Metric | Value |
|---|---|
| Register file (8T SRAM equivalent) | 1,500 |
| Other values | (computed by `transistor_count.py`) |

## Methodology Notes

1. **Transistor counts are exact**, not estimates. Standard cell counts come from the PDK's CDL SPICE netlist (one M-line = one MOSFET). SRAM counts come from the circuit design above, using textbook CMOS gate structures.

2. **The 8T cell transistor count is definitional.** An 8T SRAM cell has 8 transistors by definition — that's what "8T" means. This is not a process-specific or PDK-specific number.

3. **The same methodology applies to both designs.** The Arlet 6502's registers (A, X, Y, SP) are asymmetric special-purpose registers wired to different datapath elements. They are not a regular array and would not use SRAM in any implementation. Applying this methodology to the 6502 yields zero discount.

4. **No SRAM macro exists** at this size for IHP sg13g2. The smallest available macro (64x32, 2048 bits) stores 16x more than needed and is physically larger than the entire RISCY-V02 design. The SRAM analysis here is a paper design representing what a custom chip would use.
