module fetch(
  clk, cyc, data,
  addr,
  inst, pc_val,
  execute_jalr, execute_mispredict,
  pc_r, pc_r_next,
  pc_w);

input clk;
input cyc;
input [7:0] data;
output [15:0] addr;

output reg [15:0] inst;
output reg [15:1] pc_val;
input execute_jalr;
input execute_mispredict;

input [15:1] pc_r;
input [15:1] pc_r_next;
output reg [15:1] pc_w;

parameter JALR   = 5'b01001;
parameter BZ     = 5'b01110;
parameter BNZ    = 5'b01111;
parameter JAL    = 5'b10000;

reg [7:0] inst_lo;

assign addr = {pc_r, 1'b0};

// Only valid on second cycle.
wire [15:1] branch_offset;
assign branch_offset = {{8{data[7]}}, data[6:0]};

wire [4:0] op;
assign op = inst_lo[4:0];

// Only valid on second cycle.
reg branch_predicted;
always @*
  case (op)
    JAL: branch_predicted = 1;
    BZ, BNZ: branch_predicted = branch_offset[15];
    default: branch_predicted <= 0;
  endcase

// Only valid on second cycle.
wire [15:1] branch_target;
// yosys should make a 4-bit carry lookahead adder for us.
assign branch_target = pc_r + branch_offset;

always @* begin
  // Keep the PC from incrementing into the invalid region past the JALR on
  // the second cycle of its fetch and during its execution. On the first
  // cycle of the next fetch, the condition below is false, so the new opcode
  // is read, the PC is incremented, and the pipeline continues normally.
  if ((cyc && op == JALR) || execute_jalr)
    pc_w = pc_r;
  else if (cyc && branch_predicted)
    pc_w = branch_target;
  else
    pc_w = pc_r_next;

  // Feed a NOP (ADDI x0, 0) into decode.
  if (execute_jalr || execute_mispredict)
    inst = 16'b0000000000000010;
  else
    inst = {data, inst_lo};

  if (op == BZ || op == BNZ && !branch_predicted)
    pc_val = branch_target;
  else
    pc_val = pc_r_next;
end

always @(posedge clk)
  if (!cyc)
    inst_lo <= data;

endmodule
