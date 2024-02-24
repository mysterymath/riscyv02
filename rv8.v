module rf(clk, r_num, w_num, w_en, r, w);
input clk;
input [2:0] r_num;
input [2:0] w_num;
input [15:0] r;
input [15:0] w;

reg [15:0] regs [2:0];

assign r = r_num == 3'b0 ? 15'b0 : regs[r_num];

always @(posedge clock) begin
  if (w_en) begin
    regs[w_num] <= w;
  end
end

endmodule
