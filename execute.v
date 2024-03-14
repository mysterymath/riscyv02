module execute(
  clk, cyc, stall, data_i,
  fetch_inst, fetch_pc_val,
  jump, load_store, addr, data_o, cyc_reset,
  pie, ie, epc,
  pc_w, pie_w, ie_w, epc_w);

input clk;
input cyc;
input stall;
input [7:0] data_i;
input [15:0] fetch_inst;
// The non-predicted PC value in case of a branch; otherwise, the PC value.
// Both are taken at the time of the last fetch tick; that is, the PC has
// already been incremented.
input [15:1] fetch_pc_val;
output reg jump;
output reg load_store;
output reg [15:0] addr;
output reg [7:0] data_o;
output reg cyc_reset;

input pie;
input ie;
input [15:1] epc;
output reg [15:1] pc_w;
output reg pie_w;
output reg ie_w;
output reg [15:1] epc_w;

reg [15:0] inst;
reg [15:1] pc_val;

reg [2:0] alu_op;
reg [7:0] alu_l;
reg [7:0] alu_r;
reg [6:0] alu_c_i;
wire [7:0] alu_o;
wire [6:0] alu_c_o;
wire alu_v;
alu alu(alu_op, alu_l, alu_r, alu_o, alu_c_i, alu_c_o, alu_v);

reg [2:0] rf_r1_num;
reg [2:0] rf_r2_num;
reg [2:0] rf_w_num;
reg rf_w_en;
wire [15:0] rf_r1;
wire [15:0] rf_r2;
reg [15:0] rf_w;
rf rf(clk, rf_r1_num, rf_r2_num, rf_w_num, rf_w_en, rf_w, rf_r1, rf_r2);

// TODO: SYS

parameter LUI   = 8'b00000001;
parameter AUIPC = 8'b00000010;
parameter J     = 8'b00000011;
parameter JAL   = 8'b00000100;
parameter BZ    = 8'b00000101;
parameter BNZ   = 8'b00000110;
parameter LB    = 8'b00000111;
parameter LBU   = 8'b00001000;
parameter LW    = 8'b00001001;
parameter SB    = 8'b00001010;
parameter SW    = 8'b00001011;

parameter SYS   = 8'b00000000;
parameter LI    = 8'b00010000;
parameter ADDI  = 8'b00100000;
parameter ANDI  = 8'b00110000;
parameter ORI   = 8'b00001100;
parameter XORI  = 8'b00011100;
parameter XORIA = 8'b00101100;
parameter SLI   = 8'b00111100;
parameter SRI   = 8'b00001101;
parameter JR    = 8'b00011101;
parameter JALR  = 8'b00101101;
parameter SLTI  = 8'b00111101;
parameter SLTIU = 8'b00001110;

parameter ADD   = 8'b00001111;
parameter SUB   = 8'b00011111;
parameter AND   = 8'b00101111;
parameter OR    = 8'b00111111;
parameter XOR   = 8'b01001111;
parameter SLL   = 8'b01111111;
parameter SRL   = 8'b01101111;
parameter SRA   = 8'b01011111;
parameter SLT   = 8'b10101111;
parameter SLTU  = 8'b10111111;

parameter ALU_ADD = 3'd0;
parameter ALU_SUB = 3'd1;
parameter ALU_AND = 3'd2;
parameter ALU_OR = 3'd3;
parameter ALU_XOR = 3'd4;
parameter ALU_ROL = 3'd5;
parameter ALU_ROR = 3'd6;

parameter SYS_BRK  = 3'b000;
parameter SYS_RETI = 3'b001;
parameter SYS_CSRR = 3'b010;
parameter SYS_CSRW = 3'b011;
parameter SYS_SIE  = 3'b100;

reg [8:0] op;
wire op_sys;
assign op_sys = inst[11:9];

reg [15:0] imm;

reg [7:0] alu_o_prev_cyc;
reg [6:0] alu_c_o_prev_cyc;

reg sra;
reg branch_taken;
reg branch_predicted;

always @* begin
  case (inst[3:0])
    4'b0000, 4'b1100, 4'b1101, 4'b1110: op = {2'b0, inst[8:7], inst[3:0]};
    4'b1101: op = {inst[15:14], inst[8:7], inst[3:0]};
    default: op = {4'b0, inst[3:0]};
  endcase

  case (op)
    LB, LBU, LW, SB, SW: rf_r1_num = {1'b0, inst[8:7]};
    ADD, SUB, AND, OR, XOR, SLL, SRA, SLT, SLTU: rf_r1_num = inst[10:8];
    default: rf_r1_num = inst[7:5];
  endcase

  case (op)
    SB, SW: rf_r2_num = inst[7:5];
    default: rf_r2_num = inst[13:11];
  endcase

  case (op)
    LUI, AUIPC:
      imm = {inst[15:7], 7'b0};
    default:
      imm = {{7{inst[15]}}, inst[15:9]};
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
    LI, LUI:
      alu_l = 8'b0;
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

  case (op)
    SYS, BZ, BNZ, SB, SW, J, JR: rf_w_en = 1'b0;
    default: rf_w_en = !cyc;
  endcase

  case (op)
    XORIA, SLTI, SLTIU:
      rf_w_num = 3'd2;
    JALR:
      rf_w_num = 3'd1;
    default:
      rf_w_num = inst[7:5];
  endcase

  case(op)
    LB:
      rf_w = {{8{data_i[7]}}, data_i};
    LBU:
      rf_w = {8'b0, data_i};
    LW:
      rf_w = !cyc ? {8'bxxxxxxxx, data_i} : {rf_r1[15:8], data_i};
    SLTI, SLT:
      rf_w = {15'b0, alu_o[7] ^ alu_v};
    SLTIU, SLTU:
      rf_w = {15'b0, !alu_c_o[0]};
    JAL, JALR:
      rf_w = {pc_val, 1'b0};
    default:
      rf_w = {alu_o, alu_o_prev_cyc};
  endcase

  pc_w = op == JALR ? {alu_o, alu_o_prev_cyc} : pc_val;

  case (op)
    // alu_c_o <=> rs1 >= 1u <=> rs1
    BZ: branch_taken = alu_c_o[0];
    BNZ: branch_taken = !alu_c_o[0];
    JALR: branch_taken = 1;
    default: branch_taken = 0;
  endcase
  case (op)
    BZ, BNZ: branch_predicted = inst[15];
    default: branch_predicted = 0;
  endcase
  jump = cyc && branch_predicted != branch_taken;

  data_o = !cyc ? rf_r2[7:0] : rf_r2[15:8];

  if (op == SYS && op_sys == SYS_RETI) begin
    ie_w = pie_w;
    pie_w = 1;
  end else begin
    ie_w = ie;
    pie_w = pie;
  end
  epc_w = epc;

  case (op)
    LB, SB: cyc_reset = load_store;
    default: cyc_reset = 0;
  endcase
end

always @(negedge clk) begin
  if (stall) begin
    // NOP.
    inst <= 16'b0000000100000000;
    load_store <= 0;
  end else if (load_store) begin
    case (op)
      LB, SB: load_store <= 1'b0;
      default: load_store <= !cyc;
    endcase
  end else if (cyc) begin
    inst <= fetch_inst;
    pc_val <= fetch_pc_val;
    case (op)
      LB, LBU, LW, SB, SW: load_store <= 1'b1;
      default: load_store <= 0;
    endcase
  end
  alu_o_prev_cyc <= alu_o;
  alu_c_o_prev_cyc <= alu_c_o;
  addr <= load_store ? addr + 1 : {alu_o, alu_o_prev_cyc};
end

endmodule
