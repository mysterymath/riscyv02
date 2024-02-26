module pc(clk, n_reset, inc_en, w_en, w, r);
input clk;
input n_reset;
input inc_en;
input w_en;
input [15:0] w;
output reg [15:0] r;

wire [15:0] c_out;
wire [15:0] inc;

wire lookahead;
genvar i;
generate
  HAX1 inc_1(r[0], 1, inc[0], c_out[0]);
  for (i = 1; i < 8; i = i + 1)
    HAX1 inc_i(r[i], c_out[i-1], inc[i], c_out[i]);
  assign lookahead = r[0] & r[1] & r[2] & r[3] & r[4] & r[5] & r[6] & r[7];
  HAX1 inc_9(r[8], lookahead, inc[8], c_out[8]);
  for (i = 9; i < 16; i = i + 1)
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
