/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 *
 * Two-phase latch register file: 8 x 16-bit GP registers with 8-bit interface,
 * plus a banked R6 for interrupt context.
 *
 * When i_bit=1, register 6 reads/writes map to a separate physical register
 * (regs_int) instead of the normal regs[6]. This provides automatic save/restore
 * of R6 across interrupt entry/exit — the interrupt handler sees banked R6
 * (containing the return address), while the interrupted code's R6 is preserved.
 *
 * Leader latches (sg13g2_dlhrq_1, transparent-high) capture w_data,
 * w_sel, w_hi, and w_we at the falling clock edge.  Follower latches
 * (also sg13g2_dlhrq_1) use GATE = ~clk & write_enable to pass the
 * leader's captured value to the selected register byte during clk=0.
 *
 * Both phases use sg13g2_dlhrq_1 (27.22 µm²) rather than sg13g2_dllrq_1
 * (29.03 µm²) for followers, saving ~232 µm² across 128 follower latches.
 * The ~clk inverter in the follower gate path actually improves hold-time
 * margin: leaders close (clk falls) before followers open (~clk rises
 * through the inverter), providing a natural non-overlap guarantee.
 *
 * Single read port: execute only.  Fetch no longer needs register
 * access since JR is handled by execute.
 */

`default_nettype none

module riscyv02_regfile (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        i_bit,      // Interrupt mode: bank R6 when high

    // Write port (8-bit)
    input  wire [2:0]  w_sel,
    input  wire        w_hi,       // Select high byte for write
    input  wire [7:0]  w_data,
    input  wire        w_we,

    // Read port 1 (8-bit)
    input  wire [2:0]  r1_sel,
    input  wire        r1_hi,       // Select high byte for read
    output wire [7:0]  r1,

    // Read port 2 (8-bit)
    input  wire [2:0]  r2_sel,
    input  wire        r2_hi,      // Select high byte for read
    output wire [7:0]  r2
);

  // Phase 1 — Leader latches (sg13g2_dlhrq_1): transparent when GATE=clk=1,
  // capture at negedge. These hold w_data/w_sel/w_hi/w_we stable during clk=0
  // so follower inputs never depend on live combinational paths.
  wire [7:0] w_data_held;
  wire [2:0] w_sel_held;
  wire       w_hi_held;
  wire       w_we_held;
  wire       i_bit_held;

  generate
    genvar li;
    for (li = 0; li < 8; li = li + 1) begin : gen_leader_data
      sg13g2_dlhrq_1 u_leader (
        .D(w_data[li]),
        .GATE(clk),
        .RESET_B(rst_n),
        .Q(w_data_held[li])
      );
    end
    for (li = 0; li < 3; li = li + 1) begin : gen_leader_sel
      sg13g2_dlhrq_1 u_leader (
        .D(w_sel[li]),
        .GATE(clk),
        .RESET_B(rst_n),
        .Q(w_sel_held[li])
      );
    end
  endgenerate

  sg13g2_dlhrq_1 u_leader_hi (
    .D(w_hi),
    .GATE(clk),
    .RESET_B(rst_n),
    .Q(w_hi_held)
  );

  sg13g2_dlhrq_1 u_leader_we (
    .D(w_we),
    .GATE(clk),
    .RESET_B(rst_n),
    .Q(w_we_held)
  );

  sg13g2_dlhrq_1 u_leader_ibit (
    .D(i_bit),
    .GATE(clk),
    .RESET_B(rst_n),
    .Q(i_bit_held)
  );

  // Phase 2 — Follower latches (sg13g2_dlhrq_1): transparent when GATE=1,
  // i.e. when ~clk & write_enable. Selected register byte passes leader's
  // captured value during clk=0; all others hold.
  //
  // The ~clk inverter provides natural non-overlap: leaders close at negedge
  // before followers open through the inverter delay.
  wire [15:0] regs [0:7];
  wire clk_n = ~clk;

  generate
    genvar gi, bi;
    for (gi = 0; gi < 8; gi = gi + 1) begin : gen_reg
      // Register selected for write?
      // For register 6, only write when NOT in interrupt mode (normal R6).
      wire wen = w_we_held && (w_sel_held == gi[2:0]) &&
                 ((gi != 6) || !i_bit_held);
      // Byte-select: which byte of this register to write
      wire write_lo = wen & ~w_hi_held;
      wire write_hi = wen & w_hi_held;
      // Gate signals: transparent when clk=0 AND write enabled for this byte
      wire gate_lo = clk_n & write_lo;
      wire gate_hi = clk_n & write_hi;
      for (bi = 0; bi < 8; bi = bi + 1) begin : gen_bit_lo
        sg13g2_dlhrq_1 u_follower (
          .D(w_data_held[bi]),
          .GATE(gate_lo),
          .RESET_B(rst_n),
          .Q(regs[gi][bi])
        );
      end
      for (bi = 0; bi < 8; bi = bi + 1) begin : gen_bit_hi
        sg13g2_dlhrq_1 u_follower (
          .D(w_data_held[bi]),
          .GATE(gate_hi),
          .RESET_B(rst_n),
          .Q(regs[gi][bi+8])
        );
      end
    end
  endgenerate

  // Banked R6 (interrupt context): written when w_sel==6 AND i_bit_held
  wire [15:0] regs_int;
  wire wen_int = w_we_held && (w_sel_held == 3'd6) && i_bit_held;
  wire write_int_lo = wen_int & ~w_hi_held;
  wire write_int_hi = wen_int & w_hi_held;
  wire gate_int_lo = clk_n & write_int_lo;
  wire gate_int_hi = clk_n & write_int_hi;

  generate
    for (bi = 0; bi < 8; bi = bi + 1) begin : gen_int_lo
      sg13g2_dlhrq_1 u_follower (
        .D(w_data_held[bi]),
        .GATE(gate_int_lo),
        .RESET_B(rst_n),
        .Q(regs_int[bi])
      );
    end
    for (bi = 0; bi < 8; bi = bi + 1) begin : gen_int_hi
      sg13g2_dlhrq_1 u_follower (
        .D(w_data_held[bi]),
        .GATE(gate_int_hi),
        .RESET_B(rst_n),
        .Q(regs_int[bi+8])
      );
    end
  endgenerate

  // Read ports: banking via extended 9-entry array with 4-bit select.
  // Putting banking on the select path (computed in parallel with mux tree)
  // rather than the data path (serial with mux tree) avoids adding delay
  // to the critical regfile→dout→uio_out timing path.
  wire [15:0] regs_ext [0:8];
  generate
    for (gi = 0; gi < 8; gi = gi + 1) begin : gen_ext
      assign regs_ext[gi] = regs[gi];
    end
  endgenerate
  assign regs_ext[8] = regs_int;

  // Port 1: 9:1 mux with banking folded into select
  wire [3:0] r1_sel_ext = (r1_sel == 3'd6 && i_bit) ? 4'd8 : {1'b0, r1_sel};
  wire [15:0] r_full = regs_ext[r1_sel_ext];
  assign r1 = r1_hi ? r_full[15:8] : r_full[7:0];

  // Port 2: 9:1 mux with banking folded into select
  wire [3:0] r2_sel_ext = (r2_sel == 3'd6 && i_bit) ? 4'd8 : {1'b0, r2_sel};
  wire [15:0] r2_full = regs_ext[r2_sel_ext];
  assign r2 = r2_hi ? r2_full[15:8] : r2_full[7:0];

endmodule
