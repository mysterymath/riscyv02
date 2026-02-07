/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// ============================================================================
// Execute unit: FSM + ALU + register file.
//
// Handles LW, SW, LB, LBU, SB, JR, JALR, J, JAL, AUIPC, LUI, LI, BZ, BNZ,
// RETI, SEI, CLI, BRK, EPCR, EPCW, ADD, SUB, AND, OR, XOR, SLT, SLTU,
// SLL, SRL, SRA, ADDI, ANDI, ORI, XORI, SLTIF, SLTIUF, XORIF,
// SLLI, SRLI, SRAI
// instructions. JAL/JALR write the return address to R6 (link register).
// The register file lives
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
    output wire [15:0] fetch_pc
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
  reg [15:0] tmp;       // Cycle-to-cycle temporary (mem addr, branch target, shift carry)

  // Interrupt and PC state
  reg [15:0] pc;        // Program counter (next instruction to fetch; advanced at dispatch)
  reg [15:0] epc;       // Exception PC (bit 0 used for I flag on save)
  reg        i_bit;     // Interrupt disable flag (0=enabled, 1=disabled)

  // Decoded instruction state (latched at ir_accept)
  localparam OP_NOP    = 6'd0;
  localparam OP_SEI    = 6'd1;
  localparam OP_CLI    = 6'd2;
  localparam OP_RETI   = 6'd3;
  localparam OP_LW     = 6'd4;
  localparam OP_SW     = 6'd5;
  localparam OP_JR     = 6'd6;
  localparam OP_WAI    = 6'd7;
  localparam OP_STP    = 6'd8;
  localparam OP_BRK    = 6'd9;
  localparam OP_EPCR   = 6'd10;
  localparam OP_EPCW   = 6'd11;
  localparam OP_LB     = 6'd12;
  localparam OP_LBU    = 6'd13;
  localparam OP_SB     = 6'd14;
  localparam OP_AUIPC  = 6'd15;
  localparam OP_ADD    = 6'd16;
  localparam OP_SUB    = 6'd17;
  localparam OP_AND    = 6'd18;
  localparam OP_OR     = 6'd19;
  localparam OP_XOR    = 6'd20;
  localparam OP_SLT    = 6'd21;
  localparam OP_SLTU   = 6'd22;
  localparam OP_LI     = 6'd23;
  localparam OP_LUI    = 6'd24;
  localparam OP_BZ     = 6'd25;
  localparam OP_BNZ    = 6'd26;
  localparam OP_J      = 6'd27;
  localparam OP_JAL    = 6'd28;
  localparam OP_JALR   = 6'd29;
  localparam OP_ADDI   = 6'd30;
  localparam OP_ANDI   = 6'd31;
  localparam OP_ORI    = 6'd32;
  localparam OP_XORI   = 6'd33;
  localparam OP_SLTIF  = 6'd34;
  localparam OP_SLTIUF = 6'd35;
  localparam OP_XORIF  = 6'd36;
  localparam OP_SLL    = 6'd37;
  localparam OP_SRL    = 6'd38;
  localparam OP_SRA    = 6'd39;
  localparam OP_SLLI   = 6'd40;
  localparam OP_SRLI   = 6'd41;
  localparam OP_SRAI   = 6'd42;

  localparam LINK_REG = 3'd6;  // R6 is the link register for JAL/JALR
  localparam T0_REG   = 3'd2;  // R2 is t0 for fixed-destination IF-type ops

  reg [5:0]  op_r;            // Instruction opcode
  reg [3:0]  base_sel_r;      // Base register (or imm10[9:6] for AUIPC)
  reg [5:0]  off6_r;          // 6-bit offset
  reg [2:0]  rd_rs2_sel_r;    // Destination (LW) or source (SW)
  reg [2:0]  r_sel_r;         // Registered regfile read select (set at state transitions)
  reg        r_hi_r;          // Registered regfile read hi/lo (set at state transitions)

  // ==========================================================================
  // Shared Infrastructure
  // ==========================================================================

  // -------------------------------------------------------------------------
  // Register file (8-bit interface)
  // -------------------------------------------------------------------------
  reg  [2:0] r_sel;
  reg        r_hi;
  wire [7:0] r;
  wire [7:0] r2;
  reg        w_hi;
  reg        w_we;
  reg  [7:0] w_data;

  wire [2:0] w_sel_mux = (op_r == OP_JAL || op_r == OP_JALR) ? LINK_REG :
                         is_fixed_dest ? T0_REG : rd_rs2_sel_r;

  wire r2_hi_mux = is_shift ? 1'b0 : r_hi;

  riscyv02_regfile u_regfile (
    .clk    (clk),
    .rst_n  (rst_n),
    .w_sel  (w_sel_mux),
    .w_hi   (w_hi),
    .w_data (w_data),
    .w_we   (w_we),
    .r_sel  (r_sel),
    .r_hi   (r_hi),
    .r      (r),
    .r2_sel (off6_r[5:3]),
    .r2_hi  (r2_hi_mux),
    .r2     (r2)
  );

  // -------------------------------------------------------------------------
  // ALU
  // -------------------------------------------------------------------------
  reg  [7:0] alu_a;
  reg  [7:0] alu_b;
  reg  [2:0] alu_op;
  reg        alu_new_op;
  wire [7:0] alu_result;
  wire       alu_co;

  riscyv02_alu u_alu (
    .clk    (clk),
    .rst_n  (rst_n),
    .a      (alu_a),
    .b      (alu_b),
    .op     (alu_op),
    .new_op (alu_new_op),
    .co     (alu_co),
    .result (alu_result)
  );

  // -------------------------------------------------------------------------
  // Barrel shifter
  // -------------------------------------------------------------------------
  wire [3:0] shamt = is_shift_rr ? r2[3:0] : off6_r[3:0];

  reg  [14:0] shifter_din;
  wire [7:0]  shifter_result;

  riscyv02_shifter u_shifter (
    .din    (shifter_din),
    .shamt  (shamt[2:0]),
    .result (shifter_result)
  );

  function [7:0] rev8(input [7:0] v);
    rev8 = {v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7]};
  endfunction

  function [6:0] rev7(input [6:0] v);
    rev7 = {v[0], v[1], v[2], v[3], v[4], v[5], v[6]};
  endfunction

  wire is_alu_rr = (op_r == OP_ADD || op_r == OP_SUB ||
                    op_r == OP_AND || op_r == OP_OR || op_r == OP_XOR ||
                    op_r == OP_SLT || op_r == OP_SLTU);
  wire is_slt = (op_r == OP_SLT || op_r == OP_SLTU);
  wire is_alu_imm = (op_r == OP_ADDI || op_r == OP_ANDI || op_r == OP_ORI ||
                     op_r == OP_XORI || op_r == OP_XORIF);
  wire is_slt_imm = (op_r == OP_SLTIF || op_r == OP_SLTIUF);
  wire is_fixed_dest = (op_r == OP_SLTIF || op_r == OP_SLTIUF || op_r == OP_XORIF);
  wire is_shift_rr  = (op_r == OP_SLL || op_r == OP_SRL || op_r == OP_SRA);
  wire is_shift_imm = (op_r == OP_SLLI || op_r == OP_SRLI || op_r == OP_SRAI);
  wire is_shift     = is_shift_rr || is_shift_imm;
  wire is_right_shift = (op_r == OP_SRL || op_r == OP_SRA ||
                         op_r == OP_SRLI || op_r == OP_SRAI);
  wire is_arith_shift = (op_r == OP_SRA || op_r == OP_SRAI);
  wire is_branch = (op_r == OP_BZ || op_r == OP_BNZ);
  wire is_jump_imm = (op_r == OP_J || op_r == OP_JAL);
  reg  nz_lo_r;  // Latched |rs_lo| for branch zero check

  // -------------------------------------------------------------------------
  // State-driven signals (computed in state-property block below)
  // -------------------------------------------------------------------------
  reg        insn_completing;
  reg [15:0] next_pc;   // Return address / resume point (pc for sequential, jump target for jumps)
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

  assign fetch_pc = pc;

  always @(*) begin
    // Defaults
    bus_active      = 1'b0;
    ab              = 16'bx;
    dout            = 8'bx;
    rwb             = 1'bx;
    alu_a           = r;
    alu_new_op      = 1'bx;
    alu_b           = 8'bx;
    alu_op          = 3'd0;    // ADD (safe default for address computation)
    r_sel           = r_sel_r;
    r_hi            = r_hi_r;
    w_hi            = 1'bx;
    w_data          = uio_in;
    w_we            = 1'b0;
    insn_completing = 1'b0;
    next_pc         = pc;
    jump            = 1'b0;
    shifter_din     = 15'b0;

    // ALU operation select (derived from op_r)
    case (op_r)
      OP_SUB, OP_SLT, OP_SLTU, OP_SLTIF, OP_SLTIUF: alu_op = 3'd1;
      OP_AND, OP_ANDI: alu_op = 3'd2;
      OP_OR, OP_ORI:   alu_op = 3'd3;
      OP_XOR, OP_XORI, OP_XORIF: alu_op = 3'd4;
      default: ;  // alu_op stays 3'd0 (ADD)
    endcase

    case (state)
      E_IDLE: ;

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
        end else if (is_alu_rr) begin
          alu_b      = r2;
          alu_new_op = 1'b1;
          w_data     = alu_result;
          w_hi       = 1'b0;
          w_we       = 1'b1;
          if (is_slt) begin
            w_data = 8'h00;
            w_hi   = 1'b1;
          end
        end else if (is_alu_imm) begin
          alu_b      = {{2{off6_r[5]}}, off6_r};
          alu_new_op = 1'b1;
          w_data     = alu_result;
          w_hi       = 1'b0;
          w_we       = 1'b1;
        end else if (is_slt_imm) begin
          alu_b      = {{2{off6_r[5]}}, off6_r};
          alu_new_op = 1'b1;
          w_data     = 8'h00;
          w_hi       = 1'b1;
          w_we       = 1'b1;
        end else if (is_shift) begin
          // Cycle 1: left shifts process lo normally, right shifts process hi
          // (r_hi_r=1 from dispatch for right shifts, so r = hi byte).
          if (shamt[3]) begin
            // Cross-byte: entire result comes from the other byte.
            // Write zero (or sign for SRA) for the evacuated byte.
            w_data = is_right_shift ?
                     (is_arith_shift ? {8{r[7]}} : 8'h00) : 8'h00;
            w_hi   = is_right_shift ? 1'b1 : 1'b0;
            w_we   = 1'b1;
          end else if (is_right_shift) begin
            // Right shift hi byte: fill from sign/zero
            shifter_din = {is_arith_shift ? {7{r[7]}} : 7'b0, r};
            w_data = shifter_result;
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end else begin
            // Left shift lo byte: reverse, right-shift, reverse
            shifter_din = {7'b0, rev8(r)};
            w_data = rev8(shifter_result);
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end
        end else if (op_r == OP_LI) begin
          w_data = {{2{off6_r[5]}}, off6_r};
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else if (op_r == OP_LUI) begin
          w_data = {off6_r[1:0], 6'b0};
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else if (is_branch) begin
          alu_a      = pc[7:0];
          alu_b      = {off6_r[5], off6_r, 1'b0};
          alu_new_op = 1'b1;
        end else if (is_jump_imm) begin
          alu_a      = pc[7:0];
          alu_b      = {off6_r[3:0], rd_rs2_sel_r, 1'b0};
          alu_new_op = 1'b1;
          if (op_r == OP_JAL) begin
            w_data = pc[7:0];
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end
        end else begin
          if (op_r == OP_BRK)
            jump = 1'b1;
          else if (op_r != OP_WAI && op_r != OP_STP)
            insn_completing = 1'b1;
        end
      end

      E_EXEC_HI: begin
        insn_completing = 1'b1;
        if (op_r == OP_EPCR) begin
          w_data = epc[15:8];
          w_hi   = 1'b1;
          w_we   = 1'b1;
        end else if (is_alu_rr) begin
          alu_b      = r2;
          alu_new_op = 1'b0;
          w_data     = alu_result;
          w_hi       = 1'b1;
          w_we       = 1'b1;
          if (is_slt) begin
            w_hi = 1'b0;
            if (op_r == OP_SLTU)
              w_data = {7'b0, ~alu_co};
            else
              w_data = {7'b0, (r[7] ^ r2[7]) ? r[7] : alu_result[7]};
          end
        end else if (is_alu_imm) begin
          alu_b      = {8{off6_r[5]}};
          alu_new_op = 1'b0;
          w_data     = alu_result;
          w_hi       = 1'b1;
          w_we       = 1'b1;
        end else if (is_slt_imm) begin
          alu_b      = {8{off6_r[5]}};
          alu_new_op = 1'b0;
          w_hi       = 1'b0;
          w_we       = 1'b1;
          if (op_r == OP_SLTIUF)
            w_data = {7'b0, ~alu_co};
          else
            w_data = {7'b0, (r[7] ^ off6_r[5]) ? r[7] : alu_result[7]};
        end else if (is_shift) begin
          // Cycle 2: left shifts process hi, right shifts process lo.
          if (shamt[3]) begin
            // Cross-byte: use tmp[7:0] as data (it has the other byte).
            if (is_right_shift) begin
              shifter_din = {is_arith_shift ? {7{tmp[7]}} : 7'b0, tmp[7:0]};
              w_data = shifter_result;
              w_hi   = 1'b0;
              w_we   = 1'b1;
            end else begin
              shifter_din = {7'b0, rev8(tmp[7:0])};
              w_data = rev8(shifter_result);
              w_hi   = 1'b1;
              w_we   = 1'b1;
            end
          end else if (is_right_shift) begin
            // Right shift lo byte: fill from tmp low bits
            shifter_din = {tmp[6:0], r};
            w_data = shifter_result;
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end else begin
            // Left shift hi byte: reverse, right-shift with reversed tmp fill, reverse
            shifter_din = {rev7(tmp[7:1]), rev8(r)};
            w_data = rev8(shifter_result);
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end
        end else if (op_r == OP_LI) begin
          w_data = {8{off6_r[5]}};
          w_hi   = 1'b1;
          w_we   = 1'b1;
        end else if (op_r == OP_LUI) begin
          w_data = {base_sel_r, off6_r[5:2]};
          w_hi   = 1'b1;
          w_we   = 1'b1;
        end else if (is_branch) begin
          alu_a      = pc[15:8];
          alu_b      = {8{off6_r[5]}};
          alu_new_op = 1'b0;
          if (op_r == OP_BZ && !nz_lo_r && r == 8'h00) begin
            jump    = 1'b1;
            next_pc = {alu_result, tmp[7:0]};
          end
          if (op_r == OP_BNZ && (nz_lo_r || r != 8'h00)) begin
            jump    = 1'b1;
            next_pc = {alu_result, tmp[7:0]};
          end
        end else if (is_jump_imm) begin
          alu_a      = pc[15:8];
          alu_b      = {{3{base_sel_r[2]}}, base_sel_r[2:0], off6_r[5:4]};
          alu_new_op = 1'b0;
          jump       = 1'b1;
          next_pc    = {alu_result, tmp[7:0]};
          if (op_r == OP_JAL) begin
            w_data = pc[15:8];
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end
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
          if (op_r == OP_JALR) begin
            w_data = pc[7:0];
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end
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
        end else begin
          alu_b     = {8{off6_r[5]}};  // sign extension
          if (op_r == OP_JR || op_r == OP_JALR) begin
            insn_completing = 1'b1;
            jump    = 1'b1;
            next_pc = {alu_result, tmp[7:0]};
          end
          if (op_r == OP_JALR) begin
            w_data = pc[15:8];
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end
        end
      end

      E_MEM_LO: begin
        bus_active   = 1'b1;
        ab           = tmp;
        w_hi         = 1'b0;
        w_we         = (op_r != OP_SW && op_r != OP_SB);
        if (op_r == OP_SB)
          insn_completing = 1'b1;
      end

      E_MEM_HI: begin
        insn_completing = 1'b1;
        w_hi            = 1'b1;
        if (op_r == OP_LB || op_r == OP_LBU) begin
          w_data        = (op_r == OP_LB) ? {8{r[7]}} : 8'h00;
          w_we          = 1'b1;
        end else begin
          bus_active    = 1'b1;
          ab            = {tmp[15:1], 1'b1};
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
      tmp              <= 16'h0000;
      op_r             <= OP_NOP;
      base_sel_r       <= 4'b0000;
      off6_r           <= 6'b000000;
      rd_rs2_sel_r     <= 3'b000;
      r_sel_r          <= 3'b000;
      r_hi_r           <= 1'b0;
      nz_lo_r          <= 1'b0;
      pc               <= 16'h0000;
      epc              <= 16'h0000;
      i_bit            <= 1'b1;  // Interrupts disabled after reset
      nmi_ack          <= 1'b0;
    end else begin
      // NMI handshake: set nmi_ack when NMI is taken; hold until project.v
      // clears nmi_pending, then release.
      if (!nmi_pending) nmi_ack <= 1'b0;
      else if (take_nmi) nmi_ack <= 1'b1;

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
          if (op_r == OP_RETI) begin
            i_bit <= epc[0];
            pc    <= next_pc;
          end
          if (op_r == OP_EPCW) epc[7:0] <= r;
          if (op_r == OP_BRK) begin
            epc   <= next_pc | {15'b0, i_bit};
            i_bit <= 1'b1;
            pc    <= 16'h000C;
          end
          if (is_branch || is_jump_imm) begin
            tmp[7:0] <= alu_result;
          end
          if (is_branch) nz_lo_r <= |r;
          if (is_shift) tmp[7:0] <= r;
          if (op_r == OP_EPCR || op_r == OP_EPCW ||
              is_alu_rr || is_alu_imm || is_slt_imm ||
              op_r == OP_LI || op_r == OP_LUI ||
              is_branch || is_jump_imm || is_shift) begin
            state  <= E_EXEC_HI;
            r_hi_r <= is_right_shift ? 1'b0 : 1'b1;
          end else
            state <= E_IDLE;
        end

        E_ADDR_LO: begin
          tmp[7:0] <= alu_result;
          r_hi_r   <= 1'b1;
          state    <= E_ADDR_HI;
        end

        E_ADDR_HI: begin
          tmp[15:8] <= alu_result;
          if (op_r == OP_JR || op_r == OP_JALR) pc <= next_pc;
          r_sel_r   <= rd_rs2_sel_r;
          r_hi_r    <= 1'b0;
          state     <= (op_r == OP_JR || op_r == OP_JALR || op_r == OP_AUIPC) ? E_IDLE : E_MEM_LO;
        end

        E_MEM_LO: begin
          r_hi_r <= (op_r == OP_LB || op_r == OP_LBU) ? 1'b0 : 1'b1;
          state  <= (op_r == OP_SB) ? E_IDLE : E_MEM_HI;
        end

        E_MEM_HI: state <= E_IDLE;

        E_EXEC_HI: begin
          if (op_r == OP_EPCW) epc[15:8] <= r;
          if (jump) pc <= next_pc;
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
        if (take_nmi) pc <= 16'h0008;
        else          pc <= 16'h0004;
      end

      // ---------------------------------------------------------------------
      // Instruction dispatch (overrides state, op_r from case block).
      // ir_accept fires in any completing state (insn_completing=1) or
      // E_IDLE, as long as ir_valid && !fetch_flush.
      // ---------------------------------------------------------------------
      if (ir_accept) begin
        pc <= pc + 16'd2;
        // Decode opcode from fetch_ir bit patterns
        if      (fetch_ir[15:12] == 4'b1010)        op_r <= OP_SW;
        else if (fetch_ir[15:12] == 4'b1000)        op_r <= OP_LW;
        else if (fetch_ir[15:9]  == 7'b1011100)     op_r <= OP_JR;
        else if (fetch_ir[15:9]  == 7'b1011101)     op_r <= OP_JALR;
        else if (fetch_ir == 16'b1111111010000001)  op_r <= OP_RETI;
        else if (fetch_ir == 16'b1111111010000010)  op_r <= OP_SEI;
        else if (fetch_ir == 16'b1111111010000011)  op_r <= OP_CLI;
        else if (fetch_ir == 16'b1111111010000100)  op_r <= OP_BRK;
        else if (fetch_ir == 16'b1111111010000101)  op_r <= OP_WAI;
        else if (fetch_ir == 16'b1111111010000110)  op_r <= OP_STP;
        else if (fetch_ir[15:3] == 13'b1111111001110)  op_r <= OP_EPCR;
        else if (fetch_ir[15:3] == 13'b1111111001111)  op_r <= OP_EPCW;
        else if (fetch_ir[15:9] == 7'b1100000)         op_r <= OP_ADD;
        else if (fetch_ir[15:9] == 7'b1100001)         op_r <= OP_SUB;
        else if (fetch_ir[15:9] == 7'b1100010)         op_r <= OP_AND;
        else if (fetch_ir[15:9] == 7'b1100011)         op_r <= OP_OR;
        else if (fetch_ir[15:9] == 7'b1100100)         op_r <= OP_XOR;
        else if (fetch_ir[15:9] == 7'b1100101)         op_r <= OP_SLT;
        else if (fetch_ir[15:9] == 7'b1100110)         op_r <= OP_SLTU;
        else if (fetch_ir[15:9] == 7'b1100111)         op_r <= OP_SLL;
        else if (fetch_ir[15:9] == 7'b1101000)         op_r <= OP_SRL;
        else if (fetch_ir[15:9] == 7'b1101001)         op_r <= OP_SRA;
        else if (fetch_ir[15:7] == 9'b111101100)       op_r <= OP_SLLI;
        else if (fetch_ir[15:7] == 9'b111101101)       op_r <= OP_SRLI;
        else if (fetch_ir[15:7] == 9'b111101110)       op_r <= OP_SRAI;
        else if (fetch_ir[15:9] == 7'b1101110)         op_r <= OP_ADDI;
        else if (fetch_ir[15:9] == 7'b1101111)         op_r <= OP_ANDI;
        else if (fetch_ir[15:9] == 7'b1110000)         op_r <= OP_ORI;
        else if (fetch_ir[15:9] == 7'b1110001)         op_r <= OP_XORI;
        else if (fetch_ir[15:9] == 7'b1110010)         op_r <= OP_LI;
        else if (fetch_ir[15:9] == 7'b1110011)         op_r <= OP_SLTIF;
        else if (fetch_ir[15:9] == 7'b1110100)         op_r <= OP_SLTIUF;
        else if (fetch_ir[15:9] == 7'b1110101)         op_r <= OP_XORIF;
        else if (fetch_ir[15:9] == 7'b1011000)        op_r <= OP_BZ;
        else if (fetch_ir[15:9] == 7'b1011001)        op_r <= OP_BNZ;
        else if (fetch_ir[15:12] == 4'b0100)          op_r <= OP_J;
        else if (fetch_ir[15:12] == 4'b0101)          op_r <= OP_JAL;
        else if (fetch_ir[15:13] == 3'b000)           op_r <= OP_LUI;
        else if (fetch_ir[15:13] == 3'b001)           op_r <= OP_AUIPC;
        else if (fetch_ir[15:12] == 4'b0110)        op_r <= OP_LB;
        else if (fetch_ir[15:12] == 4'b0111)        op_r <= OP_LBU;
        else if (fetch_ir[15:12] == 4'b1001)        op_r <= OP_SB;
        else                                        op_r <= OP_NOP;
        // LUI/AUIPC capture imm10[9:6]; JR/JALR uses rs; others use rs1
        if (fetch_ir[15:14] == 2'b00)
          base_sel_r <= fetch_ir[12:9];
        else if (fetch_ir[15:9] == 7'b1011100 || fetch_ir[15:9] == 7'b1011101)
          base_sel_r <= {1'b0, fetch_ir[2:0]};
        else
          base_sel_r <= {1'b0, fetch_ir[11:9]};
        off6_r       <= fetch_ir[8:3];
        rd_rs2_sel_r <= fetch_ir[2:0];
        // Pre-register regfile read select for first cycle of execution
        if (fetch_ir[15:12] == 4'b1100 ||
            fetch_ir[15:9] == 7'b1101000 ||
            fetch_ir[15:9] == 7'b1101001)
          r_sel_r <= fetch_ir[5:3];          // ALU/shift rs1
        else if (fetch_ir[15:12] == 4'b0110 ||    // LB
                 fetch_ir[15:12] == 4'b0111 ||    // LBU
                 fetch_ir[15:12] == 4'b1000 ||    // LW
                 fetch_ir[15:12] == 4'b1001 ||    // SB
                 fetch_ir[15:12] == 4'b1010)      // SW
          r_sel_r <= fetch_ir[11:9];         // rs1 (base register)
        else
          r_sel_r <= fetch_ir[2:0];          // rd/rs2/rs
        // Right shifts read hi byte first; everything else starts with lo.
        r_hi_r <= (fetch_ir[15:9] == 7'b1101000 ||    // SRL
                   fetch_ir[15:9] == 7'b1101001 ||    // SRA
                   fetch_ir[15:7] == 9'b111101101 ||  // SRLI
                   fetch_ir[15:7] == 9'b111101110)    // SRAI
                  ? 1'b1 : 1'b0;
        // AUIPC, LB, LBU, LW, SB, SW, JR, JALR are multi-cycle; others go to E_EXEC_LO
        state <= (fetch_ir[15:13] == 3'b001 ||
                  fetch_ir[15:12] == 4'b0110 ||
                  fetch_ir[15:12] == 4'b0111 ||
                  fetch_ir[15:12] == 4'b1000 ||
                  fetch_ir[15:12] == 4'b1001 ||
                  fetch_ir[15:12] == 4'b1010 ||
                  fetch_ir[15:9]  == 7'b1011100 ||
                  fetch_ir[15:9]  == 7'b1011101) ? E_ADDR_LO : E_EXEC_LO;
      end
    end
  end

endmodule
