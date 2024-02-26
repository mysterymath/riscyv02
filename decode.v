`include "rf.v"

module decode(clk, cyc, inst, op, rd_num, rs1_val, rs2_imm_val, rf_r_num, rf_r);

// Logical interface
input clk;
input cyc;
input [15:0] inst;
output reg [6:0] op;
output reg [2:0] rd_num;
output reg [15:0] rs1_val;
output reg [15:0] rs2_imm_val;

// Interface to register file
output reg [2:0] rf_r_num;
input [15:0] rf_r;

parameter [2:0] LUI   = 3'b000
parameter [2:0] AUIPC = 3'b001
parameter [2:0] JAL   = 3'b010

always @*
  if (!cyc)
    rf_r_num <= inst[10:8]
  else
    rf_r_num <= 0;

always @(posedge clk) begin
  if (!cyc) begin
    op <= {inst[12:11], inst[7:6], inst[2:0]};
    rd_num <= inst[5:3];
    rs1_val <= rf_r;
  end else begin
  end
end

endmodule;
