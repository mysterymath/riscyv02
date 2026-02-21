/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 *
 * Register file: 8 x 16-bit GP registers.
 * 2 read ports (16-bit) + 1 write port (16-bit).
 *
 * Write inputs are combinational from execute. Leader latches (transparent-
 * high) capture w_data/w_sel/w_we during clk=1; follower latches (gated by
 * ~clk & decoded_wen) store data during clk=0. This leader-follower pair
 * acts as a negedge-triggered write: final value captured = signal at negedge.
 *
 * Read ports are purely combinational (mux trees on follower latch outputs).
 */

`default_nettype none

(* keep_hierarchy *)
module riscyv02_regfile (
    input  wire        clk,
    input  wire        rst_n,

    // Write port (16-bit) — combinational inputs, internally staged
    input  wire [2:0]  w_sel,
    input  wire [15:0] w_data,
    input  wire        w_we,

    // Read port 1 (16-bit)
    input  wire [2:0]  r1_sel,
    output wire [15:0] r1,

    // Read port 2 (16-bit)
    input  wire [2:0]  r2_sel,
    output wire [15:0] r2
);

  // Leader latches: transparent when clk=1, hold at negedge.
  // Capture combinational write inputs from execute for stable
  // presentation to follower latches during clk=0.
  wire [15:0] w_data_r;
  wire [2:0]  w_sel_r;
  wire        w_we_r;

  generate
    genvar li;
    for (li = 0; li < 16; li = li + 1) begin : gen_leader_data
      sg13g2_dlhrq_1 u_leader (
        .D(w_data[li]), .GATE(clk), .RESET_B(rst_n), .Q(w_data_r[li])
      );
    end
    for (li = 0; li < 3; li = li + 1) begin : gen_leader_sel
      sg13g2_dlhrq_1 u_leader (
        .D(w_sel[li]), .GATE(clk), .RESET_B(rst_n), .Q(w_sel_r[li])
      );
    end
  endgenerate

  sg13g2_dlhrq_1 u_leader_we (
    .D(w_we), .GATE(clk), .RESET_B(rst_n), .Q(w_we_r)
  );

  // Follower latches: transparent when ~clk & write_enable.
  // Selected register passes write data during clk=0; all others hold.
  wire [15:0] regs [0:7];
  wire clk_n = ~clk;

  generate
    genvar gi, bi;
    for (gi = 0; gi < 8; gi = gi + 1) begin : gen_reg
      wire wen = w_we_r && (w_sel_r == gi[2:0]);
      wire gate = clk_n & wen;
      for (bi = 0; bi < 16; bi = bi + 1) begin : gen_bit
        sg13g2_dlhrq_1 u_follower (
          .D(w_data_r[bi]), .GATE(gate), .RESET_B(rst_n), .Q(regs[gi][bi])
        );
      end
    end
  endgenerate

  // Port 1: 8:1 mux (GP registers)
  assign r1 = regs[r1_sel];

  // Port 2: 8:1 mux (GP registers)
  assign r2 = regs[r2_sel];

endmodule
