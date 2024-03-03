module alu(op, l, r, o, c_i, c_o, v);
input [2:0] op;
input [7:0] l;
input [7:0] r;
output reg [7:0] o;
input [6:0] c_i;
output reg [6:0] c_o;
output reg v;

parameter [2:0] ADD = 3'd0;
parameter [2:0] SUB = 3'd1;
parameter [2:0] AND = 3'd2;
parameter [2:0] OR = 3'd3;
parameter [2:0] XOR = 3'd4;
parameter [2:0] ROL = 3'd5;
parameter [2:0] ROR = 3'd6;

wire [7:0] add_c_o;
wire [7:0] add_o;
wire [7:0] add_r;
wire add_c_i;

assign add_r = op[0] ? ~r : r;
assign add_c_i = op[0];

FAX1 low(l[0], add_r[0], add_c_i, add_c_o[0], add_o[0]);
genvar i;
generate
  for (i = 1; i < 8; i = i + 1)
    FAX1 add_i(l[i], add_r[i], add_c_o[i-1], add_c_o[i], add_o[i]);
endgenerate

wire [7:0] and_o;
assign and_o = l & r;

wire [7:0] or_o;
assign or_o = l | r;

wire [7:0] xor_o;
assign xor_o = l ^ r;

// The below is kind of circuitous, but it's the clearest way to produce
// minimum-width mux trees.

// Concatenate to produce the result rotated left by 7.
wire [14:0] rotl_7;
assign rotl_7 = {l, c_i};

// The amt to rotate right is 7-x, but that just works out to ~x. Accordingly,
// just swap the muxes direction.

wire [14:0] rotlr_1;
mux #(15) rotlr_1_mux(rotl_7, rotl_7 >> 1, r[0], rotlr_1);
wire [14:0] rotlr_2;
mux #(15) rotlr_2_mux(rotlr_1, rotlr_1 >> 2, r[1], rotlr_2);
wire [14:0] rotlr_4;
mux #(15) rotlr_4_mux(rotlr_2, rotlr_2 >> 4, r[2], rotlr_4);

wire [7:0] rotl_o;
assign rotl_o = rotlr_4[7:0];
wire [6:0] rotl_c_o;
assign rotl_c_o = rotlr_4[14:8];

// Concatenate to produce the result rotated right by 7.
wire [14:0] rotr_7;
assign rotr_7 = {c_i, l};

// The amt to rotate left is 7-x, but that just works out to ~x. Accordingly,
// just swap the muxes direction.

wire [14:0] rotrl_1;
mux #(15) rotrl_1_mux(rotr_7, rotr_7 << 1, r[0], rotrl_1);
wire [14:0] rotrl_2;
mux #(15) rotrl_2_mux(rotrl_1, rotrl_1 << 2, r[1], rotrl_2);
wire [14:0] rotrl_4;
mux #(15) rotrl_4_mux(rotrl_2, rotrl_2 << 4, r[2], rotrl_4);

wire [7:0] rotr_o;
assign rotr_o = rotrl_4[7:0];
wire [6:0] rotr_c_o;
assign rotr_c_o = rotrl_4[14:8];

wire [7:0] rot_o;
wire [6:0] rot_c_o;

mux #(8) rot_o_mux(rotl_o, rotr_o, op == ROL, rot_o);
mux #(7) rot_c_o_mux(rotl_c_o, rotr_c_o, op == ROL, rot_c_o);

always @* begin
  case(op)
    ADD, SUB: o <= add_o;
    AND: o <= and_o;
    OR: o <= or_o;
    XOR: o <= xor_o;
    ROL, ROR: o <= rot_o;
    default: o <= 8'bxxxxxxxx;
  endcase

  case(op)
    ADD, SUB: c_o <= {5'bxxxxx, add_c_o[7]};
    ROL, ROR: c_o <= rot_c_o;
    default: c_o <= 6'bxxxxxx;
  endcase

  // TODO
  v = 0;
end

endmodule

(* blackbox *)
module HAX1(A, B, YC, YS);
input A;
input B;
output YC;
output YS;
endmodule

(* blackbox *)
module FAX1(A, B, C, YC, YS);
input A;
input B;
input C;
output YC;
output YS;
endmodule
