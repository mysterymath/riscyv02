module test_reset;

reg clk;
reg n_reset;
reg n_irq;
reg n_nmi;
wire [15:0] addr;
reg [7:0] data_i;
wire [7:0] data_o;
cpu cpu(clk, n_reset, n_irq, n_nmi, addr, data_i, data_o);

initial begin
  clk = 1;
  n_reset = 0;
  n_irq = 1;
  n_nmi = 1;
  data_i = 0;

  #1;
  clk = 0;
  $display(addr);
  assert(addr == 16'hfffc);
end
endmodule
