module vector(
  cyc,
  valid,
  data,
  addr,
  pc,
  pc_w,
);

// Control input
input cyc;

// Control output
output valid;

// Data input
input [7:0] data;

// Data output
output [15:0] addr;

// PC interface
input [15:1] pc;
output [15:1] pc_w;

reg [7:1] vec_lo;

assign addr = {pc, cyc};
assign pc_w = {data, vec_lo};
assign valid = cyc;

always @(negedge clk) begin
  vec_lo <= data[7:1];

endmodule
