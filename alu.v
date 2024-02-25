module alu(r1, r2, c_in, rd, c_out);
input [7:0] r1;
input [7:0] r2;
input c_in;
output [7:0] rd;
output c_out;

wire add_c_out[7:0];
HAX1 low(r1[0], r2[0], add_c_out[0], rd[0]);
genvar i;
generate
  for (i = 1; i < 8; i = i + 1)
    FAX1 add_i(r1[i], r2[i], add_c_out[i-1], add_c_out[i], rd[i]);
endgenerate

wire and_out[7:0];
assign and_out = r1 & r2;

wire or_out[7:0];
assign or_out = r1 | r2;

wire xor_out[7:0];
assign xor_out = r1 ^ r2;

assign c_out = add_c_out[7];

endmodule

(* blackbox *)
module HAX1(A, B, YC, YS);
input A;
input B;
output YC;
output YS;
endmodule

(* blackbox *)
module FAX1(A, B, C, YC, YS);
input A;
input B;
input C;
output YC;
output YS;
endmodule
