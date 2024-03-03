module rf(clk, r1_num, r2_num, w_num, w_en, w, r1, r2);
input clk;
input [2:0] r1_num;
input [2:0] r2_num;
input [2:0] w_num;
input w_en;
input [15:0] w;
output [15:0] r1;
output [15:0] r2;

wire r_w_en[7:0];
genvar i;
generate
  for(i = 0; i < 8; i++)
    assign r_w_en[i] = w_en && w_num == i;
endgenerate

wire w_master_clk;
wire [15:0] w_master_q;
INVX2 inv_clk(clk, w_master_clk);
LATCH w_master[15:0](w_master_clk, w, w_master_q);

wire r_clk;
INVX2 inv_master_clk(w_master_clk, r_clk);
wire [15:0] r_r[7:0];
generate
  for(i = 0; i < 8; i++)
    LATCH r_i[15:0](r_clk && r_w_en[i], w_master_q, r_r[i]);
endgenerate

assign r1 = r_r[r1_num];
assign r2 = r_r[r2_num];

endmodule

(* blackbox *)
module LATCH(CLK, D, Q);
input CLK;
input D;
output Q;
endmodule

(* blackbox *)
module INVX2(A, Y);
input A;
output Y;
endmodule
