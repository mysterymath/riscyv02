module fetch(
  clk, cyc, data,
  addr, inst, non_predicted_pc,
  pc_r,
  pc_w,
  pc_w_en);

// TODO: Move the program counter in here.

input clk;
input cyc;
input [7:0] data;

output [15:0] addr;
output reg [15:0] inst;
output reg [15:1] non_predicted_pc;

input [15:1] pc_r;
output [15:1] pc_w;
output pc_w_en;

assign addr = {pc_r, 1'b0};

`include "opcodes.vi"

// Only valid on second cycle.
wire [15:1] branch_offset;
assign branch_offset = {{8{data[7]}}, data[6:0]};

// Only valid on second cycle.
reg branch_predicted;
always @*
  if (inst[4:0] == JAL)
    branch_predicted <= 1;
  else if (inst[4:0] == BZ || inst[4:0] == BNZ)
    branch_predicted <= branch_offset[15];
  else
    branch_predicted <= 0;

// Only valid on second cycle.
wire [15:1] branch_target;
// yosys should make a 4-bit carry lookahead adder for us.
assign branch_target = pc_r + branch_offset;

// Only valid on second cycle.
assign pc_w_en = branch_predicted && cyc;
assign pc_w = branch_target;

always @(posedge clk)
  if (!cyc)
    inst[7:0] <= data;
  else begin
    inst[15:8] <= data;
    // TODO: Bum the increment off of the program counter.
    if (branch_predicted)
      non_predicted_pc <= pc_r + 1;
    else
      non_predicted_pc <= branch_target;
  end

endmodule
