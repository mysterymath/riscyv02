module fetch(
  clk, n_reset, cyc, data,
  addr,
  inst, pc_val,
  execute_jump,
  pc_r, pc_r_next,
  pc_w);

input clk;
input n_reset;
input cyc;
input [7:0] data;
output [15:0] addr;

output reg [15:0] inst;
output reg [15:1] pc_val;
input execute_jump;

input [15:1] pc_r;
input [15:1] pc_r_next;
output reg [15:1] pc_w;

parameter JAL    = 4'b0011;
parameter BZ     = 4'b0100;
parameter BNZ    = 4'b0101;
parameter JALR   = 6'b011100;

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
  // the second cycle of its fetch and during its execution.
  if (op == JALR[3:0] && inst_lo[7] && !data[0])
    pc_w = pc_r;
  else if (cyc && branch_predicted)
    pc_w = branch_target;
  else
    pc_w = pc_r_next;

  // Feed a NOP to decode on execute-stage jump; the fetch was invalid.
  inst = execute_jump ? 16'b0000000100000000 : {data, inst_lo};

  if (op == BZ || op == BNZ && !branch_predicted)
    pc_val = branch_target;
  else
    pc_val = pc_r_next;
end

always @(posedge clk)
  if (!n_reset) begin
    // Simulate having fetched a NOP on the previous cycle. Otherwise, it
    // might randomly initialize to JALR, which is treated specially.
    inst_lo <= 8'b0;
  end else begin
    if (!cyc) begin
      inst_lo <= data;
    end else if (execute_jump) begin
      // Simulate having fetched a NOP on the first cycle of an executed jump.
      inst_lo <= 8'b0;
    end
  end

endmodule
