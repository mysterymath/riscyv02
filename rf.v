module rf(r_num, w_num, w_en, w, r);
input [2:0] r_num;
input [2:0] w_num;
input w_en;
input [15:0] w;
output [15:0] r;

wire r_w_en[6:0];
genvar i;
generate
  for(i = 0; i < 7; i++)
    assign r_w_en[i] = w_en && w_num == i+1;
endgenerate

wire [15:0] r_r[6:0];
generate
  for(i = 0; i < 7; i++)
    register r_i(r_w_en[i], r_r[i], w);
endgenerate

assign r = r_num == 3'b0 ? 16'b0 : r_r[r_num-1];

endmodule

module register(w_en, r, w);
input w_en;
input [15:0] w;
output [15:0] r;
LATCH latches [15:0]({16{w_en}}, w, r);
endmodule

(* blackbox *)
module LATCH(CLK, D, Q);
input CLK;
input D;
output Q;
endmodule
