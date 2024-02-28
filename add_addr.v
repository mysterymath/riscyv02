module offset_pc(i, offset, o);
input [15:1] i;
input [8:1] offset;
output [15:1] o;

// Sign-extend offset. Doing this here gives an accurate picture of the
// effects of knowing the high bits are equal.
wire [15:1] soffset;
assign soffset = {{9{offset[8]}}, offset[7:1]};

// Yosys should generate a carry-lookahead adder.
assign o = i + soffset;

endmodule;

module cla_adder(a, b, c, p, g);
input a;
input b;
input c;
output p;
output g;
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
