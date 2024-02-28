module execute(
  clk, cyc, inst, non_predicted_pc,
  alu_op,
  rf_r1_num, rf_r2_num);

input clk;
input cyc;
input [15:0] inst;
input [15:1] non_predicted_pc;

output reg [2:0] alu_op;

output reg rf_r1_num;
output reg rf_r2_num;

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

parameter [2:0] ALU_ADD = 3'd0;
parameter [2:0] ALU_SUB = 3'd1;
parameter [2:0] ALU_AND = 3'd2;
parameter [2:0] ALU_OR = 3'd3;
parameter [2:0] ALU_XOR = 3'd4;
parameter [2:0] ALU_ROL = 3'd5;
parameter [2:0] ALU_ROR = 3'd6;

wire [4:0] op;
assign op = inst[4:0];

reg [2:0] rd;

always @* begin
  case (op)
    MOVI, ADDI, ANDI, ORI, XORI, SLI, SRI, JALR, SLTI, SLTIU, LUI,
      AUIPC, BZ, BNZ, JAL, INT:
      rf_r1_num = inst[7:5];
    default:
      rf_r1_num = inst[10:8];
  endcase

  case (op)
    SB, SW:
      rf_r1_num = inst[7:5];
    default:
      rf_r1_num = inst[13:11];
  endcase

  case (op)
    BZ, BNZ, INT, SB, SW:
      rd = 3'b0;
    JALR:
      rd = 3'b1; // link register
    default:
      rd = rf_r1_num;
  endcase

  case (op)
    SLTI, SLTIU, SUB, SLT, SLTU: alu_op = ALU_SUB;
    ANDI, AND: alu_op = ALU_AND;
    ORI, OR: alu_op = ALU_OR;
    XORI, XOR: alu_op = ALU_XOR;
    SLI, SLL: alu_op = ALU_ROL;
    SRL, SRA: alu_op = ALU_ROR;
    default: alu_op = ALU_ADD;
  endcase
end

endmodule
