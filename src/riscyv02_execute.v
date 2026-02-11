/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// ============================================================================
// Execute unit: FSM + ALU + register file.
//
// All instruction state is held in a single 16-bit ir register containing the
// raw instruction word (or a synthesized pseudo-instruction for interrupts).
// All decode properties are derived directly from ir bits — no intermediate
// opcode encoding. This makes decode cost transparent for ISA optimization.
//
// All instructions dispatch to E_EXEC_LO, then optionally continue to
// E_EXEC_HI (two-cycle ops). Memory instructions proceed from E_EXEC_HI
// to E_MEM_LO/HI for bus access.
//
// ISA encoding
// ------------
// Bits [15:12] form the "opcode" and determine the instruction format:
//
//   Opcode       Format   Description
//   0000..0011   U        Upper immediate (LUI, AUIPC; 3-bit prefix)
//   0100..0101   J        PC-relative jump (J, JAL)
//   0110..1010   S        Load/store (LB, LBU, LW, SB, SW; rs1 at [11:9])
//   1011..1111   C        Compact (ALU, shift, branch, control, system)
//
// U-format uses a 3-bit prefix [15:13], gaining one extra immediate bit.
// All other formats use the full 4-bit opcode.
//
// Within C-format, bits [14:12] = group and [11:9] = sub identify the
// specific instruction. S-format places rs1 at [11:9], off6 at [8:3].
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
  localparam E_EXEC_LO = 3'd1;  // Execute / address compute low byte
  localparam E_EXEC_HI = 3'd2;  // Execute / address compute high byte
  localparam E_MEM_LO  = 3'd3;  // Memory access low byte
  localparam E_MEM_HI  = 3'd4;  // Memory access high byte (can accept next)

  reg [2:0]  state;
  reg [15:0] ir;        // Instruction register (raw or synthesized for interrupts)
  reg [15:0] tmp;       // Cycle-to-cycle temporary (mem addr, branch target, shift carry)

  // Interrupt and PC state
  reg [15:0] pc;        // Program counter (next instruction to fetch; advanced at dispatch)
  reg        i_bit;     // Interrupt disable flag (0=enabled, 1=disabled)

  // -------------------------------------------------------------------------
  // Instruction decode: all properties derived directly from ir
  //
  // No intermediate "op" encoding — every property is a visible function
  // of ir bits, making decode cost transparent for ISA optimization.
  // -------------------------------------------------------------------------

  // Instruction fields
  wire [5:0] off6 = ir[8:3];

  // Format detection from ir[15:12]
  wire fmt_u = !ir[15] && !ir[14];                       // 00xx: LUI, AUIPC
  wire fmt_j = !ir[15] && ir[14] && !ir[13];             // 010x: J, JAL
  wire fmt_c = ir[15] && (ir[14] || (ir[13] && ir[12])); // 1011..1111
  // S-format is the residual: 0110..1010

  // --- Instruction identity ---

  // U-format
  wire is_lui   = fmt_u && !ir[13];           // 000x
  wire is_auipc = fmt_u && ir[13];            // 001x

  // J-format
  wire is_j   = fmt_j && !ir[12];             // 0100
  wire is_jal = fmt_j && ir[12];              // 0101

  // S-format (individual opcodes from ir[15:12])
  wire is_lb  = ir[15:12] == 4'b0110;
  wire is_lbu = ir[15:12] == 4'b0111;
  wire is_lw  = ir[15:12] == 4'b1000;
  wire is_sb  = ir[15:12] == 4'b1001;
  wire is_sw  = ir[15:12] == 4'b1010;

  // C-format groups (ir[15:12])
  wire is_alu_rr      = ir[15:12] == 4'b1011;  // grp 011
  wire is_shift       = ir[15:12] == 4'b1100;  // grp 100
  wire is_control     = ir[15:12] == 4'b1101;  // grp 101
  wire is_alu_imm_grp = ir[15:12] == 4'b1110;  // grp 110
  wire is_system      = ir[15:12] == 4'b1111;  // grp 111

  // System group (sub = ir[11:9])
  wire is_sei  = is_system && ir[11:9] == 3'b001;
  wire is_cli  = is_system && ir[11:9] == 3'b010;
  wire is_reti = is_system && ir[11:9] == 3'b011;
  wire is_int  = is_system && ir[11:9] == 3'b100;
  wire is_wai  = is_system && ir[11:9] == 3'b101;
  wire is_stp  = is_system && ir[11:9] == 3'b111;

  // Control group (sub = ir[11:9])
  wire is_branch  = is_control && !ir[11];              // sub 0xx
  wire is_li      = is_control && ir[11:9] == 3'b100;
  wire is_jr_jalr = is_control && ir[11] && ir[10];     // sub 11x

  // --- Behavioral properties ---

  localparam LINK_REG = 3'd6;
  localparam T0_REG   = 3'd2;

  // Memory
  wire is_load       = is_lb || is_lbu || is_lw;
  wire is_store      = is_sb || is_sw;
  wire is_mem_addr   = is_load || is_store || is_auipc;
  wire is_byte_load  = is_lb || is_lbu;
  wire is_byte_store = is_sb;

  // Jump/link
  wire is_jump_imm = fmt_j;
  wire is_linking  = is_jal || (is_jr_jalr && ir[9]);   // JAL, JALR

  // Branch properties (sub bits within control group)
  wire is_sign_branch = is_branch && ir[10];             // BLTZ, BGEZ
  wire branch_inv     = ir[9];                           // BNZ/BGEZ invert

  // Shift properties (sub bits within shift group)
  wire is_shift_rr    = is_shift && !ir[11];             // sub[2]=0: register
  wire is_right_shift = is_shift && ir[10];              // sub[1]=1: right
  wire is_arith_shift = is_shift && ir[9];               // sub[0]=1: arithmetic

  // ALU sub-opcode properties (ir[11:9] = sub within ALU groups)
  wire is_slt       = is_alu_rr      && ir[11] && (ir[10] ^ ir[9]);
  wire is_slt_imm   = is_alu_imm_grp && ir[11] && (ir[10] ^ ir[9]);
  wire is_alu_imm   = is_alu_imm_grp && !(ir[11] && (ir[10] ^ ir[9]));
  wire is_fixed_dest = is_alu_imm_grp && ir[11] && |ir[10:9];

  // Two-cycle: everything except system group single-cycle ops
  wire is_two_cycle = !is_system;

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

  wire [2:0] w_sel_mux = is_linking ? LINK_REG :
                         is_fixed_dest ? T0_REG : ir[2:0];

  reg  [2:0] r2_sel_r;  // Registered at dispatch; avoids ir decode on critical dout path
  reg        r2_hi_r;   // Registered; alternates 0/1 across states (lo then hi)

  riscyv02_regfile u_regfile (
    .clk    (clk),
    .rst_n  (rst_n),
    .i_bit  (i_bit),
    .w_sel  (w_sel_mux),
    .w_hi   (w_hi),
    .w_data (w_data),
    .w_we   (w_we),
    .r_sel  (r_sel),
    .r_hi   (r_hi),
    .r      (r),
    .r2_sel (r2_sel_r),
    .r2_hi  (r2_hi_r),
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
  wire [3:0] shamt = is_shift_rr ? r2[3:0] : off6[3:0];

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

  // -------------------------------------------------------------------------
  // Combinational register-file select from (state, ir)
  // -------------------------------------------------------------------------

  // r_sel: read port 1 register select
  //   E_MEM states: read rd/rs2 at ir[2:0]
  //   Execute states: format-dependent source register
  always @(*) begin
    if (state == E_MEM_LO || state == E_MEM_HI)
      r_sel = ir[2:0];
    else if (is_reti)
      r_sel = 3'd6;                                 // Banked R6
    else if (fmt_c &&
             ir[14:12] != 3'b011 &&
             !(ir[14:12] == 3'b100 && !ir[11]))
      r_sel = ir[2:0];                              // C I-type: rd/rs at [2:0]
    else if (fmt_c || fmt_u || fmt_j)
      r_sel = ir[5:3];                              // C R-type, U, J: rs1 at [5:3]
    else
      r_sel = ir[11:9];                             // S-format: rs1 at [11:9]
  end

  // r_hi: read port 1 byte select
  //   E_EXEC_LO: high byte first for right shifts, low byte for everything else
  //   E_EXEC_HI: swapped (low for right shifts, high for everything else)
  //   E_MEM_LO: low byte
  //   E_MEM_HI: high byte (except byte loads re-read low for sign extension)
  always @(*) begin
    case (state)
      E_EXEC_LO: r_hi = is_right_shift;
      E_EXEC_HI: r_hi = !is_right_shift;
      E_MEM_LO:  r_hi = 1'b0;
      E_MEM_HI:  r_hi = !is_byte_load;
      default:   r_hi = 1'b0;
    endcase
  end

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
  assign waiting = (state == E_IDLE) && is_wai;
  assign stopped = (state == E_IDLE) && is_stp;

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
    w_hi            = 1'bx;
    w_data          = uio_in;
    w_we            = 1'b0;
    insn_completing = 1'b0;
    next_pc         = pc;
    jump            = 1'b0;
    shifter_din     = 15'b0;

    // ALU operation select: groups 011/110 share sub-opcode in ir[11:9]
    if (is_alu_rr || is_alu_imm_grp) begin
      if (is_slt || is_slt_imm)
        alu_op = 3'd1;                // SLT variants → SUB
      else if (!ir[11])
        alu_op = ir[11:9];            // ADD=0, SUB=1, AND=2, OR=3
      else
        alu_op = 3'd4;                // XOR, XORI, XORIF
    end

    case (state)
      E_IDLE: ;

      E_EXEC_LO: begin
        if (is_reti) begin
          // Read banked R6 low byte (r_sel=6, r_hi=0, i_bit=1)
          // Captured into tmp[7:0] at negedge
        end else if (is_int) begin
          w_data = pc[7:0];          // Return addr low byte (i_bit in bit 0)
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else if (is_mem_addr) begin
          // Address computation low byte (loads, stores, AUIPC)
          alu_new_op = 1'b1;
          if (is_auipc) begin
            alu_a  = pc[7:0];
            alu_b  = {off6[1:0], 6'b0};             // (imm10 << 6) low byte
            w_data = alu_result;
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end else
            alu_b = {{2{off6[5]}}, off6};            // unscaled byte offset
        end else if (is_jr_jalr) begin
          // JR/JALR address computation low byte
          alu_a      = r;
          alu_b      = {off6[5], off6, 1'b0};        // offset * 2 (code alignment)
          alu_new_op = 1'b1;
          if (is_linking) begin                        // JALR
            w_data = pc[7:0];
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end
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
          alu_b      = {{2{off6[5]}}, off6};
          alu_new_op = 1'b1;
          w_data     = alu_result;
          w_hi       = 1'b0;
          w_we       = 1'b1;
        end else if (is_slt_imm) begin
          alu_b      = {{2{off6[5]}}, off6};
          alu_new_op = 1'b1;
          w_data     = 8'h00;
          w_hi       = 1'b1;
          w_we       = 1'b1;
        end else if (is_shift) begin
          // Cycle 1: left shifts process lo normally, right shifts process hi
          // (r_hi=1 from combinational decode for right shifts, so r = hi byte).
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
        end else if (is_li) begin
          w_data = {{2{off6[5]}}, off6};
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else if (is_lui) begin
          w_data = {off6[1:0], 6'b0};
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else if (is_branch) begin
          alu_a      = pc[7:0];
          alu_b      = {off6[5], off6, 1'b0};
          alu_new_op = 1'b1;
        end else if (is_jump_imm) begin
          alu_a      = pc[7:0];
          alu_b      = {ir[6:0], 1'b0};              // off12[6:0] << 1
          alu_new_op = 1'b1;
          if (is_linking) begin                          // JAL
            w_data = pc[7:0];
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end
        end else begin
          if (!is_wai && !is_stp)
            insn_completing = 1'b1;
        end
      end

      E_EXEC_HI: begin
        if (is_mem_addr) begin
          // Address computation high byte (loads, stores, AUIPC)
          alu_new_op = 1'b0;
          if (is_auipc) begin
            alu_a           = pc[15:8];
            alu_b           = ir[12:5];              // (imm10 << 6) high byte
            w_data          = alu_result;
            w_hi            = 1'b1;
            w_we            = 1'b1;
            insn_completing = 1'b1;
          end else
            alu_b = {8{off6[5]}};  // sign extension
        end else if (is_jr_jalr) begin
          // JR/JALR address computation high byte
          alu_b           = {8{off6[5]}};
          alu_new_op      = 1'b0;
          jump            = 1'b1;
          next_pc         = {alu_result, tmp[7:0]};
          insn_completing = 1'b1;
          if (is_linking) begin                          // JALR
            w_data = pc[15:8];
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end
        end else begin
          if (is_reti) begin
            // Hi byte from current read (r_sel=6, r_hi=1), lo from tmp
            jump    = 1'b1;
            next_pc = {r, tmp[7:1], 1'b0};
          end else if (is_int) begin
            w_data  = pc[15:8];
            w_hi    = 1'b1;
            w_we    = 1'b1;
            jump    = 1'b1;
            next_pc = {12'b0, off6[1:0] + 2'd1, 2'b00};  // Vector address (skip reset at 0)
          end else begin
          // Execute high byte (ALU, shift, branch, jump, LI, LUI)
          insn_completing = 1'b1;
          if (is_alu_rr) begin
            alu_b      = r2;
            alu_new_op = 1'b0;
            w_data     = alu_result;
            w_hi       = 1'b1;
            w_we       = 1'b1;
            if (is_slt) begin
              w_hi = 1'b0;
              if (ir[10])                                // SLTU (sub[1])
                w_data = {7'b0, ~alu_co};
              else
                w_data = {7'b0, (r[7] ^ r2[7]) ? r[7] : alu_result[7]};
            end
          end else if (is_alu_imm) begin
            alu_b      = {8{off6[5]}};
            alu_new_op = 1'b0;
            w_data     = alu_result;
            w_hi       = 1'b1;
            w_we       = 1'b1;
          end else if (is_slt_imm) begin
            alu_b      = {8{off6[5]}};
            alu_new_op = 1'b0;
            w_hi       = 1'b0;
            w_we       = 1'b1;
            if (ir[10])                                   // SLTIUF (sub[1])
              w_data = {7'b0, ~alu_co};
            else
              w_data = {7'b0, (r[7] ^ off6[5]) ? r[7] : alu_result[7]};
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
          end else if (is_li) begin
            w_data = {8{off6[5]}};
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end else if (is_lui) begin
            w_data = ir[12:5];                         // {imm10[9:6], imm10[5:2]}
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end else if (is_branch) begin
            alu_a      = pc[15:8];
            alu_b      = {8{off6[5]}};
            alu_new_op = 1'b0;
            if ((is_sign_branch ? r[7] : !tmp[8] && r == 8'h00) ^ branch_inv) begin
              jump    = 1'b1;
              next_pc = {alu_result, tmp[7:0]};
            end
          end else if (is_jump_imm) begin
            alu_a      = pc[15:8];
            alu_b      = {{3{ir[11]}}, ir[11:7]};     // sext(off12[11:7])
            alu_new_op = 1'b0;
            jump       = 1'b1;
            next_pc    = {alu_result, tmp[7:0]};
            if (is_linking) begin                              // JAL
              w_data = pc[15:8];
              w_hi   = 1'b1;
              w_we   = 1'b1;
            end
          end
          end // inner else (insn_completing path)
        end // outer else (not mem, not jr_jalr)
      end

      E_MEM_LO: begin
        bus_active   = 1'b1;
        ab           = tmp;
        w_hi         = 1'b0;
        w_we         = !is_store;
        if (is_byte_store)                              // SB
          insn_completing = 1'b1;
      end

      E_MEM_HI: begin
        insn_completing = 1'b1;
        w_hi            = 1'b1;
        bus_active      = !is_byte_load;
        ab              = {tmp[15:8] + {7'b0, ~|tmp[7:0]}, tmp[7:0]};
        if (is_byte_load) begin
          w_data        = is_lbu ? 8'h00 : {8{r[7]}}; // LBU : LB
          w_we          = 1'b1;
        end else
          w_we          = !is_store;
      end

      default: ;
    endcase

    case (state)
      E_MEM_LO, E_MEM_HI: begin
        dout = r2;  // rs2 via port 2 (low-fanout path to uio_out)
        rwb  = !is_store;
      end
      default: ;
    endcase

    // Flush: interrupts or instruction jump (JR, RETI, BRK).
    fetch_flush = take_nmi || take_irq || jump;
  end

  // ==========================================================================
  // FSM (negedge clk)
  // ==========================================================================

  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state    <= E_IDLE;
      ir       <= 16'h0000;
      tmp      <= 16'h0000;
      pc       <= 16'h0000;
      i_bit    <= 1'b1;  // Interrupts disabled after reset
      nmi_ack  <= 1'b0;
      r2_hi_r  <= 1'b0;
    end else begin
      // NMI handshake: set nmi_ack when NMI is taken; hold until project.v
      // clears nmi_pending, then release.
      if (!nmi_pending) nmi_ack <= 1'b0;
      else if (take_nmi) nmi_ack <= 1'b1;

      // ---------------------------------------------------------------------
      // State machine: transitions and per-state effects.
      // Ordered before interrupt entry and dispatch so their overrides
      // (state, i_bit, pc, ir) take priority via last-NBA-wins.
      // ---------------------------------------------------------------------
      case (state)
        E_IDLE: ;

        E_EXEC_LO: begin
          if (is_sei) i_bit <= 1'b1;
          if (is_cli) i_bit <= 1'b0;
          if (is_reti) tmp[7:0] <= r;   // Capture banked R6 low byte
          if (is_branch || is_jump_imm || is_mem_addr || is_jr_jalr)
            tmp[7:0] <= alu_result;
          if (is_branch) tmp[8] <= |r;  // nz_lo
          if (is_shift) tmp[7:0] <= r;
          // RETI and INT are two-cycle (system group, not is_two_cycle)
          if (is_reti || is_int || is_two_cycle) begin
            if (!is_shift) r2_hi_r <= 1'b1;
            state <= E_EXEC_HI;
          end else
            state <= E_IDLE;
        end

        E_EXEC_HI: begin
          if (is_mem_addr) begin
            // Address high byte computed; set up for memory access or complete.
            tmp[15:8] <= alu_result;
            r2_hi_r   <= 1'b0;
            state     <= (is_auipc) ? E_IDLE : E_MEM_LO;
          end else if (is_jr_jalr) begin
            pc    <= next_pc;
            state <= E_IDLE;
          end else begin
            if (is_reti) i_bit <= tmp[0];
            if (jump) pc <= next_pc;
            state <= E_IDLE;
          end
        end

        E_MEM_LO: begin
          tmp[7:0] <= tmp[7:0] + 8'd1;  // Increment for E_MEM_HI address
          r2_hi_r  <= 1'b1;
          state    <= is_byte_store ? E_IDLE : E_MEM_HI;
        end

        E_MEM_HI: state <= E_IDLE;

        default: state <= 3'bx;
      endcase

      // ---------------------------------------------------------------------
      // Interrupt entry: synthesize an INT instruction in ir.
      // ir = {7'b1111100, vector_id[5:0], 3'd6} decodes as is_int with
      // off6 = vector_id and write dest = R6 (banked).
      // ---------------------------------------------------------------------
      if (take_nmi || take_irq) begin
        ir    <= {7'b1111100, 5'b00000, take_nmi, 3'd6};  // IRQ=0, NMI=1
        pc[0] <= i_bit;            // Stash old I flag in pc bit 0
        i_bit <= 1'b1;
        state <= E_EXEC_LO;
      end

      // ---------------------------------------------------------------------
      // Instruction dispatch: load ir from fetch and advance PC.
      // INT instructions (including BRK) set I=1 and stash old I in pc[0].
      // ---------------------------------------------------------------------
      if (ir_accept) begin
        pc <= pc + 16'd2;
        ir <= fetch_ir;
        // Stores (SB=1001, SW=1010) have rs2 at [2:0]; all others at [8:6].
        // Registered here to break ir decode → regfile → dout critical path.
        r2_sel_r <= (fetch_ir[15:12] == 4'b1001 || fetch_ir[15:12] == 4'b1010) ?
                    fetch_ir[2:0] : fetch_ir[8:6];
        r2_hi_r  <= 1'b0;
        if (fetch_ir[15:9] == 7'b1111100) begin
          pc[0] <= i_bit;         // Stash I flag
          i_bit <= 1'b1;
        end
        state <= E_EXEC_LO;
      end
    end
  end

endmodule
