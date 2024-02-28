module fetch(
  clk, cyc, data,
  addr, inst, non_predicted_pc,
  pc_r,
  pc_r_next,
  pc_w);

input clk;
input cyc;
input [7:0] data;

output [15:0] addr;
output reg [15:0] inst;
output reg [15:1] non_predicted_pc;

input [15:1] pc_r;
input [15:1] pc_r_next;
output [15:1] pc_w;

assign addr = {pc_r, 1'b0};

parameter TRAP  = 5'b00000;
parameter MOVI  = 5'b00001;
parameter ADDI  = 5'b00010;
parameter ANDI  = 5'b00011;
parameter ORI   = 5'b00100;
parameter XORI  = 5'b00101;
parameter SLI   = 5'b00110;
parameter SRI   = 5'b00111;
parameter JALR  = 5'b01000;
parameter SLTI  = 5'b01001;
parameter SLTIU = 5'b01010;
parameter LUI   = 5'b01011;
parameter AUIPC = 5'b01100;
parameter BZ    = 5'b01101;
parameter BNZ   = 5'b01110;
parameter JAL   = 5'b01111;
parameter INT   = 5'b10000;

parameter LB    = 5'b10001;
parameter LBU   = 5'b10010;
parameter LW    = 5'b10011;
parameter SB    = 5'b10100;
parameter SW    = 5'b10101;

parameter ADD   = 5'b10110;
parameter SUB   = 5'b10111;
parameter AND   = 5'b11000;
parameter OR    = 5'b11001;
parameter XOR   = 5'b11010;
parameter SLL   = 5'b11011;
parameter SRL   = 5'b11100;
parameter SRA   = 5'b11101;
parameter SLT   = 5'b11110;
parameter SLTU  = 5'b11111;


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

assign pc_w = (cyc && branch_predicted) ? branch_target : pc_r_next;

always @(posedge clk)
  if (!cyc)
    inst[7:0] <= data;
  else begin
    inst[15:8] <= data;
    if (branch_predicted)
      non_predicted_pc <= pc_r_next;
    else
      non_predicted_pc <= branch_target;
  end

endmodule
