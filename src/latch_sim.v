// Behavioral simulation models for IHP SG13G2 cells.
// RTL simulation only; synthesis uses real PDK cells.

module sg13g2_dlhrq_1 (Q, D, RESET_B, GATE);
  output reg Q;
  input D, RESET_B, GATE;
  always @(*)
    if (!RESET_B) Q = 1'b0;
    else if (GATE) Q = D;
endmodule

// Latch-based clock gating cell (behavioral model for simulation)
module sg13g2_lgcp_1 (GCLK, GATE, CLK);
  output GCLK;
  input GATE, CLK;
  reg gate_latched;
  always @(CLK or GATE)
    if (!CLK) gate_latched <= GATE;  // Latch on low phase (transparent-low)
  assign GCLK = CLK & gate_latched;
endmodule
