/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 *
 * Two-phase latch register file: 8 x 16-bit GP registers.
 *
 * Leader latches (sg13g2_dlhrq_1, transparent-high) capture w_data,
 * w_sel, and w_we at the falling clock edge.  Follower latches
 * (sg13g2_dllrq_1, transparent-low) pass the leader's captured value
 * to the selected register during clk=0.  The opposite polarities
 * guarantee by construction that leaders and followers are never
 * simultaneously transparent.
 */

`default_nettype none

module riscyv02_regfile (
    input  wire        clk,
    input  wire        rst_n,

    // Write port
    input  wire [2:0]  w_sel,
    input  wire [15:0] w_data,
    input  wire        w_we,

    // Read port (execute)
    input  wire [2:0]  exec_r_sel,
    output wire [15:0] exec_r,

    // Read port (fetch)
    input  wire [2:0]  fetch_r_sel,
    output wire [15:0] fetch_r
);

  // Phase 1 — Leader latches (sg13g2_dlhrq_1): transparent when GATE=clk=1,
  // capture at negedge. These hold w_data/w_sel/w_we stable during clk=0
  // so follower inputs never depend on live combinational paths.
  wire [15:0] w_data_held;
  wire [2:0]  w_sel_held;
  wire        w_we_held;

  generate
    genvar li;
    for (li = 0; li < 16; li = li + 1) begin : gen_leader_data
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

  sg13g2_dlhrq_1 u_leader_we (
    .D(w_we),
    .GATE(clk),
    .RESET_B(rst_n),
    .Q(w_we_held)
  );

  // Phase 2 — Follower latches (sg13g2_dllrq_1): transparent when GATE_N=0,
  // i.e. when ~(clk | ~wen[i]) = ~clk & wen[i]. Selected register passes
  // leader's captured value during clk=0; all others hold.
  wire [15:0] regs [0:7];

  generate
    genvar gi, bi;
    for (gi = 0; gi < 8; gi = gi + 1) begin : gen_reg
      wire wen = w_we_held && (w_sel_held == gi[2:0]);
      wire gate_n = clk | ~wen;
      for (bi = 0; bi < 16; bi = bi + 1) begin : gen_bit
        sg13g2_dllrq_1 u_follower (
          .D(w_data_held[bi]),
          .GATE_N(gate_n),
          .RESET_B(rst_n),
          .Q(regs[gi][bi])
        );
      end
    end
  endgenerate

  // Read ports
  assign exec_r  = regs[exec_r_sel];
  assign fetch_r = regs[fetch_r_sel];

endmodule
