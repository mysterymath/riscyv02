/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// =========================================================================
// Fetch unit: owns addr internally, resolves JR with explicit interlock.
//
// JR resolves immediately in F_HI unless jr_hazard is asserted (RAW
// hazard: execute holds a pending LW to the JR source register).  When
// hazarded, F_HI commits the instruction dispatch but enters F_HAZARD
// instead of F_LO.  F_HAZARD doesn't drive the bus — it just waits for
// the hazard to clear, at which point fetch_r (via the forwarding mux
// in project.v) holds the correct value.  F_HAZARD then commits the JR
// target address and proceeds to F_LO.  This achieves zero-bubble JR
// resolution: F_HAZARD resolves on the same cycle as execute's LOAD_HI,
// concurrent with the register write.
// =========================================================================
module riscyv02_fetch (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,
    input  wire [15:0] fetch_r,
    input  wire        bus_free,
    input  wire        exec_busy,
    input  wire        ir_accept,
    input  wire        jr_hazard,
    output reg         ir_valid,
    output reg  [15:0] new_ir,
    output wire [15:0] ab,
    output wire [2:0]  fetch_r_sel
);

  localparam F_LO   = 2'd0;
  localparam F_HI   = 2'd1;
  localparam F_HAZARD = 2'd2;

  reg [1:0]  state;
  reg [7:0]  ir_lo;
  reg [15:0] addr;

  // Deferred JR state: rs and offset latched when entering F_HAZARD.
  reg [2:0] jr_rs;
  reg [5:0] jr_off6;

  // Combinational decode of the instruction being assembled
  wire [15:0] fetched_ir = {uio_in, ir_lo};
  wire is_jr = (state == F_HI) && (fetched_ir[15:9] == 7'b1011100);

  // fetch_r_sel: in F_HAZARD, read the JR register so the forwarding mux
  // in project.v can supply the correct value.
  assign fetch_r_sel = (state == F_HAZARD) ? jr_rs : fetched_ir[2:0];

  // JR target: in F_HI, computed from fetched_ir fields; in F_HAZARD,
  // recomputed from latched jr_off6.  Both use fetch_r (which includes
  // forwarded write data via project.v).
  wire [5:0] jr_off_mux = (state == F_HAZARD) ? jr_off6 : fetched_ir[8:3];
  wire [15:0] jr_offset = {{9{jr_off_mux[5]}}, jr_off_mux, 1'b0};
  wire [15:0] jr_target = fetch_r + jr_offset;

  wire [15:0] seq_pc = {addr[15:1] + 15'd1, 1'b0};

  // Bus address: F_HAZARD doesn't fetch, so addr is stale but unused
  // (bus arbitration only selects fetch_ab when execute isn't bus_active).
  assign ab = (state == F_HI) ? {addr[15:1], 1'b1} : addr;

  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state    <= F_LO;
      ir_lo    <= 8'h00;
      ir_valid <= 1'b0;
      new_ir   <= 16'h0000;
      addr     <= 16'h0000;
      jr_rs    <= 3'b000;
      jr_off6  <= 6'b000000;
    end else begin
      if (ir_accept)
        ir_valid <= 1'b0;

      case (state)
        F_LO: if (bus_free) begin
          ir_lo  <= uio_in;
          state  <= F_HI;
        end

        F_HI: if (bus_free && !exec_busy) begin
          new_ir   <= fetched_ir;
          ir_valid <= 1'b1;
          if (is_jr && jr_hazard) begin
            // RAW hazard: dispatch the instruction but defer JR resolution.
            jr_rs   <= fetched_ir[2:0];
            jr_off6 <= fetched_ir[8:3];
            state   <= F_HAZARD;
          end else begin
            addr  <= is_jr ? jr_target : seq_pc;
            state <= F_LO;
          end
        end

        F_HAZARD: if (!jr_hazard) begin
          // Hazard cleared: fetch_r now holds the correct value
          // (via forwarding or regfile).  Commit JR target.
          addr  <= jr_target;
          state <= F_LO;
        end

        default: state <= F_LO;
      endcase
    end
  end

endmodule
