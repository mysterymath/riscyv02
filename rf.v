module rf(clk, r1_num, r2_num, w_num, w, r1, r2);
input clk;
input [2:0] r1_num;
input [2:0] r2_num;
input [2:0] w_num;
input [15:0] w;
output [15:0] r1;
output [15:0] r2;

wire r_w_en[6:0];
genvar i;
generate
  for(i = 0; i < 7; i++)
    assign r_w_en[i] = w_num == i+1;
endgenerate

wire [15:0] r_r[6:0];
generate
  for(i = 0; i < 7; i++)
    register r_i(clk, r_w_en[i], r_r[i], w);
endgenerate

assign r1 = r1_num == 3'b0 ? 16'b0 : r_r[r1_num-1];
assign r2 = r2_num == 3'b0 ? 16'b0 : r_r[r2_num-1];

endmodule

module register(clk, w_en, r, w);
input clk;
input w_en;
input [15:0] w;
output reg [15:0] r;

always @(posedge clk)
  if (w_en)
    r <= w;

endmodule
