/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// ============================================================================
// Execute unit: FSM + ALU + register file.
//
// Handles LW, SW, JR, RETI, SEI, CLI instructions. The register file lives
// here since only execute needs register access.
//
// Code is organized by state: combinational signals are computed in a single
// state-property block so each state's behavior is visible in one place.
// ============================================================================

module riscyv02_execute (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,
    input  wire        irqb,         // Interrupt request (active low, level-sensitive)
    input  wire        nmib,         // Non-maskable interrupt (active low, edge-triggered)
    input  wire        ir_valid,
    input  wire [15:0] fetch_ir,
    output reg         bus_active,
    output reg  [15:0] ab,
    output reg  [7:0]  dout,
    output reg         rwb,
    output wire        ir_accept,
    // Control flow redirect to fetch
    output reg         redirect,
    output reg  [15:0] redirect_pc
);

  // ==========================================================================
  // Interface and State
  // ==========================================================================

  // FSM states
  localparam E_IDLE    = 3'd0;  // Waiting for instruction
  localparam E_EXEC    = 3'd1;  // Execute single-cycle instruction effects
  localparam E_ADDR_LO = 3'd2;  // Computing address low byte
  localparam E_ADDR_HI = 3'd3;  // Computing address high byte
  localparam E_MEM_LO  = 3'd4;  // Memory access low byte
  localparam E_MEM_HI  = 3'd5;  // Memory access high byte (can accept next)

  reg [2:0]  state;
  reg [15:0] MAR;       // Memory Address Register

  // Interrupt and PC state
  reg [15:0] pc;        // Program counter (current instruction address)
  reg [15:0] epc;       // Exception PC (bit 0 used for I flag on save)
  reg        i_bit;     // Interrupt disable flag (0=enabled, 1=disabled)
  reg        nmib_prev; // Previous NMIB value for falling-edge detection
  reg        nmi_pending; // Latched NMI (set on falling edge, cleared when taken)

  // Decoded instruction state (latched at ir_accept)
  // 3-bit opcode encoding (saves DFFs vs one-hot)
  localparam OP_NOP  = 3'd0;
  localparam OP_SEI  = 3'd1;
  localparam OP_CLI  = 3'd2;
  localparam OP_RETI = 3'd3;
  localparam OP_LW   = 3'd4;
  localparam OP_SW   = 3'd5;
  localparam OP_JR   = 3'd6;

  reg [2:0]  op_r;            // Instruction opcode
  reg [2:0]  base_sel_r;      // Base register selector
  reg [5:0]  off6_r;          // 6-bit offset
  reg [2:0]  rd_rs2_sel_r;    // Destination (LW) or source (SW)

  // ==========================================================================
  // Shared Infrastructure
  // ==========================================================================

  // -------------------------------------------------------------------------
  // Register file (8-bit interface)
  // -------------------------------------------------------------------------
  reg  [2:0] r_sel;
  reg        r_hi;
  wire [7:0] r;
  reg        w_hi;
  reg        w_we;

  riscyv02_regfile u_regfile (
    .clk    (clk),
    .rst_n  (rst_n),
    .w_sel  (rd_rs2_sel_r),
    .w_hi   (w_hi),
    .w_data (uio_in),
    .w_we   (w_we),
    .r_sel  (r_sel),
    .r_hi   (r_hi),
    .r      (r)
  );

  // -------------------------------------------------------------------------
  // ALU
  // -------------------------------------------------------------------------
  reg  [7:0] alu_b;
  reg        alu_new_op;
  wire [7:0] alu_result;

  riscyv02_alu u_alu (
    .clk    (clk),
    .rst_n  (rst_n),
    .a      (r),
    .b      (alu_b),
    .new_op (alu_new_op),
    .result (alu_result)
  );

  // -------------------------------------------------------------------------
  // State-driven signals (computed in state-property block below)
  // -------------------------------------------------------------------------
  reg        fsm_ready;
  reg [15:0] next_pc;
  reg        jump;      // Instruction wants to redirect fetch to next_pc

  // Interrupt control: NMI has priority over IRQ.
  // nmi_edge is combinational so NMI is taken the same cycle the falling
  // edge arrives (no 1-cycle detection latency when fsm_ready).
  wire nmi_edge = nmib_prev && !nmib;
  wire take_nmi = fsm_ready && (nmi_pending || nmi_edge);
  wire take_irq = fsm_ready && !irqb && !i_bit && !take_nmi;
  assign ir_accept  = fsm_ready && ir_valid && !redirect;

  // ==========================================================================
  // State-Property Block
  //
  // All state-dependent combinational signals computed in one place.
  // Each state's properties are visible together.
  // ==========================================================================

  always @(*) begin
    // Defaults
    bus_active      = 1'b0;
    ab              = 16'bx;
    dout            = 8'bx;
    rwb             = 1'bx;
    alu_new_op      = 1'bx;
    alu_b           = 8'bx;
    r_sel           = 3'bx;
    r_hi            = 1'bx;
    w_hi            = 1'bx;
    w_we            = 1'b0;
    fsm_ready       = 1'b0;
    next_pc         = pc;
    jump            = 1'b0;

    case (state)
      E_IDLE:
        fsm_ready = 1'b1;

      E_EXEC: begin
        if (op_r == OP_RETI) begin
          fsm_ready = 1'b1;
          jump      = 1'b1;
          next_pc   = {epc[15:1], 1'b0};
        end else
          next_pc += 16'd2;
      end

      E_ADDR_LO: begin
        alu_new_op = 1'b1;
        alu_b      = {off6_r[5], off6_r, 1'b0};  // offset * 2
        r_sel      = base_sel_r;
        r_hi       = 1'b0;
      end

      E_ADDR_HI: begin
        alu_new_op = 1'b0;
        alu_b      = {8{off6_r[5]}};  // sign extension
        r_sel      = base_sel_r;      // base register
        r_hi       = 1'b1;
        if (op_r == OP_JR) begin
          fsm_ready = 1'b1;
          jump      = 1'b1;
          next_pc   = {alu_result, MAR[7:0]};
        end
      end

      E_MEM_LO: begin
        bus_active   = 1'b1;
        ab           = MAR;
        r_sel        = rd_rs2_sel_r;
        r_hi         = 1'b0;
        w_hi         = 1'b0;
        w_we         = (op_r != OP_SW);
      end

      E_MEM_HI: begin
        bus_active      = 1'b1;
        ab              = {MAR[15:1], 1'b1};
        r_sel           = rd_rs2_sel_r;
        r_hi            = 1'b1;
        w_hi            = 1'b1;
        w_we            = (op_r != OP_SW);
        fsm_ready       = 1'b1;
        next_pc        += 16'd2;
      end
    endcase

    case (state)
      E_MEM_LO, E_MEM_HI: begin
        dout = r;   // rs2_lo or rs2_hi from regfile (only meaningful for SW)
        rwb  = (op_r != OP_SW);
      end
    endcase

    // Redirect priority: NMI > IRQ > instruction jump.
    // Interrupts override jump target; instruction jumps only redirect
    // when no interrupt is being taken.
    redirect = (take_nmi || take_irq) || jump;
    if (take_nmi)       redirect_pc = 16'h0008;
    else if (take_irq)  redirect_pc = 16'h0004;
    else                redirect_pc = next_pc;
  end

  // ==========================================================================
  // FSM (negedge clk)
  // ==========================================================================

  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state            <= E_IDLE;
      MAR              <= 16'h0000;
      op_r             <= OP_NOP;
      base_sel_r       <= 3'b000;
      off6_r           <= 6'b000000;
      rd_rs2_sel_r     <= 3'b000;
      pc               <= 16'h0000;
      epc              <= 16'h0000;
      i_bit            <= 1'b1;  // Interrupts disabled after reset
      nmib_prev        <= 1'b1;  // Assume NMIB inactive
      nmi_pending      <= 1'b0;
    end else begin
      // NMI edge detection and pending latch.
      // nmi_pending captures edges that arrive while FSM is busy;
      // cleared when NMI is taken (take_nmi includes combinational nmi_edge).
      nmib_prev <= nmib;
      if (take_nmi)
        nmi_pending <= 1'b0;
      else if (nmi_edge)
        nmi_pending <= 1'b1;

      // PC update: redirect_pc for jumps/interrupts, next_pc otherwise.
      // Interrupt entry also saves EPC and sets I=1.
      pc <= redirect ? redirect_pc : next_pc;
      if (take_nmi || take_irq) begin
        epc   <= next_pc | {15'b0, i_bit};  // Save return address with I flag
        i_bit <= 1'b1;                       // Disable further interrupts
        state <= E_IDLE;
      end else begin
        // ---------------------------------------------------------------------
        // Instruction dispatch (centralized)
        //
        // ir_accept fires from E_IDLE or E_MEM_HI (not during jump since
        // that sets redirect). All dispatch actions happen here:
        //   - Latch decoded instruction fields
        //   - Transition to E_EXEC (single-cycle) or E_ADDR_LO (multi-cycle)
        // ---------------------------------------------------------------------
        if (ir_accept) begin
          // Decode opcode from fetch_ir bit patterns
          if      (fetch_ir[15:12] == 4'b1010)        op_r <= OP_SW;
          else if (fetch_ir[15:12] == 4'b1000)        op_r <= OP_LW;
          else if (fetch_ir[15:9]  == 7'b1011100)     op_r <= OP_JR;
          else if (fetch_ir == 16'b1111111010000001)  op_r <= OP_RETI;
          else if (fetch_ir == 16'b1111111010000010)  op_r <= OP_SEI;
          else if (fetch_ir == 16'b1111111010000011)  op_r <= OP_CLI;
          else                                        op_r <= OP_NOP;
          // JR uses rs field for base; LW/SW use rs1 field
          base_sel_r   <= (fetch_ir[15:9] == 7'b1011100) ? fetch_ir[2:0] : fetch_ir[11:9];
          off6_r       <= fetch_ir[8:3];
          rd_rs2_sel_r <= fetch_ir[2:0];
          // LW, SW, JR are multi-cycle; others go to E_EXEC
          state <= (fetch_ir[15:12] == 4'b1000 ||
                    fetch_ir[15:12] == 4'b1010 ||
                    fetch_ir[15:9]  == 7'b1011100) ? E_ADDR_LO : E_EXEC;
        end else case (state)
          // ---------------------------------------------------------------------
          // Non-dispatch state transitions and instruction effects
          // ---------------------------------------------------------------------
          E_EXEC: begin
            if (op_r == OP_SEI) i_bit <= 1'b1;
            if (op_r == OP_CLI) i_bit <= 1'b0;
            if (op_r == OP_RETI) i_bit <= epc[0];
            state <= E_IDLE;
          end

          E_ADDR_LO: begin
            MAR[7:0] <= alu_result;
            state    <= E_ADDR_HI;
          end

          E_ADDR_HI: begin
            MAR[15:8] <= alu_result;
            state     <= (op_r == OP_JR) ? E_IDLE : E_MEM_LO;
          end

          E_MEM_LO: state <= E_MEM_HI;

          E_MEM_HI: state <= E_IDLE;

          default: state <= E_IDLE;
        endcase
      end
    end
  end

endmodule
