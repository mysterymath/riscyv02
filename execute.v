module execute(
  clk, cyc,
  fetch_inst, fetch_pc_val,
  jalr_executing, mispredict, mispredict_pc,
  alu_op, alu_l, alu_r, alu_c_i,
  alu_o, alu_c_o, alu_v,
  rf_r1_num, rf_r2_num, rf_w_num,
  rf_r1, rf_r2,
  rf_w);

input clk;
input cyc;
input [15:0] fetch_inst;
// The non-predicted PC value in case of a branch; otherwise, the PC value.
// Both are taken at the time of the last fetch tick; that is, the PC has
// already been incremented.
input [15:1] fetch_pc_val;
output jalr_executing;
output reg mispredict;
output [15:1] mispredict_pc;

reg [15:0] inst;
reg [15:1] pc_val;

output reg [2:0] alu_op;
output reg [7:0] alu_l;
output reg [7:0] alu_r;
output reg [6:0] alu_c_i;
input [7:0] alu_o;
input [6:0] alu_c_o;
input alu_v;

output reg rf_r1_num;
output reg rf_r2_num;
output reg rf_w_num;
input [15:0] rf_r1;
input [15:0] rf_r2;
output reg [15:0] rf_w;

// TODO: SYS, LB, LBU, LW, SB, SW

parameter SYS    = 5'b00000;
parameter LI     = 5'b00001;
parameter ADDI   = 5'b00010;
parameter ANDI   = 5'b00011;
parameter ORI    = 5'b00100;
parameter XORI   = 5'b00101;
parameter XORIA  = 5'b00110;
parameter SLI    = 5'b00111;
parameter SRI    = 5'b01000;
parameter JALR   = 5'b01001;
parameter SLTI   = 5'b01010;
parameter SLTIU  = 5'b01011;
parameter LUI    = 5'b01100;
parameter AUIPC  = 5'b01101;
parameter BZ     = 5'b01110;
parameter BNZ    = 5'b01111;
parameter JAL    = 5'b10000;

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

reg [15:0] imm;

reg [7:0] alu_o_prev_cyc;
reg [6:0] alu_c_o_prev_cyc;

reg sra;
reg branch_taken;
reg branch_predicted;

always @* begin
  op = inst[4:0];

  case (op)
    LI, LUI: rf_r1_num = 3'b0;
    LB, LBU, LW, SB, SW: rf_r1_num = 3'b100;
    ADD, SUB, AND, OR, XOR, SLL, SRA, SLT, SLTU: rf_r1_num = inst[10:8];
    default: rf_r1_num = inst[7:5];
  endcase

  case (op)
    SB, SW: rf_r2_num = inst[7:5];
    default: rf_r2_num = inst[13:11];
  endcase

  if (!cyc)
    rf_w_num = 3'b0;
  else
    case (op)
      SYS, BZ, BNZ, SB, SW:
        rf_w_num = 3'b0;
      XORIA, SLTI, SLTIU:
        rf_w_num = 3'b011;
      JALR:
        rf_w_num = 3'b1;
      default:
        rf_w_num = inst[7:5];
    endcase

  case (op)
    LUI, AUIPC:
      imm = {inst[15:8], 8'b0};
    default:
      imm = {{8{inst[15]}}, inst[15:8]};
  endcase

  case (op)
    SLTI, SLTIU, BZ, BNZ, SUB, SLT, SLTU: alu_op = ALU_SUB;
    ANDI, AND: alu_op = ALU_AND;
    ORI, OR: alu_op = ALU_OR;
    XORI, XORIA, XOR: alu_op = ALU_XOR;
    SLI, SLL: alu_op = ALU_ROL;
    SRL, SRA: alu_op = ALU_ROR;
    default: alu_op = ALU_ADD;
  endcase

  case(op)
    SRA: sra = 1;
    SRI: sra = imm[7];
    default: sra = 0;
  endcase

  case(op)
    SLI, SLL:
      if (imm[3])
        alu_l = !cyc ? 8'b0 : rf_r1[7:0];
      else
        alu_l = !cyc ? rf_r1[7:0] : rf_r1[15:8];
    SRI, SRL, SRA:
      if (imm[3])
        alu_l = !cyc ? (sra ? {8{rf_r1[15]}} : 8'b0) : rf_r1[15:8];
      else
        alu_l = !cyc ? rf_r1[15:8] : rf_r1[7:0];
    // Note that this makes AUIPC relative to the *next* instruction. Should
    // be fine.
    AUIPC:
      alu_l = !cyc ? {pc_val[7:1], 1'b0} : pc_val[15:8];
    default:
      alu_l = !cyc ? rf_r1[7:0] : rf_r1[15:8];
  endcase

  case(op)
    // Condition computed as SLTIU 1
    BZ, BNZ:
      alu_r = !cyc ? 8'b1 : 8'b0;
    ADD, SUB, AND, OR, XOR, SLT, SLTU:
      alu_r = !cyc ? rf_r2[7:0] : rf_r2[15:8];
    SLL, SRL, SRA:
      alu_r = rf_r2[7:0];
    SLI, SRI:
      alu_r = imm[7:0];
    default:
      alu_r = !cyc ? imm[7:0] : imm[15:8];
  endcase

  if (!cyc)
    alu_c_i = {6{sra ? alu_l[7] : 1'b0}};
  else
    alu_c_i = alu_c_o_prev_cyc;

  case(op)
    SLTI, SLT:
      rf_w = {15'b0, alu_o[7] ^ alu_v};
    SLTIU, SLTU:
      rf_w = {15'b0, !alu_c_o[0]};
    JAL, JALR:
      rf_w = {pc_val, 1'b0};
    default:
      rf_w = {alu_o, alu_o_prev_cyc};
  endcase

  jalr_executing = op == JALR;
  mispredict_pc = pc_val;

  case (op)
    // alu_c_o <=> rs1 >= 1u <=> rs1
    BZ: branch_taken = alu_c_o[0];
    BNZ: branch_taken = !alu_c_o[0];
    default: branch_taken = 0;
  endcase
  case (op)
    BZ, BNZ: branch_predicted = inst[15];
    default: branch_predicted = 0;
  endcase
  mispredict = branch_predicted != branch_taken;
end

always @(posedge clk) begin
  if (cyc) begin
    inst <= fetch_inst;
    pc_val <= fetch_pc_val;
  end
  alu_o_prev_cyc <= alu_o;
  alu_c_o_prev_cyc <= alu_c_o;
end

endmodule

(* blackbox *)
module HAX1(A, B, YC, YS);
input A;
input B;
output YC;
output YS;
endmodule
