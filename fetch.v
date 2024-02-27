module fetch(clk, cyc, data, pc_r, addr, inst);

input clk;
input cyc;
input [7:0] data;
input [15:1] pc_r;
output [15:0] addr;
output reg [15:0] inst;

assign addr = {pc_r, 1'b0};

// Note that a different decode is always in progress, so we can't simply
// forward data to the high byte of its address. We have to store it for the
// *next* decode.

always @(posedge clk)
  if (!cyc)
    inst[7:0] <= data;
  else
    inst[15:8] <= data;

endmodule
