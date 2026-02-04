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
// Pending instruction buffer: when fetch presents an instruction (ir_valid)
// and execute is not ready, the instruction is captured into pending_ir.
// When execute becomes ready, it dispatches from pending_ir.  If execute
// is already ready when fetch presents, it dispatches directly (bypass).
// =========================================================================
module riscyv02_execute (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,
    input  wire        ir_valid,
    input  wire [15:0] new_ir,
    output wire        bus_active,
    output reg  [15:0] ab,
    output reg  [7:0]  dout,
    output reg         rwb,
    output wire        ir_accept,
    // Control flow redirect to fetch
    output wire        redirect,
    output wire [15:0] redirect_pc
);

  localparam E_IDLE       = 3'd0;
  localparam E_ADDR_LO    = 3'd1;  // Computing address low byte
  localparam E_ADDR_HI    = 3'd2;  // Computing address high byte
  localparam E_LOAD_LO    = 3'd3;  // Reading memory low byte
  localparam E_LOAD_HI    = 3'd4;  // Reading memory high byte (can dispatch)
  localparam E_STORE_LO   = 3'd5;  // Writing memory low byte
  localparam E_STORE_HI   = 3'd6;  // Writing memory high byte (can dispatch)

  reg [2:0]  state;
  reg [15:0] IR;
  reg [15:0] MAR;
  reg [7:0]  mem_lo;
  reg [15:0] store_data;  // Holds rs/rs1 in E_ADDR_LO, then rs2 in E_ADDR_HI (for store)

  // -------------------------------------------------------------------------
  // Pending instruction buffer
  // -------------------------------------------------------------------------
  reg [15:0] pending_ir;
  reg        pending_valid;

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
  wire ready = (state == E_IDLE) || (state == E_LOAD_HI) || (state == E_STORE_HI);
  assign bus_active = (state == E_LOAD_LO  || state == E_LOAD_HI ||
                       state == E_STORE_LO || state == E_STORE_HI);

  // -------------------------------------------------------------------------
  // Dispatch logic with pending buffer bypass
  // -------------------------------------------------------------------------
  // Dispatch source: pending buffer if valid, else direct from fetch
  wire [15:0] dispatch_ir = pending_valid ? pending_ir : new_ir;
  wire        dispatch_available = pending_valid || ir_valid;

  // Decode on dispatch_ir (for dispatch decision)
  wire is_lw_disp = (dispatch_ir[15:12] == 4'b1000);
  wire is_sw_disp = (dispatch_ir[15:12] == 4'b1010);
  wire is_jr_disp = (dispatch_ir[15:9] == 7'b1011100);

  // Dispatch: recognised instruction that execute will act on
  wire dispatch_valid = ready && dispatch_available && (is_lw_disp || is_sw_disp || is_jr_disp);

  // NOP: instruction available but not recognized
  wire dispatch_nop = ready && dispatch_available && !(is_lw_disp || is_sw_disp || is_jr_disp);

  // Capture: fetch has instruction, buffer empty, not ready (can't dispatch now)
  wire capture = ir_valid && !pending_valid && !ready;

  // ir_accept: we consumed an instruction this cycle (for SYNC)
  assign ir_accept = dispatch_valid || dispatch_nop;

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
  assign alu_b = (state == E_ADDR_LO) ? {IR[8], IR[8:3], 1'b0} : {8{IR[8]}};

  // -------------------------------------------------------------------------
  // Instruction decode on latched IR (for execution)
  // -------------------------------------------------------------------------
  wire is_lw_ir = (IR[15:12] == 4'b1000);
  wire is_sw_ir = (IR[15:12] == 4'b1010);
  wire is_jr_ir = (IR[15:9] == 7'b1011100);

  // -------------------------------------------------------------------------
  // Register file interface
  // -------------------------------------------------------------------------
  // Read port mux:
  //   E_ADDR_LO (JR):    rs from IR[2:0]
  //   E_ADDR_LO (LW/SW): rs1 from IR[11:9]
  //   E_ADDR_HI (SW):    rs2 from IR[2:0]
  //   Other:             don't care
  assign r_sel = (state == E_ADDR_LO && is_jr_ir) ? IR[2:0] :
                 (state == E_ADDR_HI && is_sw_ir) ? IR[2:0] :
                 IR[11:9];

  // Write port: fires in E_LOAD_HI
  assign w_we   = (state == E_LOAD_HI);
  assign w_sel  = IR[2:0];
  assign w_data = {uio_in, mem_lo};

  // -------------------------------------------------------------------------
  // Redirect interface (for JR)
  // -------------------------------------------------------------------------
  assign redirect    = (state == E_ADDR_HI) && is_jr_ir;
  assign redirect_pc = {alu_result, MAR[7:0]};

  // -------------------------------------------------------------------------
  // Bus outputs (combinational)
  // -------------------------------------------------------------------------
  always @(*) begin
    ab   = MAR;
    dout = 8'h00;
    rwb  = 1'b1;
    case (state)
      E_LOAD_LO: begin
        ab  = MAR;
        rwb = 1'b1;
      end
      E_LOAD_HI: begin
        ab  = {MAR[15:1], 1'b1};
        rwb = 1'b1;
      end
      E_STORE_LO: begin
        ab   = MAR;
        dout = store_data[7:0];
        rwb  = 1'b0;
      end
      E_STORE_HI: begin
        ab   = {MAR[15:1], 1'b1};
        dout = store_data[15:8];
        rwb  = 1'b0;
      end
      default: begin
        ab  = 16'h0000;
        rwb = 1'b1;
      end
    endcase
  end

  // -------------------------------------------------------------------------
  // FSM (negedge clk)
  // -------------------------------------------------------------------------
  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state        <= E_IDLE;
      IR           <= 16'h0000;
      MAR          <= 16'h0000;
      mem_lo       <= 8'h00;
      store_data   <= 16'h0000;
      pending_ir   <= 16'h0000;
      pending_valid <= 1'b0;
    end else begin
      // Capture instruction into pending buffer when not ready
      if (capture) begin
        pending_ir    <= new_ir;
        pending_valid <= 1'b1;
      end

      case (state)
        E_IDLE: begin
          if (dispatch_valid) begin
            IR            <= dispatch_ir;
            pending_valid <= 1'b0;
            state         <= E_ADDR_LO;
          end else if (dispatch_nop) begin
            pending_valid <= 1'b0;
          end
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
          if (is_jr_ir) begin
            // JR: done, redirect fires this cycle. Can dispatch next.
            if (dispatch_valid) begin
              IR            <= dispatch_ir;
              pending_valid <= 1'b0;
              state         <= E_ADDR_LO;
            end else begin
              if (dispatch_nop) pending_valid <= 1'b0;
              state <= E_IDLE;
            end
          end else if (is_sw_ir) begin
            // SW: latch rs2 for store data, proceed to memory write
            store_data <= r;
            state      <= E_STORE_LO;
          end else begin
            // LW: proceed to memory read
            state <= E_LOAD_LO;
          end
        end

        E_LOAD_LO: begin
          mem_lo <= uio_in;
          state  <= E_LOAD_HI;
        end

        E_LOAD_HI: begin
          if (dispatch_valid) begin
            IR            <= dispatch_ir;
            pending_valid <= 1'b0;
            state         <= E_ADDR_LO;
          end else begin
            if (dispatch_nop) pending_valid <= 1'b0;
            state <= E_IDLE;
          end
        end

        E_STORE_LO: begin
          state <= E_STORE_HI;
        end

        E_STORE_HI: begin
          if (dispatch_valid) begin
            IR            <= dispatch_ir;
            pending_valid <= 1'b0;
            state         <= E_ADDR_LO;
          end else begin
            if (dispatch_nop) pending_valid <= 1'b0;
            state <= E_IDLE;
          end
        end

        default: state <= E_IDLE;
      endcase
    end
  end

endmodule
