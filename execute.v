module execute(
  clk, cyc, inst, non_predicted_pc);

input clk;
input cyc;
input [15:0] inst;
input [15:1] non_predicted_pc;

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
