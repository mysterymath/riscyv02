/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module riscyv02_alu (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] a,
    input  wire [7:0] b,
    input  wire       new_op,  // 1 = new operation (ci=0), 0 = continue (ci=latched carry)
    output wire [7:0] result
);
  reg carry;
  wire ci = new_op ? 1'b0 : carry;
  wire co;
  assign {co, result} = a + b + ci;

  always @(negedge clk or negedge rst_n)
    if (!rst_n) carry <= 1'b0;
    else        carry <= co;
endmodule
