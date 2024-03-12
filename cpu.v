module cpu(
  clk,
  n_reset,
  n_irq,
  n_nmi,
  addr,
  data_i,
  data_o
);
input clk;
input n_reset;
input n_irq;
input n_nmi;
output reg [15:0] addr;
input [7:0] data_i;
output reg [7:0] data_o;

reg cyc;

reg n_nmi_prev;
reg n_nmi_cur;
reg nmi_p;
reg irq_p;
reg pie;
reg ie;

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

wire [15:0] execute_addr;
wire [15:1] execute_pc_w;
execute execute(
  clk, n_reset, cyc, data_i,
  fetch_inst, fetch_pc_val,
  execute_jump, execute_load_store, execute_addr, data_o, execute_pc_w);

reg [15:1] epc;

always @* begin
  irq_p = !n_reset ? 0 : (!n_irq && ie);
  if (!n_reset)
    pc_w = 16'hfffc;
  else if (nmi_p && cyc)
    pc_w = 16'hfffa;
  else if (irq_p && cyc)
    pc_w = 16'hfffe;
  else
    pc_w = (execute_jump && !vector) ? execute_pc_w : fetch_pc_w;
  addr = execute_load_store ? execute_addr : fetch_addr;
end

always @(negedge clk) begin
  if (!n_reset) begin
    // What happens before reset shouldn't affect what happens after, so
    // disallow a NMI.
    n_nmi_prev <= 0;
    cyc <= 0;
    vector <= 1;
    ie <= 0;
  end else begin
    cyc <= !cyc;
    n_nmi_prev <= n_nmi_cur;
    n_nmi_cur <= n_nmi;
    if (n_nmi_prev && !n_nmi_cur)
      nmi_p <= 1;
    if (cyc) begin
      if ((nmi_p || irq_p) && !execute_load_store) begin
        vector <= 1;
        // Following RISC-V, our NMIs aren't intended to be recoverable, so
        // this epc is informational, and it may clobber an existing IRQ epc.
        epc <= pc_r;
        pie <= ie;
        ie <= 0;
      end else if (vector) begin
        if (execute_jump)
          epc <= execute_pc_w;
        vector <= 0;
      end
      nmi_p <= 0;
    end
  end
end

endmodule
