/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// =========================================================================
// Fetch unit: instruction fetch only, no register access.
//
// Always speculates sequential PC.  When execute resolves control flow
// (JR, branches), it signals redirect to reset fetch to the correct PC.
//
// The ir output is combinational: {uio_in, ir_lo} during F_HI.
// ir_valid pulses for one cycle when a complete instruction is available.
// Execute must capture ir immediately when ir_valid=1.
// =========================================================================
module riscyv02_fetch (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,
    input  wire        bus_free,
    input  wire        redirect,
    input  wire [15:0] redirect_pc,
    output wire        ir_valid,
    output wire [15:0] ir,
    output wire [15:0] ab
);

  localparam F_LO = 1'd0;
  localparam F_HI = 1'd1;

  reg        state;
  reg [15:0] addr;
  reg [7:0]  ir_lo;  // Low byte staging register

  wire [15:0] seq_pc = {addr[15:1] + 15'd1, 1'b0};

  // Bus address
  assign ab = (state == F_HI) ? {addr[15:1], 1'b1} : addr;

  // Instruction output: combinational, valid only when ir_valid=1
  assign ir = {uio_in, ir_lo};
  assign ir_valid = (state == F_HI) && bus_free && !redirect;

  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= F_LO;
      ir_lo <= 8'h00;
      addr  <= 16'h0000;
    end else if (redirect) begin
      // Control flow redirect from execute: reset to new PC
      addr  <= redirect_pc;
      state <= F_LO;
    end else begin
      case (state)
        F_LO: if (bus_free) begin
          ir_lo <= uio_in;
          state <= F_HI;
        end

        F_HI: if (bus_free) begin
          addr  <= seq_pc;
          state <= F_LO;
        end

        default: state <= F_LO;
      endcase
    end
  end

endmodule
