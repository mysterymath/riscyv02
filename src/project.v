/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 *
 * RISCY-V02 "Byte Byte Jump" — Minimal Turing-Complete RISC-V Subset
 *
 * ISA: LW, SW, JR only (all other opcodes = NOP).
 * Architecture: 2-stage pipeline (Fetch / Execute) with 8-bit muxed bus.
 *
 * Bus protocol: identical to tt_um_arlet_6502 mux/demux protocol.
 *
 *   mux_sel=0 (address out):
 *     uo_out[7:0]  = AB[7:0]
 *     uio_out[7:0] = AB[15:8]   (uio_oe = 8'hFF, all output)
 *
 *   mux_sel=1 (data + status):
 *     uo_out[0]    = RWB
 *     uo_out[1]    = SYNC (instruction boundary indicator)
 *     uo_out[7:2]  = 0
 *     uio[7:0]     = D[7:0] bidirectional data bus
 *     uio_oe       = RWB ? 8'h00 : 8'hFF
 *
 * Control signals:
 *   ui_in[2]     = RDY (active-high ready input for wait states / single-step)
 *
 * Instruction encoding (16-bit):
 *   LW  rd, off(rs1):  [1000][rs1:3][off6:6][rd:3]
 *   SW  rs2, off(rs1): [1010][rs1:3][off6:6][rs2:3]
 *   JR  rs, off6:      [1011100][off6:6][rs:3]
 */

`default_nettype none

// =========================================================================
// Top module: mux_sel, bus arbitration, output muxes
// =========================================================================
module tt_um_riscyv02 (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);

  // -----------------------------------------------------------------------
  // RDY input and clock gating
  // -----------------------------------------------------------------------
  wire rdy = ui_in[2];

  // Gated clock for CPU logic — freezes when RDY=0
  wire cpu_clk;
  sg13g2_lgcp_1 u_cpu_icg (
    .CLK  (clk),
    .GATE (rdy),
    .GCLK (cpu_clk)
  );

  // -----------------------------------------------------------------------
  // Mux select: dual-edge register (identical to 6502 wrapper).
  // Runs on clk so protocol timing continues even when CPU is halted.
  // -----------------------------------------------------------------------
  wire mux_sel = q ^ q_d;

  reg q;
  always @(posedge clk or negedge rst_n)
    if (!rst_n)        q <= 1'b0;
    else if (!mux_sel) q <= ~q;

  reg q_d;
  always @(negedge clk or negedge rst_n)
    if (!rst_n)       q_d <= 1'b0;
    else if (mux_sel) q_d <= ~q_d;

  // -----------------------------------------------------------------------
  // Inter-module wires
  // -----------------------------------------------------------------------
  wire        ir_valid;
  wire [15:0] new_ir;
  wire [15:0] fetch_ab;

  wire        exec_busy;
  wire        exec_bus_active;
  wire        exec_ir_accept;
  wire [15:0] exec_ab;
  wire [7:0]  exec_dout;
  wire        exec_rwb;
  wire        redirect;
  wire [15:0] redirect_pc;

  // -----------------------------------------------------------------------
  // Submodule instances
  // -----------------------------------------------------------------------
  riscyv02_fetch u_fetch (
    .clk        (cpu_clk),
    .rst_n      (rst_n),
    .uio_in     (uio_in),
    .bus_free   (!exec_bus_active),
    .exec_busy  (exec_busy),
    .ir_accept  (exec_ir_accept),
    .redirect   (redirect),
    .redirect_pc(redirect_pc),
    .ir_valid   (ir_valid),
    .new_ir     (new_ir),
    .ab         (fetch_ab)
  );

  riscyv02_execute u_execute (
    .clk        (cpu_clk),
    .rst_n      (rst_n),
    .uio_in     (uio_in),
    .ir_valid   (ir_valid),
    .new_ir     (new_ir),
    .busy       (exec_busy),
    .bus_active (exec_bus_active),
    .ab         (exec_ab),
    .dout       (exec_dout),
    .rwb        (exec_rwb),
    .ir_accept  (exec_ir_accept),
    .redirect   (redirect),
    .redirect_pc(redirect_pc)
  );

  // -----------------------------------------------------------------------
  // Bus arbitration
  // -----------------------------------------------------------------------
  wire [15:0] AB  = exec_bus_active ? exec_ab  : fetch_ab;
  wire        RWB = exec_bus_active ? exec_rwb : 1'b1;
  wire [7:0]  DO  = exec_dout;

  // -----------------------------------------------------------------------
  // SYNC: instruction boundary indicator.
  //
  // Registered ir_accept: SYNC goes high one cycle after execute accepts
  // a new instruction.  SYNC=1 indicates "a new instruction has been
  // dispatched to execute."  This matches 6502 semantics where SYNC is
  // high during opcode fetch, marking the boundary between instructions.
  // -----------------------------------------------------------------------
  reg sync_r;
  always @(negedge cpu_clk or negedge rst_n)
    if (!rst_n) sync_r <= 1'b0;
    else        sync_r <= exec_ir_accept;

  wire SYNC = sync_r;

  // -----------------------------------------------------------------------
  // Output muxes (identical protocol to 6502 wrapper)
  // -----------------------------------------------------------------------
  assign uo_out  = mux_sel ? {6'b0, SYNC, RWB} : AB[7:0];
  assign uio_out = mux_sel ? DO : AB[15:8];
  assign uio_oe  = mux_sel ? (RWB ? 8'h00 : 8'hFF) : 8'hFF;

  // Unused
  wire _unused = &{ena, ui_in[7:3], ui_in[1:0], 1'b0};

endmodule
