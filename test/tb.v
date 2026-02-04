/*
 * Testbench for tt_um_riscyv02.
 *
 * Models the external bus environment: a demux address register feeding a
 * 64KB async SRAM.
 *
 * Bus timing (one CPU cycle = one clk period):
 *
 *   posedge clk — mux_sel is still 0 (address phase):
 *     addr register captures AB from {uio_out, uo_out}.
 *     SRAM read output (uio_in) settles to ram[addr].
 *
 *   negedge clk — mux_sel is still 1 (data phase):
 *     CPU reads uio_in (for fetches and loads).
 *     Writes are captured: if RWB==0 (uo_out[0]), ram[addr] <= uio_out.
 *
 * Write capture and reset:
 *
 *   The write capture is gated on rst_n.  During reset, the mux_sel
 *   circuit is held at 0 (address phase), so uo_out carries AB[7:0]
 *   rather than {6'b0, SYNC, RWB}.  Without the gate, any address with
 *   bit 0 == 0 would be misinterpreted as RWB==0 (write), corrupting
 *   RAM contents.  In real hardware, the SRAM's write-enable signal
 *   would similarly be deasserted during reset.
 */

`default_nettype none
`timescale 1ns / 1ps

module tb ();

  initial begin
    $dumpfile("tb.fst");
    $dumpvars(0, user_project);
    $dumpvars(0, clk);
    $dumpvars(0, rst_n);
    $dumpvars(0, ena);
    $dumpvars(0, uio_in);
    $dumpvars(0, uo_out);
    $dumpvars(0, uio_out);
    $dumpvars(0, uio_oe);
    $dumpvars(0, addr);
    #1;
  end

  // Clock, reset, and enable are driven by cocotb.
  reg       clk;
  reg       rst_n;
  reg       ena;
  reg [7:0] ui_in;

  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;

  // SRAM read data: continuous from registered address.
  wire [7:0] uio_in = ram[addr];

  tt_um_riscyv02 user_project (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (ena),
      .clk    (clk),
      .rst_n  (rst_n)
  );

  // 64KB RAM — zero-initialized.  Program contents are written by cocotb
  // before reset, so the `initial` here is equivalent to flash being
  // blank at manufacturing.
  reg [7:0] ram [0:65535];
  integer i;
  initial for (i = 0; i < 65536; i = i + 1) ram[i] = 8'h00;

  // -----------------------------------------------------------------------
  // Address register: models the demux's posedge-triggered address capture.
  //
  // Resets to 0x0000, matching the real demux and the CPU's reset PC.
  // -----------------------------------------------------------------------
  reg [15:0] addr;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n)
      addr <= 16'h0000;
    else
      addr <= {uio_out, uo_out};
  end

  // -----------------------------------------------------------------------
  // Write capture: at negedge clk (data phase).
  //
  // At negedge, mux_sel is still 1, so uo_out[0] = RWB.  RWB=0 means
  // the CPU is writing.  Gated on rst_n because during reset mux_sel is
  // stuck at 0 (address phase) and uo_out carries address bits, not RWB.
  // -----------------------------------------------------------------------
  always @(negedge clk) begin
    if (rst_n && !uo_out[0])
      ram[addr] <= uio_out;
  end

endmodule
