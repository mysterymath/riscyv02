/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 *
 * Register file: 8 x 16-bit GP registers plus a 9th entry for the
 * Exception PC (EPC) register.  2 read ports (16-bit) + 1 write port (16-bit).
 *
 * EPC is accessible as entry 8 via 4-bit select lines on port 1 and the
 * write port. Port 2 remains 3-bit (GP registers only).
 *
 * Storage uses sg13g2_dlhrq_1 follower latches: GATE = ~clk & write_enable.
 * Write inputs (w_data, w_sel, w_we) must be stable during clk=0; the
 * execute module's pipeline registers guarantee this.
 *
 * Read ports are purely combinational (mux trees on latch outputs).
 */

`default_nettype none

module riscyv02_regfile (
    input  wire        clk,
    input  wire        rst_n,

    // Write port (16-bit) — inputs must be registered (stable during clk=0)
    input  wire [3:0]  w_sel,      // Bit 3 selects EPC (entry 8)
    input  wire [15:0] w_data,
    input  wire        w_we,

    // Read port 1 (16-bit)
    input  wire [3:0]  r1_sel,     // Bit 3 selects EPC (entry 8)
    output wire [15:0] r1,

    // Read port 2 (16-bit)
    input  wire [2:0]  r2_sel,
    output wire [15:0] r2
);

  // Storage latches (sg13g2_dlhrq_1): transparent when GATE=1,
  // i.e. when ~clk & write_enable. Selected register passes
  // write data during clk=0; all others hold.
  wire [15:0] regs [0:7];
  wire clk_n = ~clk;

  generate
    genvar gi, bi;
    for (gi = 0; gi < 8; gi = gi + 1) begin : gen_reg
      // GP register selected for write? Only when w_sel[3]=0 (not EPC).
      wire wen = w_we && !w_sel[3] && (w_sel[2:0] == gi[2:0]);
      // Single gate: both bytes written together
      wire gate = clk_n & wen;
      for (bi = 0; bi < 8; bi = bi + 1) begin : gen_bit_lo
        sg13g2_dlhrq_1 u_follower (
          .D(w_data[bi]),
          .GATE(gate),
          .RESET_B(rst_n),
          .Q(regs[gi][bi])
        );
      end
      for (bi = 0; bi < 8; bi = bi + 1) begin : gen_bit_hi
        sg13g2_dlhrq_1 u_follower (
          .D(w_data[bi+8]),
          .GATE(gate),
          .RESET_B(rst_n),
          .Q(regs[gi][bi+8])
        );
      end
    end
  endgenerate

  // EPC register (entry 8): written when w_sel[3]=1
  wire [15:0] regs_epc;
  wire wen_epc = w_we && w_sel[3];
  wire gate_epc = clk_n & wen_epc;

  generate
    for (bi = 0; bi < 8; bi = bi + 1) begin : gen_epc_lo
      sg13g2_dlhrq_1 u_follower (
        .D(w_data[bi]),
        .GATE(gate_epc),
        .RESET_B(rst_n),
        .Q(regs_epc[bi])
      );
    end
    for (bi = 0; bi < 8; bi = bi + 1) begin : gen_epc_hi
      sg13g2_dlhrq_1 u_follower (
        .D(w_data[bi+8]),
        .GATE(gate_epc),
        .RESET_B(rst_n),
        .Q(regs_epc[bi+8])
      );
    end
  endgenerate

  // Read ports: extended 9-entry array with 4-bit select for port 1.
  // Port 2 uses standard 8-entry array (no EPC access needed).
  wire [15:0] regs_ext [0:8];
  generate
    for (gi = 0; gi < 8; gi = gi + 1) begin : gen_ext
      assign regs_ext[gi] = regs[gi];
    end
  endgenerate
  assign regs_ext[8] = regs_epc;

  // Port 1: 9:1 mux (r1_sel[3] selects EPC) — full 16-bit output
  assign r1 = regs_ext[r1_sel];

  // Port 2: 8:1 mux (GP registers only) — full 16-bit output
  assign r2 = regs[r2_sel];

endmodule
