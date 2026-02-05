/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 *
 * Two-phase latch register file: 8 x 16-bit GP registers with 8-bit interface.
 *
 * Leader latches (sg13g2_dlhrq_1, transparent-high) capture w_data,
 * w_sel, w_hi, and w_we at the falling clock edge.  Follower latches
 * (sg13g2_dllrq_1, transparent-low) pass the leader's captured value
 * to the selected register byte during clk=0.  The opposite polarities
 * guarantee by construction that leaders and followers are never
 * simultaneously transparent.
 *
 * Single read port: execute only.  Fetch no longer needs register
 * access since JR is handled by execute.
 */

`default_nettype none

module riscyv02_regfile (
    input  wire        clk,
    input  wire        rst_n,

    // Write port (8-bit)
    input  wire [2:0]  w_sel,
    input  wire        w_hi,       // Select high byte for write
    input  wire [7:0]  w_data,
    input  wire        w_we,

    // Read port (8-bit)
    input  wire [2:0]  r_sel,
    input  wire        r_hi,       // Select high byte for read
    output wire [7:0]  r
);

  // Phase 1 — Leader latches (sg13g2_dlhrq_1): transparent when GATE=clk=1,
  // capture at negedge. These hold w_data/w_sel/w_hi/w_we stable during clk=0
  // so follower inputs never depend on live combinational paths.
  wire [7:0] w_data_held;
  wire [2:0] w_sel_held;
  wire       w_hi_held;
  wire       w_we_held;

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

  // Phase 2 — Follower latches (sg13g2_dllrq_1): transparent when GATE_N=0,
  // i.e. when ~(clk | ~wen[i]) = ~clk & wen[i]. Selected register byte passes
  // leader's captured value during clk=0; all others hold.
  //
  // Byte-select writes: each follower's gate_n becomes clk | ~(wen & byte_match)
  //   - Low bytes (bi<8): byte_match = ~w_hi_held
  //   - High bytes (bi>=8): byte_match = w_hi_held
  wire [15:0] regs [0:7];

  generate
    genvar gi, bi;
    for (gi = 0; gi < 8; gi = gi + 1) begin : gen_reg
      // Register selected for write?
      wire wen = w_we_held && (w_sel_held == gi[2:0]);
      // Byte-select: which byte of this register to write
      wire write_lo = wen & ~w_hi_held;
      wire write_hi = wen & w_hi_held;
      // Gate signals: transparent when clk=0 AND write enabled for this byte
      // (GATE_N=0 means transparent, so gate_n = clk | ~write_xx)
      wire gate_n_lo = clk | ~write_lo;
      wire gate_n_hi = clk | ~write_hi;
      for (bi = 0; bi < 8; bi = bi + 1) begin : gen_bit_lo
        sg13g2_dllrq_1 u_follower (
          .D(w_data_held[bi]),
          .GATE_N(gate_n_lo),
          .RESET_B(rst_n),
          .Q(regs[gi][bi])
        );
      end
      for (bi = 0; bi < 8; bi = bi + 1) begin : gen_bit_hi
        sg13g2_dllrq_1 u_follower (
          .D(w_data_held[bi]),
          .GATE_N(gate_n_hi),
          .RESET_B(rst_n),
          .Q(regs[gi][bi+8])
        );
      end
    end
  endgenerate

  // Read port: 8:1 register mux, then 2:1 hi/lo byte mux
  wire [15:0] r_full = regs[r_sel];
  assign r = r_hi ? r_full[15:8] : r_full[7:0];

endmodule
