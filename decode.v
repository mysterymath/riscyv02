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

reg [2:0] rs2_num;

parameter [2:0] LUI   = 3'b000
parameter [2:0] AUIPC = 3'b001
parameter [2:0] JAL   = 3'b010
parameter [4:0] ADDI  = 5'b00011
parameter [4:0] SLTI  = 5'b00100
parameter [4:0] SLTIU = 5'b00101
parameter [4:0] ANDI  = 5'b00110
parameter [4:0] ORI   = 5'b00111
parameter [4:0] XORI  = 5'b01011
parameter [4:0] SLI   = 5'b01100
parameter [4:0] SRI   = 5'b01101
parameter [4:0] JALR  = 5'b01110
parameter [4:0] BEQ   = 5'b01111
parameter [4:0] BNE   = 5'b10011
parameter [4:0] BLT   = 5'b10100
parameter [4:0] BLTU  = 5'b10101
parameter [4:0] BGE   = 5'b10110
parameter [4:0] BGEU  = 5'b10111
parameter [4:0] LB    = 5'b11011
parameter [4:0] LBU   = 5'b11100
parameter [4:0] LW    = 5'b11101
parameter [4:0] SB    = 5'b11110
parameter [4:0] SW    = 5'b11111
parameter [6:0] ADD   = 7'b0100011
parameter [6:0] SLT   = 7'b0100100
parameter [6:0] SLTU  = 7'b0100101
parameter [6:0] AND   = 7'b0100110
parameter [6:0] OR    = 7'b0100111
parameter [6:0] XOR   = 7'b0101011
parameter [6:0] SLL   = 7'b0101100
parameter [6:0] SRL   = 7'b0101101
parameter [6:0] SUB   = 7'b0101110
parameter [6:0] SRA   = 7'b0101111

always @*
  if (!cyc)
    rf_r_num <= inst[10:8]
  else
    rf_r_num <= 0;

always @(posedge clk) begin
  if (!cyc) begin
    op <= {inst[15:14], inst[7:6], inst[2:0]};
    case op([2:0])
      LUI, ADDI, AUIPC
    endcase
    rs2_num <= inst[13:11];
    rs1_val <= rf_r;
    case op([2:0])
      // U-type
      LUI, AUIPC: begin
        rs2_imm_val <= {inst[15:5], 5'b0};
        rd_num <= {1'b0, inst[4:3]};
      end
      // J-type
      JAL: begin
        rs2_imm_val <= {{5{inst[15]}}, inst[12:5], inst[13], inst[14], 1'b0};
        rd_num <= {1'b0, inst[4:3]};
      end
      default: begin
        rd_num <= inst[5:3];
        case (op[4:0])
          // I-type
          ADDI, SLTI, SLTIU, ANDI, ORI, XORI, SLI, SRI, JALR:
            rs2_imm_val <= {{12{inst[15]}}, inst[14:11]};
          // S-type
          SB, SW:
            rs2_imm_val <= {{12{inst[15]}}, inst[14], inst[5:3]};
          // B-type
          BEQ, BNE, BLT, BLTU, BGE, BGEU:
            rs2_imm_val <= {{12{inst[15]}}, inst[3], inst[14], inst[5:4], 1'b0};
        endcase
      end
      endcase
  end else begin
  end
end

endmodule;
