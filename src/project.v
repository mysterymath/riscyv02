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
 *     uo_out[1]    = SYNC (always 0 for this core)
 *     uo_out[7:2]  = 0
 *     uio[7:0]     = D[7:0] bidirectional data bus
 *     uio_oe       = RWB ? 8'h00 : 8'hFF
 *
 * Instruction encoding (16-bit):
 *   LW  rd, off(rs1):  [1000][rs1:3][off6:6][rd:3]
 *   SW  rs2, off(rs1): [1010][rs1:3][off6:6][rs2:3]
 *   JR  rs, off6:      [1011100][off6:6][rs:3]
 */

`default_nettype none

// =========================================================================
// Top module: PC, register file, mux_sel, bus arbitration, output muxes
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
  // Mux select: dual-edge register (identical to 6502 wrapper).
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
  // PC register — only execute updates it (architectural confirmed PC).
  // -----------------------------------------------------------------------
  reg [15:0] PC;
  wire        exec_pc_we;
  wire [15:0] exec_pc_next;

  always @(negedge clk or negedge rst_n) begin
    if (!rst_n)
      PC <= 16'h0000;
    else if (exec_pc_we)
      PC <= exec_pc_next;
  end

  // -----------------------------------------------------------------------
  // Register file: 8 x 16-bit GP registers
  // -----------------------------------------------------------------------
  reg [15:0] regs [0:7];

  // Execute read port
  wire [2:0]  reg_a_sel;
  wire [15:0] reg_a = regs[reg_a_sel];

  // Fetch read port (for speculative JR resolution)
  wire [2:0]  fetch_reg_sel;
  wire [15:0] fetch_reg = regs[fetch_reg_sel];

  wire [2:0]  rd_sel;
  wire [15:0] rd_data;
  wire        rd_we;

  integer k;
  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (k = 0; k < 8; k = k + 1)
        regs[k] <= 16'h0000;
    end else if (rd_we) begin
      regs[rd_sel] <= rd_data;
    end
  end

  // -----------------------------------------------------------------------
  // Inter-module wires
  // -----------------------------------------------------------------------
  wire        ir_valid;
  wire [15:0] new_ir;
  wire [15:0] fetch_ab;
  wire [15:0] ir_addr;

  wire        exec_busy;
  wire [15:0] exec_ab;
  wire [7:0]  exec_do;
  wire        exec_rwb;

  // -----------------------------------------------------------------------
  // Submodule instances
  // -----------------------------------------------------------------------
  riscyv02_fetch u_fetch (
    .clk           (clk),
    .rst_n         (rst_n),
    .uio_in        (uio_in),
    .fetch_reg     (fetch_reg),
    .exec_busy     (exec_busy),
    .ir_valid      (ir_valid),
    .new_ir        (new_ir),
    .fetch_ab      (fetch_ab),
    .fetch_reg_sel (fetch_reg_sel),
    .ir_addr       (ir_addr)
  );

  riscyv02_execute u_execute (
    .clk          (clk),
    .rst_n        (rst_n),
    .uio_in       (uio_in),
    .ir_valid     (ir_valid),
    .new_ir       (new_ir),
    .ir_addr      (ir_addr),
    .reg_a        (reg_a),
    .exec_pc_we   (exec_pc_we),
    .exec_pc_next (exec_pc_next),
    .exec_busy    (exec_busy),
    .exec_ab      (exec_ab),
    .exec_do      (exec_do),
    .exec_rwb     (exec_rwb),
    .reg_a_sel    (reg_a_sel),
    .rd_sel       (rd_sel),
    .rd_data      (rd_data),
    .rd_we        (rd_we)
  );

  // -----------------------------------------------------------------------
  // Bus arbitration
  // -----------------------------------------------------------------------
  wire [15:0] AB  = exec_busy ? exec_ab  : fetch_ab;
  wire        RWB = exec_busy ? exec_rwb : 1'b1;
  wire [7:0]  DO  = exec_do;

  // -----------------------------------------------------------------------
  // Output muxes (identical protocol to 6502 wrapper)
  // -----------------------------------------------------------------------
  assign uo_out  = mux_sel ? {6'b0, 1'b0, RWB} : AB[7:0];
  assign uio_out = mux_sel ? DO : AB[15:8];
  assign uio_oe  = mux_sel ? (RWB ? 8'h00 : 8'hFF) : 8'hFF;

  // Unused
  wire _unused = &{ena, ui_in, 1'b0};

endmodule
