module alu(op, l, r, c_in, o, c_out);
input [2:0] op;
input [7:0] l;
input [7:0] r;
input c_in;
output reg [7:0] o;
output reg c_out;

parameter [2:0] ADD = 3'd0;
parameter [2:0] SUB = 3'd1;
parameter [2:0] AND = 3'd2;
parameter [2:0] OR = 3'd3;
parameter [2:0] XOR = 3'd4;
parameter [2:0] ROL = 3'd5;
parameter [2:0] ROR = 3'd6;

wire [7:0] add_c_out;
wire [7:0] add_out;
wire [7:0] add_r;
wire add_c_in;

assign add_r = op[0] ? ~r : r;
assign add_c_in = op[0];

FAX1 low(l[0], add_r[0], add_c_in, add_c_out[0], add_out[0]);
genvar i;
generate
  for (i = 1; i < 8; i = i + 1)
    FAX1 add_i(l[i], add_r[i], add_c_out[i-1], add_c_out[i], add_out[i]);
endgenerate

wire [7:0] and_out;
assign and_out = l & r;

wire [7:0] or_out;
assign or_out = l | r;

wire [7:0] xor_out;
assign xor_out = l ^ r;

always @* begin
  case(op)
    ADD, SUB: o <= add_out;
    AND: o <= and_out;
    OR: o <= or_out;
    XOR: o <= xor_out;
    ROL: o <= (l << 1) | {7'b0, c_in};
    ROR: o <= (l >> 1) | {c_in, 7'b0};
    default: o <= 8'bxxxxxxxx;
  endcase

  case(op)
    ADD, SUB: c_out <= add_c_out[7];
    ROL: c_out <= l[7];
    ROR: c_out <= l[0];
    default: c_out <= 0;
  endcase
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
