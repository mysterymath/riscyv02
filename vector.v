module vector(
  trigger,
  active,
  data,
  addr,
  pc,
  pc_w,
);

// Control input
input trigger;

// Control output
output reg active;

// Data input
input [7:0] data;

// Data output
output [15:0] addr;

// PC interface
input [15:1] pc;
output [15:1] pc_w;

reg cyc;
reg [7:1] vec_lo;

assign addr = {pc, cyc};
assign pc_w = {data, vec_lo};

always @(negedge clk) begin
  if (trigger) begin
    cyc <= 0;
    active <= 1;
  else if (active) begin
    vec_lo <= data[7:1];
    cyc <= !cyc;
    if (cyc)
      active <= 0;
  end
endmodule
