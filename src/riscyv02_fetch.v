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
// Instruction holding: fetch holds the instruction until execute accepts it.
// F_HI: instruction available combinationally from {uio_in, ir_r[7:0]}
// F_HOLD: instruction fully registered in ir_r, waiting for execute to accept
// =========================================================================
module riscyv02_fetch (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,
    input  wire        bus_free,
    input  wire        ir_accept,
    input  wire        redirect,
    input  wire [15:0] redirect_pc,
    output wire        ir_valid,
    output wire [15:0] ir,
    output wire [15:0] ab
);

  localparam F_LO   = 2'd0;
  localparam F_HI   = 2'd1;
  localparam F_HOLD = 2'd2;

  reg [1:0]  state;
  reg [15:0] addr;
  reg [15:0] ir_r;    // Low byte in F_HI, full instruction in F_HOLD

  wire [15:0] seq_pc = {addr[15:1] + 15'd1, 1'b0};

  // Bus address: only active in F_LO/F_HI, not F_HOLD
  assign ab = (state == F_HI) ? {addr[15:1], 1'b1} : addr;

  // Instruction output: combinational in F_HI, registered in F_HOLD
  assign ir = (state == F_HOLD) ? ir_r : {uio_in, ir_r[7:0]};
  assign ir_valid = ((state == F_HI) && bus_free && !redirect) || (state == F_HOLD);

  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= F_LO;
      ir_r  <= 16'h0000;
      addr  <= 16'h0000;
    end else if (redirect) begin
      // Control flow redirect from execute: reset to new PC
      addr  <= redirect_pc;
      state <= F_LO;
    end else begin
      case (state)
        F_LO: if (bus_free) begin
          ir_r[7:0] <= uio_in;
          state     <= F_HI;
        end

        F_HI: if (bus_free) begin
          if (ir_accept) begin
            // Fast path: execute accepted immediately
            addr  <= seq_pc;
            state <= F_LO;
          end else begin
            // Slow path: register full instruction, wait for execute
            ir_r  <= {uio_in, ir_r[7:0]};
            state <= F_HOLD;
          end
        end

        F_HOLD: if (ir_accept) begin
          addr  <= seq_pc;
          state <= F_LO;
        end

        default: state <= F_LO;
      endcase
    end
  end

endmodule
