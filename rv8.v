module rf(clk, r_num, w_num, w_en, w, r);
input clk;
input [2:0] r_num;
input [2:0] w_num;
input w_en;
input [15:0] w;

output reg [15:0] r;

reg [15:0] regs [6:0];

always @(posedge clk) begin
  if (w_en && w_num != 3'b0) begin
    regs[w_num-1] <= w;
    if (r_num == 3'b0)
      r <= 16'b0;
    else if (r_num == w_num)
      r <= w;
    else
      r <= regs[r_num-1];
  end else
    r <= r_num == 3'b0 ? 16'b0 : regs[r_num-1];
end

endmodule
