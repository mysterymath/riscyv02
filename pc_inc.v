module pc_inc(in, o);
input [15:1] in;
output [15:1] o;

wire [15:1] c_out;
wire lookahead;
genvar i;
generate
  HAX1 inc_1(in[1], 1, o[1], c_out[1]);
  for (i = 2; i < 9; i = i + 1)
    HAX1 inc_i(in[i], c_out[i-1], o[i], c_out[i]);
  assign lookahead = in[1] & in[2] & in[3] & in[4] & in[5] & in[6] & in[7] & in[8];
  HAX1 inc_9(in[9], lookahead, o[9], c_out[9]);
  for (i = 10; i < 16; i = i + 1)
    HAX1 inc_i(in[i], c_out[i-1], o[i], c_out[i]);
endgenerate

endmodule

(* blackbox *)
module HAX1(A, B, YC, YS);
input A;
input B;
output YC;
output YS;
endmodule
