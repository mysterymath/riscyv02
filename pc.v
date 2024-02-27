module pc(clk, n_reset, inc_en, w_en, w, r);
input clk;
input n_reset;
input inc_en;
input w_en;
input [15:1] w;
output reg [15:1] r;

wire [15:1] c_out;
wire [15:1] inc;

wire lookahead;
genvar i;
generate
  HAX1 inc_1(r[1], 1, inc[1], c_out[1]);
  for (i = 2; i < 9; i = i + 1)
    HAX1 inc_i(r[i], c_out[i-1], inc[i], c_out[i]);
  assign lookahead = r[1] & r[2] & r[3] & r[4] & r[5] & r[6] & r[7] & r[8];
  HAX1 inc_9(r[9], lookahead, inc[9], c_out[9]);
  for (i = 10; i < 16; i = i + 1)
    HAX1 inc_i(r[i], c_out[i-1], inc[i], c_out[i]);
endgenerate

always @(posedge clk)
  if (!n_reset)
    r <= 16'b0;
  else if (w_en)
    r <= w;
  else if (inc_en)
    r <= inc;

endmodule

(* blackbox *)
module HAX1(A, B, YC, YS);
input A;
input B;
output YC;
output YS;
endmodule
