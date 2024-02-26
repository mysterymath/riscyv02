module pc(clk, n_reset, inc_en, w_en, w, r);
input clk;
input n_reset;
input inc_en;
input w_en;
input [15:1] w;
output reg [15:1] r;

wire [15:1] c_out;
wire [15:1] inc;

genvar i;
generate
  HAX1 lo(r[1], 1, inc[1], c_out[1]);
  for (i = 2; i < 16; i = i + 1)
    HAX1 ha_i(r[i], c_out[i-1], inc[i], c_out[i]);
endgenerate

always @(posedge clk)
  if (!n_reset)
    r <= 15'b0;
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
