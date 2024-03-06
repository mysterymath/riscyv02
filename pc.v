module pc(clk, w, r, r_next);
input clk;
input [15:1] w;
output reg [15:1] r;
output [15:1] r_next;

wire [15:1] c_out;

wire lookahead;
genvar i;
generate
  HAX1 inc_1(r[1], 1'b1, r_next[1], c_out[1]);
  for (i = 2; i < 9; i = i + 1)
    HAX1 inc_i(r[i], c_out[i-1], r_next[i], c_out[i]);
  assign lookahead = r[1] & r[2] & r[3] & r[4] & r[5] & r[6] & r[7] & r[8];
  HAX1 inc_9(r[9], lookahead, r_next[9], c_out[9]);
  for (i = 10; i < 16; i = i + 1)
    HAX1 inc_i(r[i], c_out[i-1], r_next[i], c_out[i]);
endgenerate

always @(negedge clk)
  r <= w;

endmodule

(* blackbox *)
module HAX1(A, B, YC, YS);
input A;
input B;
output YC;
output YS;
endmodule
