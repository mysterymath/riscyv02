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
// =========================================================================
module riscyv02_fetch (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,
    input  wire        bus_free,
    input  wire        exec_busy,
    input  wire        ir_accept,
    input  wire        redirect,
    input  wire [15:0] redirect_pc,
    output reg         ir_valid,
    output reg  [15:0] new_ir,
    output wire [15:0] ab
);

  localparam F_LO = 1'd0;
  localparam F_HI = 1'd1;

  reg        state;
  reg [7:0]  ir_lo;
  reg [15:0] addr;

  // Combinational decode of the instruction being assembled
  wire [15:0] fetched_ir = {uio_in, ir_lo};

  wire [15:0] seq_pc = {addr[15:1] + 15'd1, 1'b0};

  // Bus address
  assign ab = (state == F_HI) ? {addr[15:1], 1'b1} : addr;

  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state    <= F_LO;
      ir_lo    <= 8'h00;
      ir_valid <= 1'b0;
      new_ir   <= 16'h0000;
      addr     <= 16'h0000;
    end else if (redirect) begin
      // Control flow redirect from execute: reset to new PC
      addr     <= redirect_pc;
      ir_valid <= 1'b0;
      state    <= F_LO;
    end else begin
      if (ir_accept)
        ir_valid <= 1'b0;

      case (state)
        F_LO: if (bus_free) begin
          ir_lo <= uio_in;
          state <= F_HI;
        end

        F_HI: if (bus_free && !exec_busy) begin
          new_ir   <= fetched_ir;
          ir_valid <= 1'b1;
          addr     <= seq_pc;
          state    <= F_LO;
        end

        default: state <= F_LO;
      endcase
    end
  end

endmodule
