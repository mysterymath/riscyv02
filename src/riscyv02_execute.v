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
// Code is organized by instruction behavior rather than signal type, so each
// instruction's complete behavior is visible in a cohesive section.
// ============================================================================

module riscyv02_execute (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,
    input  wire        irqb,         // Interrupt request (active low)
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

  // ==========================================================================
  // SECTION 1: Interface and State
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
  // SECTION 2: Shared Infrastructure
  // ==========================================================================

  // -------------------------------------------------------------------------
  // Register file (8-bit interface)
  // -------------------------------------------------------------------------
  wire [2:0]  r_sel;
  wire        r_hi;
  wire [7:0]  r;
  wire [2:0]  w_sel;
  wire        w_hi;
  wire [7:0]  w_data;
  wire        w_we;

  riscyv02_regfile u_regfile (
    .clk      (clk),
    .rst_n    (rst_n),
    .w_sel    (w_sel),
    .w_hi     (w_hi),
    .w_data   (w_data),
    .w_we     (w_we),
    .r_sel    (r_sel),
    .r_hi     (r_hi),
    .r        (r)
  );

  // -------------------------------------------------------------------------
  // ALU
  // -------------------------------------------------------------------------
  wire [7:0] alu_a, alu_b;
  wire       alu_new_op;
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
  // Instruction decode (from fetch_ir, stable when ir_valid)
  // -------------------------------------------------------------------------
  wire is_lw   = (fetch_ir[15:12] == 4'b1000);
  wire is_sw   = (fetch_ir[15:12] == 4'b1010);
  wire is_jr   = (fetch_ir[15:9] == 7'b1011100);
  wire is_reti = (fetch_ir == 16'b1111111010000001);
  wire is_sei  = (fetch_ir == 16'b1111111010000010);
  wire is_cli  = (fetch_ir == 16'b1111111010000011);
  wire is_multicycle = is_lw || is_sw || is_jr;

  wire [2:0] ir_base_sel   = is_jr ? fetch_ir[2:0] : fetch_ir[11:9];
  wire [5:0] ir_off6       = fetch_ir[8:3];
  wire [2:0] ir_rd_rs2_sel = fetch_ir[2:0];

  // -------------------------------------------------------------------------
  // Phase identification signals (document what the FSM is doing)
  // -------------------------------------------------------------------------
  wire in_addr_phase = (state == E_ADDR_LO) || (state == E_ADDR_HI);
  wire in_mem_phase  = (state == E_MEM_LO)  || (state == E_MEM_HI);

  // -------------------------------------------------------------------------
  // Core control signals
  // -------------------------------------------------------------------------
  assign bus_active = in_mem_phase;

  // Instruction-driven jump: JR or RETI completing (non-sequential control flow)
  wire jr_completing   = (state == E_ADDR_HI) && (op_r == OP_JR);
  wire reti_completing = (state == E_EXEC) && (op_r == OP_RETI);
  wire insn_jump       = jr_completing || reti_completing;

  // Instruction completing: current instruction finishes, PC updates to next_pc
  wire insn_completing = (state == E_EXEC) || jr_completing || (state == E_MEM_HI);

  // FSM ready: at instruction boundary, can accept new instruction or take IRQ
  // (E_IDLE is ready but not completing; E_EXEC without jump completes but isn't ready)
  wire fsm_ready = (state == E_IDLE) || (state == E_MEM_HI) || insn_jump;

  // Interrupt control
  wire irq_pending = !irqb && !i_bit;
  wire take_irq    = fsm_ready && irq_pending;
  assign ir_accept = fsm_ready && ir_valid && !redirect;

  // ==========================================================================
  // SECTION 3: Per-Instruction-Group Behavior
  // ==========================================================================

  // -------------------------------------------------------------------------
  // 3a. Address Computation (LW, SW, JR share this)
  //
  // Timing: E_ADDR_LO → E_ADDR_HI
  // Resources: ALU computes base + sign-extended offset * 2
  // Result: MAR holds computed address
  //
  // E_ADDR_LO: read base_lo, ALU adds offset_lo (new operation, ci=0)
  // E_ADDR_HI: read base_hi, ALU adds offset_hi (continue, ci=carry)
  // -------------------------------------------------------------------------
  assign alu_new_op = (state == E_ADDR_LO);
  assign alu_a = r;  // base_lo or base_hi from regfile
  assign alu_b = (state == E_ADDR_LO) ? {off6_r[5], off6_r, 1'b0}  // offset * 2
                                      : {8{off6_r[5]}};            // sign extension

  // -------------------------------------------------------------------------
  // 3b. Memory Operations (LW, SW)
  //
  // Timing: E_MEM_LO → E_MEM_HI
  // Resources: Bus for memory access, regfile for data transfer
  //
  // LW: reads from memory, writes to rd (w_we active)
  // SW: reads from rs2, writes to memory (rwb low)
  // -------------------------------------------------------------------------

  // Read port: base register during addr phase, rs2 during mem phase
  assign r_sel = in_mem_phase ? rd_rs2_sel_r : base_sel_r;
  assign r_hi  = (state == E_ADDR_HI) || (state == E_MEM_HI);

  // Write port: active during mem phase for LW only
  assign w_we   = in_mem_phase && (op_r != OP_SW);
  assign w_sel  = rd_rs2_sel_r;
  assign w_hi   = (state == E_MEM_HI);
  assign w_data = uio_in;

  // -------------------------------------------------------------------------
  // 3c. Jump Operations (JR, RETI)
  //
  // JR:   Timing: E_ADDR_LO → E_ADDR_HI (completes with address)
  //       Target: computed address from ALU
  //
  // RETI: Timing: E_EXEC (single cycle)
  //       Target: saved EPC (low bit cleared)
  //       Effect: restores I flag from epc[0]
  // -------------------------------------------------------------------------
  wire [15:0] jump_target = (op_r == OP_RETI) ? {epc[15:1], 1'b0}
                                              : {alu_result, MAR[7:0]};

  // -------------------------------------------------------------------------
  // 3d. Flag Operations (SEI, CLI)
  //
  // Timing: E_EXEC (single cycle)
  // SEI: sets i_bit to disable interrupts
  // CLI: clears i_bit to enable interrupts
  // (Effects applied in FSM sequential block)
  // -------------------------------------------------------------------------

  // ==========================================================================
  // SECTION 4: Control Flow Aggregation
  // ==========================================================================

  // -------------------------------------------------------------------------
  // Next PC: where execution continues after current instruction
  //
  // INVARIANT: All PC updates MUST go through next_pc. When take_irq fires,
  // epc <= next_pc and the FSM's normal PC updates are skipped. Using next_pc
  // for all PC writes ensures the interrupted value is always saved correctly.
  //
  // Cases:
  //   insn_jump:      go to jump_target (JR computed address, RETI saved EPC)
  //   E_IDLE:         PC already advanced by previous instruction
  //   Otherwise:      advance to pc + 2
  // -------------------------------------------------------------------------
  wire [15:0] next_pc = insn_jump         ? jump_target :
                        (state == E_IDLE) ? pc :
                        pc + 16'd2;

  // -------------------------------------------------------------------------
  // Redirect interface (JR, RETI, IRQ entry)
  //
  // redirect: asserted when fetch should discard current instruction stream
  // redirect_pc: target address for the redirect
  // -------------------------------------------------------------------------
  assign redirect    = insn_jump || take_irq;
  assign redirect_pc = take_irq ? 16'h0004 : jump_target;

  // ==========================================================================
  // SECTION 5: Bus Outputs and FSM
  // ==========================================================================

  // -------------------------------------------------------------------------
  // Bus outputs (active only during memory phase)
  // -------------------------------------------------------------------------
  always @(*) begin
    ab   = 16'h0000;
    dout = 8'h00;
    rwb  = 1'b1;
    if (in_mem_phase) begin
      ab   = (state == E_MEM_LO) ? MAR : {MAR[15:1], 1'b1};
      dout = r;   // rs2_lo or rs2_hi from regfile (only meaningful for SW)
      rwb  = (op_r != OP_SW);
    end
  end

  // -------------------------------------------------------------------------
  // FSM (negedge clk)
  // -------------------------------------------------------------------------
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
    end else begin

      // ---------------------------------------------------------------------
      // PC update (centralized)
      // ---------------------------------------------------------------------
      if (take_irq)
        pc <= 16'h0004;
      else if (insn_completing)
        pc <= next_pc;

      // ---------------------------------------------------------------------
      // IRQ entry (highest priority)
      // ---------------------------------------------------------------------
      if (take_irq) begin
        epc   <= next_pc | {15'b0, i_bit};  // Save return address with I flag
        i_bit <= 1'b1;                       // Disable further interrupts
        state <= E_IDLE;
      end

      // ---------------------------------------------------------------------
      // Instruction dispatch (centralized)
      //
      // ir_accept fires from E_IDLE or E_MEM_HI (not during insn_jump since
      // that sets redirect). All dispatch actions happen here:
      //   - Latch decoded instruction fields
      //   - Transition to E_EXEC (single-cycle) or E_ADDR_LO (multi-cycle)
      // ---------------------------------------------------------------------
      else if (ir_accept) begin
        op_r         <= is_sw   ? OP_SW   :
                        is_lw   ? OP_LW   :
                        is_jr   ? OP_JR   :
                        is_reti ? OP_RETI :
                        is_sei  ? OP_SEI  :
                        is_cli  ? OP_CLI  : OP_NOP;
        base_sel_r   <= ir_base_sel;
        off6_r       <= ir_off6;
        rd_rs2_sel_r <= ir_rd_rs2_sel;
        state        <= is_multicycle ? E_ADDR_LO : E_EXEC;
      end

      // ---------------------------------------------------------------------
      // Non-dispatch state transitions and instruction effects
      // ---------------------------------------------------------------------
      else case (state)

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

endmodule
