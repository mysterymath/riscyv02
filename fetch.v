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

output wire [15:0] inst;
output reg [15:1] pc_val;
input stall;

wire stall;

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

wire op6;
assign op6 = {data[0], inst_lo[7], op};

// In case of a JR or JALR, perform a jump back to the beginning of the
// instruction to avoid reading invalid memory.
wire [15:1] branch_offset;
assign branch_offset = (op6 == JR || op6 == JALR) ? -16'd1 : {{6{data[7]}}, data, inst_lo[7]};

wire [3:0] op;
assign op = inst_lo[3:0];

// Only valid on second cycle.
reg branch_predicted;
always @*
  case (op)
    J, JAL, JR, JALR: branch_predicted = 1;
    BZ, BNZ: branch_predicted = branch_offset[15];
    default: branch_predicted = 0;
  endcase

wire [15:1] branch_target;
// yosys should make a 4-bit carry lookahead adder for us.
assign branch_target = pc_r + branch_offset;

wire op_sys;
assign op_sys = data[3:1];

assign inst = {data, inst_lo};

always @* begin
  if (stall)
    pc_w = pc_r;
  else begin
    if (!cyc)
      pc_w = pc_r_next;
    else if (vector)
      pc_w = {data, inst_lo};
    else if (branch_predicted)
      pc_w = branch_target;
    else
      pc_w = pc_r_next;
  end

  if (op == BZ || op == BNZ && !branch_predicted)
    pc_val = branch_target;
  else
    pc_val = pc_r_next;

  brk = !vector && op6 == SYS && op_sys == SYS_BRK;
end

always @(negedge clk)
  if (!cyc && !stall)
    inst_lo <= data;

endmodule
