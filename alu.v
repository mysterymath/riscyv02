module alu(op, l, r, o, c_i, c_o, v);
input [2:0] op;
input [7:0] l;
input [7:0] r;
output reg [7:0] o;
input c_i;
output reg c_o;
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

always @* begin
  case(op)
    ADD, SUB: o <= add_o;
    AND: o <= and_o;
    OR: o <= or_o;
    XOR: o <= xor_o;
    ROL: o <= (l << 1) | {7'b0, c_i};
    ROR: o <= (l >> 1) | {c_i, 7'b0};
    default: o <= 8'bxxxxxxxx;
  endcase

  case(op)
    ADD, SUB: c_o <= {5'b0, add_c_o[7]};
    ROL: c_o <= {5'b0, l[7]};
    ROR: c_o <= {5'b0, l[0]};
    default: c_o <= 6'b0;
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
