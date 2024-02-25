module barrel_shift(i, left, amt, o);
input [15:0] i;
input left;
input [2:0] amt;
output [15:0] o;

wire [23:0] tmp0;
// The purpose of the wide output is to capture the shifted out bits as
// a carry; the rest of the bits are irrelevant. 
assign tmp0 = left ? {8'bxxxxxxxx, i} : {i, 8'bxxxxxxxx};
wire [23:0] tmp1;
assign tmp1 = left ? (amt[2] ? tmp0 << 4 : tmp0) : (amt[2] ? tmp0 >> 4 : tmp0);
wire [23:0] tmp2;
assign tmp2 = left ? (amt[1] ? tmp1 << 2 : tmp1) : (amt[1] ? tmp1 >> 2 : tmp1);
wire [23:0] tmp3;
assign tmp3 = left ? (amt[0] ? tmp2 << 1 : tmp2) : (amt[0] ? tmp2 >> 1 : tmp2);

assign o = left ? tmp3[23:8] : tmp3[15:0];

endmodule
