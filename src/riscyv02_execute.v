/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// ============================================================================
// Execute unit: FSM + ALU + register file.
//
// Handles LW, SW, LB, LBU, SB, JR, AUIPC, RETI, SEI, CLI, BRK, EPCR, EPCW instructions. The register file lives
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
    input  wire        nmi_pending,  // NMI pending (from project.v, ungated domain)
    input  wire        nmi_edge,     // NMI combinational edge (same-cycle detection)
    input  wire        ir_valid,
    input  wire [15:0] fetch_ir,
    output reg         bus_active,
    output reg  [15:0] ab,
    output reg  [7:0]  dout,
    output reg         rwb,
    output wire        ir_accept,
    output reg         nmi_ack,      // NMI acknowledged (registered, for clearing nmi_pending)
    output wire        waiting,      // WAI: halted until interrupt (gates cpu_clk)
    output wire        stopped,      // STP: halted permanently, only reset recovers
    // Fetch pipeline flush and next-instruction address
    output reg         fetch_flush,
    output reg  [15:0] fetch_pc
);

  // ==========================================================================
  // Interface and State
  // ==========================================================================

  // FSM states
  localparam E_IDLE    = 3'd0;  // Waiting for instruction
  localparam E_EXEC_LO = 3'd1;  // Execute low byte / single-cycle effects
  localparam E_ADDR_LO = 3'd2;  // Computing address low byte
  localparam E_ADDR_HI = 3'd3;  // Computing address high byte
  localparam E_MEM_LO  = 3'd4;  // Memory access low byte
  localparam E_MEM_HI  = 3'd5;  // Memory access high byte (can accept next)
  localparam E_EXEC_HI = 3'd6;  // Execute high byte (two-cycle instructions)

  reg [2:0]  state;
  reg [15:0] MAR;       // Memory Address Register

  // Interrupt and PC state
  reg [15:0] pc;        // Program counter (current instruction address)
  reg [15:0] epc;       // Exception PC (bit 0 used for I flag on save)
  reg        i_bit;     // Interrupt disable flag (0=enabled, 1=disabled)

  // Decoded instruction state (latched at ir_accept)
  localparam OP_NOP  = 4'd0;
  localparam OP_SEI  = 4'd1;
  localparam OP_CLI  = 4'd2;
  localparam OP_RETI = 4'd3;
  localparam OP_LW   = 4'd4;
  localparam OP_SW   = 4'd5;
  localparam OP_JR   = 4'd6;
  localparam OP_WAI  = 4'd7;
  localparam OP_STP  = 4'd8;
  localparam OP_BRK  = 4'd9;
  localparam OP_EPCR = 4'd10;
  localparam OP_EPCW = 4'd11;
  localparam OP_LB   = 4'd12;
  localparam OP_LBU  = 4'd13;
  localparam OP_SB    = 4'd14;
  localparam OP_AUIPC = 4'd15;

  reg [3:0]  op_r;            // Instruction opcode
  reg [3:0]  base_sel_r;      // Base register (or imm10[9:6] for AUIPC)
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
  reg  [7:0] w_data;

  riscyv02_regfile u_regfile (
    .clk    (clk),
    .rst_n  (rst_n),
    .w_sel  (rd_rs2_sel_r),
    .w_hi   (w_hi),
    .w_data (w_data),
    .w_we   (w_we),
    .r_sel  (r_sel),
    .r_hi   (r_hi),
    .r      (r)
  );

  // -------------------------------------------------------------------------
  // ALU
  // -------------------------------------------------------------------------
  reg  [7:0] alu_a;
  reg  [7:0] alu_b;
  reg        alu_new_op;
  wire [7:0] alu_result;

  riscyv02_alu u_alu (
    .clk    (clk),
    .rst_n  (rst_n),
    .a      (alu_a),
    .b      (alu_b),
    .new_op (alu_new_op),
    .result (alu_result)
  );

  // -------------------------------------------------------------------------
  // State-driven signals (computed in state-property block below)
  // -------------------------------------------------------------------------
  reg        insn_completing;
  reg [15:0] next_pc;   // The next instruction in non-interrupted control flow
  reg        jump;      // Whether next_pc isn't the sequential next instruction

  // Interrupt control: NMI has priority over IRQ.
  // nmi_edge is combinational so NMI is taken the same cycle the falling
  // edge arrives (no 1-cycle detection latency when fsm_ready).
  // nmi_ack guard prevents double-fire while waiting for project.v to clear
  // nmi_pending (nmi_ack stays high until the handshake completes).
  wire fsm_ready = state == E_IDLE || insn_completing;
  wire take_nmi = fsm_ready && (nmi_pending || nmi_edge) && !nmi_ack;
  wire take_irq = fsm_ready && !irqb && !i_bit && !take_nmi;
  assign ir_accept      = fsm_ready && ir_valid && !fetch_flush;
  assign waiting = (state == E_IDLE) && (op_r == OP_WAI);
  assign stopped = (state == E_IDLE) && (op_r == OP_STP);

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
    alu_a           = r;
    alu_new_op      = 1'bx;
    alu_b           = 8'bx;
    r_sel           = 3'bx;
    r_hi            = 1'bx;
    w_hi            = 1'bx;
    w_data          = uio_in;
    w_we            = 1'b0;
    insn_completing = 1'b0;
    next_pc         = pc;
    fetch_pc        = pc + 16'd2;
    jump            = 1'b0;

    case (state)
      E_IDLE: begin
        fetch_pc  = pc;
      end

      E_EXEC_LO: begin
        if (op_r == OP_RETI) begin
          insn_completing = 1'b1;
          jump      = 1'b1;
          next_pc   = {epc[15:1], 1'b0};
        end else if (op_r == OP_EPCR) begin
          w_data = epc[7:0];
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else if (op_r == OP_EPCW) begin
          r_sel = rd_rs2_sel_r;
          r_hi  = 1'b0;
        end else begin
          next_pc += 16'd2;
          if (op_r == OP_BRK)
            jump = 1'b1;
          else if (op_r != OP_WAI && op_r != OP_STP)
            insn_completing = 1'b1;
        end
      end

      E_EXEC_HI: begin
        insn_completing = 1'b1;
        next_pc        += 16'd2;
        if (op_r == OP_EPCR) begin
          w_data = epc[15:8];
          w_hi   = 1'b1;
          w_we   = 1'b1;
        end else begin
          r_sel = rd_rs2_sel_r;
          r_hi  = 1'b1;
        end
      end

      E_ADDR_LO: begin
        alu_new_op = 1'b1;
        if (op_r == OP_AUIPC) begin
          alu_a  = pc[7:0];
          alu_b  = {off6_r[1:0], 6'b0};            // (imm10 << 6) low byte
          w_data = alu_result;
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else begin
          if (op_r == OP_LB || op_r == OP_LBU || op_r == OP_SB)
            alu_b = {{2{off6_r[5]}}, off6_r};      // unscaled
          else
            alu_b = {off6_r[5], off6_r, 1'b0};     // offset * 2
          r_sel  = base_sel_r[2:0];
          r_hi   = 1'b0;
        end
      end

      E_ADDR_HI: begin
        alu_new_op = 1'b0;
        if (op_r == OP_AUIPC) begin
          alu_a     = pc[15:8];
          alu_b     = {base_sel_r, off6_r[5:2]};   // (imm10 << 6) high byte
          w_data    = alu_result;
          w_hi      = 1'b1;
          w_we      = 1'b1;
          insn_completing = 1'b1;
          next_pc  += 16'd2;
        end else begin
          alu_b     = {8{off6_r[5]}};  // sign extension
          r_sel     = base_sel_r[2:0];
          r_hi      = 1'b1;
          if (op_r == OP_JR) begin
            insn_completing = 1'b1;
            jump      = 1'b1;
            next_pc   = {alu_result, MAR[7:0]};
          end
        end
      end

      E_MEM_LO: begin
        bus_active   = 1'b1;
        ab           = MAR;
        r_sel        = rd_rs2_sel_r;
        r_hi         = 1'b0;
        w_hi         = 1'b0;
        w_we         = (op_r != OP_SW && op_r != OP_SB);
        if (op_r == OP_SB) begin
          insn_completing = 1'b1;
          next_pc        += 16'd2;
        end
      end

      E_MEM_HI: begin
        insn_completing = 1'b1;
        next_pc        += 16'd2;
        w_hi            = 1'b1;
        if (op_r == OP_LB || op_r == OP_LBU) begin
          r_sel         = rd_rs2_sel_r;
          r_hi          = 1'b0;
          w_data        = (op_r == OP_LB) ? {8{r[7]}} : 8'h00;
          w_we          = 1'b1;
        end else begin
          bus_active    = 1'b1;
          ab            = {MAR[15:1], 1'b1};
          r_sel         = rd_rs2_sel_r;
          r_hi          = 1'b1;
          w_we          = (op_r != OP_SW);
        end
      end

    endcase

    case (state)
      E_MEM_LO, E_MEM_HI: begin
        dout = r;   // rs2_lo or rs2_hi from regfile (only meaningful for SW/SB)
        rwb  = (op_r != OP_SW && op_r != OP_SB);
      end
    endcase

    // Flush: interrupts or instruction jump (JR, RETI, BRK).
    fetch_flush = take_nmi || take_irq || jump;
  end

  // ==========================================================================
  // FSM (negedge clk)
  // ==========================================================================

  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state            <= E_IDLE;
      MAR              <= 16'h0000;
      op_r             <= OP_NOP;
      base_sel_r       <= 4'b0000;
      off6_r           <= 6'b000000;
      rd_rs2_sel_r     <= 3'b000;
      pc               <= 16'h0000;
      epc              <= 16'h0000;
      i_bit            <= 1'b1;  // Interrupts disabled after reset
      nmi_ack          <= 1'b0;
    end else begin
      // NMI handshake: set nmi_ack when NMI is taken; hold until project.v
      // clears nmi_pending, then release.
      if (!nmi_pending) nmi_ack <= 1'b0;
      else if (take_nmi) nmi_ack <= 1'b1;

      // PC update
      if (take_nmi)
        pc <= 16'h0008;
      else if (take_irq)
        pc <= 16'h0004;
      else
        pc <= next_pc;

      // ---------------------------------------------------------------------
      // State machine: transitions and per-state effects.
      // Ordered before interrupt entry and dispatch so their overrides
      // (state, epc, i_bit, pc, op_r) take priority via last-NBA-wins.
      // ---------------------------------------------------------------------
      case (state)
        E_IDLE: ;

        E_EXEC_LO: begin
          if (op_r == OP_SEI) i_bit <= 1'b1;
          if (op_r == OP_CLI) i_bit <= 1'b0;
          if (op_r == OP_RETI) i_bit <= epc[0];
          if (op_r == OP_EPCW) epc[7:0] <= r;
          if (op_r == OP_BRK) begin
            epc   <= next_pc | {15'b0, i_bit};
            i_bit <= 1'b1;
            pc    <= 16'h000C;
          end
          if (op_r == OP_EPCR || op_r == OP_EPCW)
            state <= E_EXEC_HI;
          else
            state <= E_IDLE;
        end

        E_ADDR_LO: begin
          MAR[7:0] <= alu_result;
          state    <= E_ADDR_HI;
        end

        E_ADDR_HI: begin
          MAR[15:8] <= alu_result;
          state     <= (op_r == OP_JR || op_r == OP_AUIPC) ? E_IDLE : E_MEM_LO;
        end

        E_MEM_LO: state <= (op_r == OP_SB) ? E_IDLE : E_MEM_HI;

        E_MEM_HI: state <= E_IDLE;

        E_EXEC_HI: begin
          if (op_r == OP_EPCW) epc[15:8] <= r;
          state <= E_IDLE;
        end

        default: state <= 3'bx;
      endcase

      // ---------------------------------------------------------------------
      // Interrupt entry (overrides state, epc, i_bit from case block)
      // ---------------------------------------------------------------------
      if (take_nmi || take_irq) begin
        epc   <= next_pc | {15'b0, i_bit};
        i_bit <= 1'b1;
        op_r  <= OP_NOP;
        state <= E_IDLE;
      end

      // ---------------------------------------------------------------------
      // Instruction dispatch (overrides state, op_r from case block).
      // ir_accept fires in any completing state (insn_completing=1) or
      // E_IDLE, as long as ir_valid && !fetch_flush.
      // ---------------------------------------------------------------------
      if (ir_accept) begin
        // Decode opcode from fetch_ir bit patterns
        if      (fetch_ir[15:12] == 4'b1010)        op_r <= OP_SW;
        else if (fetch_ir[15:12] == 4'b1000)        op_r <= OP_LW;
        else if (fetch_ir[15:9]  == 7'b1011100)     op_r <= OP_JR;
        else if (fetch_ir == 16'b1111111010000001)  op_r <= OP_RETI;
        else if (fetch_ir == 16'b1111111010000010)  op_r <= OP_SEI;
        else if (fetch_ir == 16'b1111111010000011)  op_r <= OP_CLI;
        else if (fetch_ir == 16'b1111111010000100)  op_r <= OP_BRK;
        else if (fetch_ir == 16'b1111111010000101)  op_r <= OP_WAI;
        else if (fetch_ir == 16'b1111111010000110)  op_r <= OP_STP;
        else if (fetch_ir[15:3] == 13'b1111111001110)  op_r <= OP_EPCR;
        else if (fetch_ir[15:3] == 13'b1111111001111)  op_r <= OP_EPCW;
        else if (fetch_ir[15:13] == 3'b001)           op_r <= OP_AUIPC;
        else if (fetch_ir[15:12] == 4'b0110)        op_r <= OP_LB;
        else if (fetch_ir[15:12] == 4'b0111)        op_r <= OP_LBU;
        else if (fetch_ir[15:12] == 4'b1001)        op_r <= OP_SB;
        else                                        op_r <= OP_NOP;
        // AUIPC captures imm10[9:6]; JR uses rs; others use rs1
        if (fetch_ir[15:13] == 3'b001)
          base_sel_r <= fetch_ir[12:9];
        else if (fetch_ir[15:9] == 7'b1011100)
          base_sel_r <= {1'b0, fetch_ir[2:0]};
        else
          base_sel_r <= {1'b0, fetch_ir[11:9]};
        off6_r       <= fetch_ir[8:3];
        rd_rs2_sel_r <= fetch_ir[2:0];
        // AUIPC, LB, LBU, LW, SB, SW, JR are multi-cycle; others go to E_EXEC_LO
        state <= (fetch_ir[15:13] == 3'b001 ||
                  fetch_ir[15:12] == 4'b0110 ||
                  fetch_ir[15:12] == 4'b0111 ||
                  fetch_ir[15:12] == 4'b1000 ||
                  fetch_ir[15:12] == 4'b1001 ||
                  fetch_ir[15:12] == 4'b1010 ||
                  fetch_ir[15:9]  == 7'b1011100) ? E_ADDR_LO : E_EXEC_LO;
      end
    end
  end

endmodule
