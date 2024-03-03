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
INVX1 inv_clk(clk, w_master_clk);
LATCH w_master[15:0](w_master_clk, w, w_master_q);

wire r_clk;
INVX1 inv_master_clk(w_master_clk, r_clk);
wire [15:0] r_r[7:0];
generate
  for(i = 0; i < 8; i++)
    LATCH r_i[15:0](r_clk && r_w_en[i], w_master_q, r_r[i]);
endgenerate

wire [15:0] r1_0_r[3:0];
wire [15:0] r1_1_r[1:0];
wire [15:0] r1_2_r;
generate
  for (i = 0; i < 4; i++)
    mux #(16) r1_0(r_r[i+1], r_r[i], r1_num[0], r1_0_r[i]);
  for (i = 0; i < 2; i++)
    mux #(16) r1_1(r1_0_r[i+1], r1_0_r[i], r1_num[1], r1_1_r[i]);
  mux #(16) r1_2(r1_1_r[1], r1_1_r[0], r1_num[2], r1_2_r);
endgenerate;
assign r1 = ~r1_2_r;

wire [15:0] r2_0_r[3:0];
wire [15:0] r2_1_r[1:0];
wire [15:0] r2_2_r;
generate
  for (i = 0; i < 4; i++)
    mux #(16) r2_0(r_r[i+1], r_r[i], r2_num[0], r2_0_r[i]);
  for (i = 0; i < 2; i++)
    mux #(16) r2_1(r2_0_r[i+1], r2_0_r[i], r2_num[1], r2_1_r[i]);
  mux #(16) r2_2(r2_1_r[1], r2_1_r[0], r2_num[2], r1_2_r);
endgenerate;
assign r2 = ~r2_2_r;

endmodule

(* blackbox *)
module LATCH(CLK, D, Q);
input CLK;
input D;
output Q;
endmodule

(* blackbox *)
module INVX1(A, Y);
input A;
output Y;
endmodule
