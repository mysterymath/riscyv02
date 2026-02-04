/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// =========================================================================
// Execute unit: FSM + ALU + register file.
//
// Handles LW, SW, and JR instructions.  JR computes its target using the
// ALU and signals a redirect to fetch.  The register file lives here since
// only execute needs register access.
//
// Active instruction state is stored in decoded form: control signals
// (is_store, is_jr) plus extracted fields (base register, offset, dest/src).
// This makes behavioral sharing explicit — LW and SW share E_MEM_LO/HI
// states, differing only in the is_store_r control signal.
//
// Instruction holding is done by fetch: fetch presents ir_valid and holds
// the instruction stable until execute asserts ir_accept.  Execute decodes
// directly from fetch_ir when ready to accept.
// =========================================================================
module riscyv02_execute (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,
    input  wire        ir_valid,
    input  wire [15:0] fetch_ir,
    output wire        bus_active,
    output reg  [15:0] ab,
    output reg  [7:0]  dout,
    output reg         rwb,
    output wire        ir_accept,
    // Control flow redirect to fetch
    output wire        redirect,
    output wire [15:0] redirect_pc
);

  localparam E_IDLE    = 3'd0;
  localparam E_ADDR_LO = 3'd1;  // Computing address low byte
  localparam E_ADDR_HI = 3'd2;  // Computing address high byte
  localparam E_MEM_LO  = 3'd3;  // Memory access low byte
  localparam E_MEM_HI  = 3'd4;  // Memory access high byte (can dispatch)

  reg [2:0]  state;
  reg [15:0] MAR;
  reg [7:0]  mem_lo;
  reg [15:0] store_data;  // Holds base reg in E_ADDR_LO, then rs2 in E_ADDR_HI (for store)

  // -------------------------------------------------------------------------
  // Decoded instruction state (active)
  // -------------------------------------------------------------------------
  reg        is_store_r;      // 1 = SW, 0 = LW or JR
  reg        is_jr_r;         // 1 = JR
  reg [2:0]  base_sel_r;      // Base register selector
  reg [5:0]  off6_r;          // 6-bit offset
  reg [2:0]  rd_rs2_sel_r;    // Destination (LW) or source (SW)

  // -------------------------------------------------------------------------
  // Register file (internal to execute)
  // -------------------------------------------------------------------------
  wire [2:0]  r_sel;
  wire [15:0] r;
  wire [2:0]  w_sel;
  wire [15:0] w_data;
  wire        w_we;

  riscyv02_regfile u_regfile (
    .clk      (clk),
    .rst_n    (rst_n),
    .w_sel    (w_sel),
    .w_data   (w_data),
    .w_we     (w_we),
    .r_sel    (r_sel),
    .r        (r)
  );

  // -------------------------------------------------------------------------
  // Control signals
  // -------------------------------------------------------------------------
  // Ready states can accept a new dispatch.
  wire ready = (state == E_IDLE) || (state == E_MEM_HI);
  assign bus_active = (state == E_MEM_LO) || (state == E_MEM_HI);

  // -------------------------------------------------------------------------
  // Dispatch logic (fetch holds instruction until accepted)
  // -------------------------------------------------------------------------
  // Decode directly from fetch_ir (stable when ir_valid)
  wire is_lw_disp = (fetch_ir[15:12] == 4'b1000);
  wire is_sw_disp = (fetch_ir[15:12] == 4'b1010);
  wire is_jr_disp = (fetch_ir[15:9] == 7'b1011100);
  wire dispatch_is_valid = is_lw_disp || is_sw_disp || is_jr_disp;
  wire dispatch_is_store = is_sw_disp;
  wire dispatch_is_jr = is_jr_disp;
  wire [2:0] dispatch_base_sel = is_jr_disp ? fetch_ir[2:0] : fetch_ir[11:9];
  wire [5:0] dispatch_off6 = fetch_ir[8:3];
  wire [2:0] dispatch_rd_rs2_sel = fetch_ir[2:0];

  // Dispatch: recognised instruction that execute will act on
  wire dispatch_valid = ready && ir_valid && dispatch_is_valid;

  // NOP: instruction available but not recognized
  wire dispatch_nop = ready && ir_valid && !dispatch_is_valid;

  // ir_accept: we consumed an instruction this cycle
  assign ir_accept = ready && ir_valid;

  // -------------------------------------------------------------------------
  // ALU
  // -------------------------------------------------------------------------
  wire [7:0] alu_a, alu_b;
  wire       alu_start;
  wire [7:0] alu_result;
  wire       alu_co;

  riscyv02_alu u_alu (
    .clk    (clk),
    .rst_n  (rst_n),
    .a      (alu_a),
    .b      (alu_b),
    .start  (alu_start),
    .result (alu_result),
    .co     (alu_co)
  );

  // ALU inputs: compute address as register + sign-extended offset * 2
  // Start ALU on E_ADDR_LO; carry propagates to E_ADDR_HI.
  assign alu_start = (state == E_ADDR_LO);
  assign alu_a = (state == E_ADDR_LO) ? r[7:0] : store_data[15:8];
  assign alu_b = (state == E_ADDR_LO) ? {off6_r[5], off6_r, 1'b0} : {8{off6_r[5]}};

  // -------------------------------------------------------------------------
  // Register file interface
  // -------------------------------------------------------------------------
  // Read port mux:
  //   E_ADDR_LO: base_sel_r (rs for JR, rs1 for LW/SW - already decoded)
  //   E_ADDR_HI (SW): rd_rs2_sel_r (rs2 for store data)
  //   Other: don't care
  assign r_sel = (state == E_ADDR_HI && is_store_r) ? rd_rs2_sel_r : base_sel_r;

  // Write port: fires in E_MEM_HI for loads only
  assign w_we   = (state == E_MEM_HI) && !is_store_r;
  assign w_sel  = rd_rs2_sel_r;
  assign w_data = {uio_in, mem_lo};

  // -------------------------------------------------------------------------
  // Redirect interface (for JR)
  // -------------------------------------------------------------------------
  assign redirect    = (state == E_ADDR_HI) && is_jr_r;
  assign redirect_pc = {alu_result, MAR[7:0]};

  // -------------------------------------------------------------------------
  // Bus outputs (combinational)
  // -------------------------------------------------------------------------
  always @(*) begin
    ab   = 16'h0000;
    dout = 8'h00;
    rwb  = 1'b1;
    case (state)
      E_MEM_LO: begin
        ab   = MAR;
        dout = store_data[7:0];
        rwb  = !is_store_r;
      end
      E_MEM_HI: begin
        ab   = {MAR[15:1], 1'b1};
        dout = store_data[15:8];
        rwb  = !is_store_r;
      end
      default: ;
    endcase
  end

  // -------------------------------------------------------------------------
  // FSM (negedge clk)
  // -------------------------------------------------------------------------
  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state            <= E_IDLE;
      MAR              <= 16'h0000;
      mem_lo           <= 8'h00;
      store_data       <= 16'h0000;
      // Decoded instruction state
      is_store_r       <= 1'b0;
      is_jr_r          <= 1'b0;
      base_sel_r       <= 3'b000;
      off6_r           <= 6'b000000;
      rd_rs2_sel_r     <= 3'b000;
    end else begin
      case (state)
        E_IDLE: begin
          if (dispatch_valid) begin
            // Latch decoded instruction fields
            is_store_r     <= dispatch_is_store;
            is_jr_r        <= dispatch_is_jr;
            base_sel_r     <= dispatch_base_sel;
            off6_r         <= dispatch_off6;
            rd_rs2_sel_r   <= dispatch_rd_rs2_sel;
            state          <= E_ADDR_LO;
          end
          // dispatch_nop: just accept (ir_accept fires), stay in E_IDLE
        end

        E_ADDR_LO: begin
          // Compute and latch low byte of address.
          // Latch register value for ALU high byte next cycle.
          MAR[7:0]   <= alu_result;
          store_data <= r;
          state      <= E_ADDR_HI;
        end

        E_ADDR_HI: begin
          // Compute high byte.  Next state depends on instruction type.
          MAR[15:8] <= alu_result;
          if (is_jr_r) begin
            // JR: done, redirect fires this cycle. Can dispatch next.
            if (dispatch_valid) begin
              is_store_r     <= dispatch_is_store;
              is_jr_r        <= dispatch_is_jr;
              base_sel_r     <= dispatch_base_sel;
              off6_r         <= dispatch_off6;
              rd_rs2_sel_r   <= dispatch_rd_rs2_sel;
              state          <= E_ADDR_LO;
            end else begin
              state <= E_IDLE;
            end
          end else begin
            // LW or SW: proceed to memory access
            if (is_store_r) store_data <= r;  // Latch rs2 for store
            state <= E_MEM_LO;
          end
        end

        E_MEM_LO: begin
          if (!is_store_r) mem_lo <= uio_in;  // Capture low byte for load
          state <= E_MEM_HI;
        end

        E_MEM_HI: begin
          if (dispatch_valid) begin
            is_store_r     <= dispatch_is_store;
            is_jr_r        <= dispatch_is_jr;
            base_sel_r     <= dispatch_base_sel;
            off6_r         <= dispatch_off6;
            rd_rs2_sel_r   <= dispatch_rd_rs2_sel;
            state          <= E_ADDR_LO;
          end else begin
            state <= E_IDLE;
          end
        end

        default: state <= E_IDLE;
      endcase
    end
  end

endmodule
