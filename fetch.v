module fetch(
  clk, cyc, data, vector,
  addr,
  inst, pc_val,
  stall,
  pc_r, pc_r_next,
  pc_w, brk);

input clk;
input cyc;
input [7:0] data;
input vector;
output [15:0] addr;

output reg [15:0] inst;
output reg [15:1] pc_val;
input stall;

input [15:1] pc_r;
input [15:1] pc_r_next;
output reg [15:1] pc_w;
output reg brk;

parameter J      = 4'b0011;
parameter JAL    = 4'b0100;
parameter BZ     = 4'b0101;
parameter BNZ    = 4'b0110;
parameter SYS    = 6'b000000;
parameter JR     = 6'b011101;
parameter JALR   = 6'b101101;

parameter SYS_BRK  = 3'b000;

reg [7:0] inst_lo;

assign addr = {pc_r, 1'b0};

// Only valid on second cycle.
wire [15:1] branch_offset;
assign branch_offset = {{6{data[7]}}, data, inst_lo[7]};

wire [3:0] op;
assign op = inst_lo[3:0];

// Only valid on second cycle.
reg branch_predicted;
always @*
  case (op)
    J, JAL: branch_predicted = 1;
    BZ, BNZ: branch_predicted = branch_offset[15];
    default: branch_predicted = 0;
  endcase

// Only valid on second cycle.
wire [15:1] branch_target;
// yosys should make a 4-bit carry lookahead adder for us.
assign branch_target = pc_r + branch_offset;

wire op6;
assign op6 = {data[0], inst_lo[7], op};

wire op_sys;
assign op_sys = data[3:1];

always @* begin
  if (vector)
    pc_w = !cyc ? pc_r_next : {data, inst_lo};
  else if (!cyc)
    pc_w = pc_r_next;
  else if (op6 == JR || op6 == JALR) begin
    // We can't know the actual target until after execute, but fetching reset
    // is always safe.
    pc_w = 16'hfffc;
  end else if (branch_predicted)
    pc_w = branch_target;
  else
    pc_w = pc_r_next;

  // Feed a NOP to decode on a stall.
  inst = stall ? 16'b0000000100000000 : {data, inst_lo};

  if (op == BZ || op == BNZ && !branch_predicted)
    pc_val = branch_target;
  else
    pc_val = pc_r_next;

  brk = !vector && op6 == SYS && op_sys == SYS_BRK;
end

always @(negedge clk)
  if (!cyc)
    inst_lo <= data;

endmodule
