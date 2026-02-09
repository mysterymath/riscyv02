# RISCY-V02 + IS61C256AL SRAM PCB Interface Design

## Overview

Connect the RISCY-V02 CPU (on a TT IHP board) to an IS61C256AL-10 32Kx8
asynchronous SRAM. The CPU uses a 6502-style muxed bus protocol where address
and data share the `uio[7:0]` pins across two clock phases.

## Bus Protocol Recap

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

## Components

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

## Glue Logic (U4: 74HCT00, quad NAND)

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

## Connections

### Address Latches (U1, U2: 74HCT573)

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

### Data Bus Transceiver (U3: 74LVC245)

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

### SRAM (U5: IS61C256AL-10TL)

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

## Timing Analysis

### Read Cycle

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

### Write Cycle

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

### Practical Clock Speed

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

## Voltage Level Summary

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

## Schematic (text)

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

## SRAM Hold Time Requirements

All zero:
- **tHA = 0ns**: address hold from write end
- **tHD = 0ns**: data hold from write end
- **tOHA = 2ns**: output hold from address change (SRAM's guarantee to us,
  irrelevant — we sample data before address changes)

No hold violations are possible against this asynchronous SRAM.
All timing requirements are setup-like (minimum pulse widths and setup times
before write-terminating edges), solvable by slowing the clock.
