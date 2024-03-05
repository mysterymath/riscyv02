module cpu(
  clk,
  n_reset,
  n_irq,
  addr,
  data_i,
  data_o
);
input clk;
input n_reset;
input n_irq;
output reg [15:0] addr;
input [7:0] data_i;
output reg [7:0] data_o;

reg cyc;

reg [15:1] pc_w;
wire [15:1] pc_r;
wire [15:1] pc_r_next;
pc pc(clk, pc_w, pc_r, pc_r_next);

reg vector;
wire [15:0] fetch_addr;
wire [15:0] fetch_inst;
wire [15:1] fetch_pc_val;
wire [15:1] fetch_pc_w;
wire execute_jump;
wire execute_load_store;
fetch fetch(
  clk, cyc, data_i, vector,
  fetch_addr,
  fetch_inst, fetch_pc_val,
  /*invalid=*/execute_jump || vector, /*freeze=*/execute_load_store,
  pc_r, pc_r_next,
  fetch_pc_w);

wire [2:0] alu_op;
wire [7:0] alu_l;
wire [7:0] alu_r;
wire [7:0] alu_o;
wire [6:0] alu_c_i;
wire [6:0] alu_c_o;
wire alu_v;
alu alu(alu_op, alu_l, alu_r, alu_o, alu_c_i, alu_c_o, alu_v);

wire clk;
wire [2:0] rf_r1_num;
wire [2:0] rf_r2_num;
wire [2:0] rf_w_num;
wire rf_w_en;
wire [15:0] rf_w;
wire [15:0] rf_r1;
wire [15:0] rf_r2;
rf rf(clk, rf_r1_num, rf_r2_num, rf_w_num, rf_w_en, rf_w, rf_r1, rf_r2);

wire [15:0] execute_addr;
wire [15:1] execute_pc_w;
execute execute(
  clk, n_reset, cyc, data_i,
  fetch_inst, fetch_pc_val,
  execute_jump, execute_load_store, execute_addr, data_o, execute_pc_w,
  alu_op, alu_l, alu_r, alu_c_i,
  alu_o, alu_c_o, alu_v,
  rf_r1_num, rf_r2_num, rf_w_num, rf_w_en,
  rf_r1, rf_r2,
  rf_w);

always @* begin
  if (!n_reset)
    pc_w = 16'hfffc;
  else if (!n_irq && cyc)
    pc_w = 16'hfffe;
  else
    pc_w = execute_jump ? execute_pc_w : fetch_pc_w;
  addr = execute_load_store ? execute_addr : fetch_addr;
end

always @(posedge clk) begin
  if (!n_reset) begin
    cyc <= 0;
    vector <= 1;
  end else begin
    cyc <= !cyc;
    if (cyc)
      vector <= !n_irq;
  end
end

endmodule
