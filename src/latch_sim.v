// Behavioral simulation models for IHP SG13G2 latch cells.
// RTL simulation only; synthesis uses real PDK cells.

module sg13g2_dlhrq_1 (Q, D, RESET_B, GATE);
  output reg Q;
  input D, RESET_B, GATE;
  always @(*)
    if (!RESET_B) Q = 1'b0;
    else if (GATE) Q = D;
endmodule

module sg13g2_dllrq_1 (Q, D, RESET_B, GATE_N);
  output reg Q;
  input D, RESET_B, GATE_N;
  always @(*)
    if (!RESET_B) Q = 1'b0;
    else if (!GATE_N) Q = D;
endmodule
