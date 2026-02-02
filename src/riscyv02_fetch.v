/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// =========================================================================
// Fetch unit: owns fetch_addr internally, speculatively resolves JR.
//
// fetch_addr is where fetch is currently looking; it advances by 2 on
// each completed fetch or redirects to a JR target on the same negedge.
// ir_addr is the address of the delivered instruction.
// =========================================================================
module riscyv02_fetch (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,
    input  wire [15:0] fetch_reg,
    input  wire        exec_busy,
    output reg         ir_valid,
    output reg  [15:0] new_ir,
    output wire [15:0] fetch_ab,
    output wire [2:0]  fetch_reg_sel,
    output reg  [15:0] ir_addr
);

  localparam F_LO = 1'b0;
  localparam F_HI = 1'b1;

  reg       f_state;
  reg [7:0] ir_lo;
  reg [15:0] fetch_addr;

  // Combinational decode of the instruction being assembled
  wire [15:0] fetched_ir = {uio_in, ir_lo};
  wire fetch_is_jr = (f_state == F_HI) && (fetched_ir[15:9] == 7'b1011100);
  assign fetch_reg_sel = fetched_ir[2:0];
  wire [15:0] fetch_sext_off_x2 = {{9{fetched_ir[8]}}, fetched_ir[8:3], 1'b0};
  wire [15:0] jr_target = fetch_reg + fetch_sext_off_x2;
  wire [15:0] addr_plus2 = {fetch_addr[15:1] + 15'd1, 1'b0};

  assign fetch_ab = (f_state == F_HI) ? {fetch_addr[15:1], 1'b1} : fetch_addr;

  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      f_state    <= F_LO;
      ir_lo      <= 8'h00;
      ir_valid   <= 1'b0;
      new_ir     <= 16'h0000;
      fetch_addr <= 16'h0000;
      ir_addr    <= 16'h0000;
    end else begin
      ir_valid <= 1'b0;

      if (!exec_busy) begin
        case (f_state)
          F_LO: begin
            ir_lo   <= uio_in;
            f_state <= F_HI;
          end
          F_HI: begin
            new_ir     <= fetched_ir;
            ir_valid   <= 1'b1;
            ir_addr    <= fetch_addr;
            fetch_addr <= fetch_is_jr ? jr_target : addr_plus2;
            f_state    <= F_LO;
          end
          default: f_state <= F_LO;
        endcase
      end
    end
  end

endmodule
