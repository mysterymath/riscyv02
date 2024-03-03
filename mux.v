module mux(a, b, s, y);

parameter W = 1;

input [W-1:0] a;
input [W-1:0] b;
input s;
output [W-1:0] y;

MUX2X1 muxes[W-1:0](a, b, s, y);
endmodule

(* blackbox *)
module MUX2X1(A, B, S, Y);
input A;
input B;
input S;
output Y;
endmodule
