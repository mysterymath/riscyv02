module mem(
  clk,
  active,
  addr,
  i_produce, i_op, i_r_num, i_addr,
  accept,
  rf_r1_num, rf_w_num, rf_w_en, rf_w,
);

// Control input
input clk;

// Control output
output reg active;

// Data output
output reg [15:0] addr;

// Pipeline input
input i_produce;
input [2:0] i_op;
input [2:0] i_r_num;
input [15:0] i_addr;

// Pipeline feedback
output accept;

// Register file interface
output [2:0] rf_r1_num;
output [2:0] rf_w_num;
output reg rf_w_en;
output reg [15:0] rf_w;

parameter LB  = 3'b111;
parameter LBU = 3'b000;
parameter LW  = 3'b001;
parameter SB  = 3'b010;
parameter SW  = 3'b011;

// Captured pipeline inputs
reg [2:0] op;
reg [2:0] r_num;

reg cyc;
reg [7:0] prev_data;

assign rf_r1_num = r_num;
assign rf_w_num = r_num;

always @* begin
  case (op)
    LB, LBU: rf_w_en = 1;
    LW: rf_w_en = cyc;
    SB, SW: rf_w_en = 0;
  endcase

  case (op)
    LB: rf_w = {{8{data[7]}}, data};
    LBU: rf_w = {8'b0, data};
    LW: rf_w = {data, prev_data};
  endcase
end

always @(negedge clk) begin
  if (!active && i_produce) begin
    op <= i_op;
    r_num <= i_r_num;
    addr <= i_addr;
    cyc <= 0;
    active <= 1;
  end else if (active) begin
    cyc <= !cyc;
    addr <= addr + 1;
    prev_data <= data;
    if (cyc || op == LB || op == LBU || op == SB)
      active <= 0;
  end
end

endmodule
