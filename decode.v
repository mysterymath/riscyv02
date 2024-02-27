module decode(clk, cyc, inst, op, rd_num, rs1_val, rs2_imm_val, freeze, branch, branch_target, rf_r_num, rf_r, alu_op, alu_l, alu_r, alu_c_in);

// Logical interface
input clk;
input cyc;
input [15:0] inst;
output reg [6:0] op;
output reg [2:0] rd_num;
output reg [15:0] rs1_val;
output reg [15:0] rs2_imm_val;

// Interface to freeze processor
output freeze;

// Interface to report branch.
output branch;
output reg [15:1] branch_target;

// Interface to register file
output reg [2:0] rf_r_num;
input [15:0] rf_r;

// Interface to use ALU while execute frozen
output reg [2:0] alu_op;
output reg [7:0] alu_l;
output reg [7:0] alu_r;
output reg alu_c_in;

// State between decode cycles.
reg [2:0] rs2_num;
reg have_branch_target;
reg alu_c_out;

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

always @*
  // rs1
  if (!cyc)
    case (inst[4:0])
      TRAP, MOVI, ADDI, ANDI, ORI, XORI, SLI, SRI, JALR, SLTI, SLTIU, LUI,
        AUIPC, BZ, BNZ, JAL, INT, LB, LBU, LW, SB, SW:
        rf_r_num <= inst[7:5];
      default:
        rf_r_num <= inst[10:8];
    endcase
  else
    rf_r_num <= rs2_num;

// Trick from RISC-V to keep most bits aligned with immediates.
wire [15:1] branch_offset;
assign branch_offset = {{8{inst[15]}}, inst[8], inst[14:9]};

// Backwards brnaches and unconditional jumps are predicted.
wire branch_predicted;
assign branch_predicted = inst[4:0] == JAL || inst[4:0] == JALR || branch_offset[15];

always @(posedge clk) begin
  // Value on first cycle is garbage from inst in previous second cycle, but
  // no matter.
  rs2_num <= inst[13:11];
  // TODO: TRAP
  if (!cyc) begin
    op <= inst[4:0];

    case (inst[4:0])
      TRAP, BZ, BNZ, INT, SB, SW: rd_num <= 3'b0;
      JALR: rd_num <= 3'b1;
      default: rd_num <= inst[7:5];
    endcase

    rs1_val <= rf_r;

    case (inst[4:0])
      TRAP, MOVI, ADDI, ANDI, ORI, XORI, SLI, SRI, JALR, SLTI, SLTIU, INT:
        rs2_imm_val <= {{9{inst[15]}}, inst[14:8]};
      LUI, AUIPC:
        rs2_imm_val <= {inst[15:8], 8'b0};
      BZ, BNZ, JAL: begin
        rs2_imm_val <= pc_offset;
      end
      LB, LBU, LW, SB, SW:
        rs2_imm_val <= {{12{inst[15]}}, inst[14:11]};
    endcase

    if (branch_predicted) begin
    end

  end else begin
    case (op)
      ADD, SUB, AND, OR, XOR, SLL, SRL, SRA, SLT, SLTU:
        rs2_imm_val <= rf_r;
    endcase
  end
end

endmodule
