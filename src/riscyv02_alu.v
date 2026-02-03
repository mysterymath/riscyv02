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
    input  wire       start,   // 1 = new op (ci=0), 0 = continue (ci=latched carry)
    output wire [7:0] result,
    output wire       co
);
  reg carry;
  wire ci = start ? 1'b0 : carry;
  assign {co, result} = a + b + ci;

  always @(negedge clk or negedge rst_n)
    if (!rst_n) carry <= 1'b0;
    else        carry <= co;
endmodule
