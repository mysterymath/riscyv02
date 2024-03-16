module fetch(
  clk, trigger, flush,
  busy,
  data,
  addr,
  valid, inst, pc_val,
  next_busy,
  epc,
  pc_r, pc_r_next,
  pc_w);

// Control input
input clk;
input trigger;
input flush;

// Control output
output busy;

// Data input
input [7:0] data;

// Data output
output [15:0] addr;

// Pipeline outputs
output valid;
output [15:0] inst;
output reg [15:1] pc_val;

// Pipeline feedback
input next_busy;

// CSR interface
input [15:1] epc;

// PC Interface
input [15:1] pc_r;
input [15:1] pc_r_next;
output reg [15:1] pc_w;

reg cyc;

parameter J      = 4'b0011;
parameter JAL    = 4'b0100;
parameter BZ     = 4'b0101;
parameter BNZ    = 4'b0110;
parameter SYS    = 6'b000000;
parameter JR     = 6'b011101;
parameter JALR   = 6'b101101;

parameter SYS_BRK  = 3'b000;
parameter SYS_RETI = 3'b001;

reg [7:0] inst_lo;

assign addr = {pc_r, cyc};

wire op6;
assign op6 = {data[0], inst_lo[7], op};

wire [15:1] branch_offset;
assign branch_offset = {{6{data[7]}}, data, inst_lo[7]};

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
  if (!busy || !cyc || flush || op6 == JR || op6 == JALR)
    pc_w = pc_r;
  else if (branch_predicted)
    pc_w = branch_target;
  else if (op6 == SYS && op_sys == SYS_RETI)
    pc_w = epc;
  else
    pc_w = pc_r_next;

  if (op == BZ || op == BNZ && !branch_predicted)
    pc_val = branch_target;
  else
    pc_val = pc_r_next;
end

always @(negedge clk)
  if (!busy && !next_busy && trigger) begin
    cyc <= 0;
    busy <= 1;
  end else if (busy) begin
    if (cyc || flush)
      busy <= 0;
    else
      inst_lo <= data;
  end
end

endmodule
