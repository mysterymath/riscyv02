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
// ISA encoding: variable-width prefix-free encoding
// -------------------------------------------------
// Prefix at MSB, registers at LSB for fixed positions.
//
//   Level  Format  Layout                             Instructions
//   5      R,8     [prefix:5|imm8:8|reg:3]            17: ADDI..XORIF
//   6      R,7     [prefix:6|imm7:7|reg:3]            2: LUI,AUIPC
//   6      "10"    [prefix:6|imm10:10]                 2: J,JAL
//   7      R,R,R   [prefix:7|rd:3|rs2:3|rs1:3]       10: ADD..SRA
//   9      R,4     [prefix:9|shamt:4|reg:3]            3: SLLI,SRLI,SRAI
//  10      R,R     [prefix:10|rd:3|rs:3]              10: LW.RR..SB.A
//  10+     System  [prefix:10|sub:6]                   6: SEI..STP
//
// ADDI has prefix 0000 so that 0x0000 = ADDI R0, 0 = NOP.
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
  reg        bus_active_r;  // Registered bus_active for E_MEM_HI (set at E_MEM_LO transition)

  // Interrupt and PC state
  reg [15:0] pc;        // Program counter (next instruction to fetch; advanced at dispatch)
  reg        i_bit;     // Interrupt disable flag (0=enabled, 1=disabled)

  // -------------------------------------------------------------------------
  // Instruction decode: all properties derived directly from ir
  // -------------------------------------------------------------------------

  // --- R,8 format (5-bit prefix @ [15:11]) ---
  wire is_addi = ir[15:11] == 5'b00000;
  wire is_li   = ir[15:11] == 5'b00001;
  wire is_lw   = ir[15:11] == 5'b00010;
  wire is_lb   = ir[15:11] == 5'b00011;
  wire is_lbu  = ir[15:11] == 5'b00100;
  wire is_sw   = ir[15:11] == 5'b00101;
  wire is_sb   = ir[15:11] == 5'b00110;
  wire is_jr   = ir[15:11] == 5'b00111;
  wire is_jalr = ir[15:11] == 5'b01000;


  wire is_andi  = ir[15:11] == 5'b01001;
  wire is_ori   = ir[15:11] == 5'b01010;
  wire is_xori  = ir[15:11] == 5'b01011;
  wire is_slti  = ir[15:11] == 5'b01100;
  wire is_sltui = ir[15:11] == 5'b01101;
  wire is_bz    = ir[15:11] == 5'b01110;
  wire is_bnz   = ir[15:11] == 5'b01111;
  wire is_xorif = ir[15:11] == 5'b10000;

  // --- R,7 / "10" format (6-bit prefix @ [15:10]) ---
  wire is_lui   = ir[15:10] == 6'b110100;
  wire is_auipc = ir[15:10] == 6'b110101;
  wire is_j     = ir[15:10] == 6'b110110;
  wire is_jal   = ir[15:10] == 6'b110111;

  // --- R,R,R format (7-bit prefix @ [15:9]) ---
  wire is_add  = ir[15:9] == 7'b1110000;
  wire is_sub  = ir[15:9] == 7'b1110001;
  wire is_and  = ir[15:9] == 7'b1110010;
  wire is_or   = ir[15:9] == 7'b1110011;
  wire is_xor  = ir[15:9] == 7'b1110100;
  wire is_slt  = ir[15:9] == 7'b1110101;
  wire is_sltu = ir[15:9] == 7'b1110110;
  wire is_sll  = ir[15:9] == 7'b1110111;
  wire is_srl  = ir[15:9] == 7'b1111000;
  wire is_sra  = ir[15:9] == 7'b1111001;

  // --- R,4 format (9-bit prefix @ [15:7]) ---
  wire is_slli = ir[15:7] == 9'b111101000;
  wire is_srli = ir[15:7] == 9'b111101001;
  wire is_srai = ir[15:7] == 9'b111101010;

  // --- R,R format (10-bit prefix @ [15:6]) ---
  wire is_lw_rr  = ir[15:6] == 10'b1111010110;
  wire is_lb_rr  = ir[15:6] == 10'b1111010111;
  wire is_lbu_rr = ir[15:6] == 10'b1111011000;
  wire is_sw_rr  = ir[15:6] == 10'b1111011001;
  wire is_sb_rr  = ir[15:6] == 10'b1111011010;
  wire is_lw_a   = ir[15:6] == 10'b1111011011;
  wire is_lb_a   = ir[15:6] == 10'b1111011100;
  wire is_lbu_a  = ir[15:6] == 10'b1111011101;
  wire is_sw_a   = ir[15:6] == 10'b1111011110;
  wire is_sb_a   = ir[15:6] == 10'b1111011111;

  // --- System format (10-bit prefix @ [15:6] + sub @ [5:0]) ---
  wire is_system_grp = ir[15:6] == 10'b1111100000;
  wire is_sei  = is_system_grp && ir[5:0] == 6'b000001;
  wire is_cli  = is_system_grp && ir[5:0] == 6'b000010;
  wire is_reti = is_system_grp && ir[5:0] == 6'b000011;
  wire is_int  = is_system_grp && ir[5];                  // sub[5]=1: INT
  wire is_wai  = is_system_grp && ir[5:0] == 6'b000101;
  wire is_stp  = is_system_grp && ir[5:0] == 6'b000111;

  // --- Behavioral groups ---

  localparam LINK_REG = 3'd6;

  // Memory groups
  wire is_r9_load  = is_lw || is_lb || is_lbu;
  wire is_r9_store = is_sw || is_sb;
  wire is_rr_load  = is_lw_rr || is_lb_rr || is_lbu_rr;
  wire is_rr_store = is_sw_rr || is_sb_rr;
  wire is_auto_load  = is_lw_a || is_lb_a || is_lbu_a;
  wire is_auto_store = is_sw_a || is_sb_a;
  wire is_auto_mem = is_auto_load || is_auto_store;

  // Combined memory properties for E_MEM and r_hi
  wire mem_is_store      = is_r9_store || is_rr_store || is_auto_store;
  wire mem_is_byte_load  = is_lb || is_lbu || is_lb_rr || is_lbu_rr || is_lb_a || is_lbu_a;
  wire mem_is_byte_store = is_sb || is_sb_rr || is_sb_a;
  wire mem_is_lbu        = is_lbu || is_lbu_rr || is_lbu_a;

  // R,R,R group
  wire is_rrr = is_add || is_sub || is_and || is_or || is_xor
              || is_slt || is_sltu || is_sll || is_srl || is_sra;
  wire is_alu_rrr  = is_add || is_sub || is_and || is_or || is_xor;
  wire is_slt_rrr  = is_slt || is_sltu;
  wire is_shift_rr = is_sll || is_srl || is_sra;

  // Shift groups
  wire is_shift_imm   = is_slli || is_srli || is_srai;
  wire is_shift       = is_shift_rr || is_shift_imm;
  wire is_right_shift = is_srl || is_sra || is_srli || is_srai;
  wire is_arith_shift = is_sra || is_srai;

  // Writes to R0: SLTI, SLTUI, XORIF
  wire is_r0_dest = is_slti || is_sltui || is_xorif;

  // Jump/branch
  wire is_branch   = is_bz || is_bnz;
  wire is_jump_imm = is_j || is_jal;
  wire is_jr_jalr  = is_jr || is_jalr;
  wire is_linking  = is_jal || is_jalr;

  // System single-cycle ops (SEI, CLI complete in E_EXEC_LO; WAI/STP hold in E_IDLE)
  wire is_system_1cyc = is_sei || is_cli || is_wai || is_stp;

  // ==========================================================================
  // Shared Infrastructure
  // ==========================================================================

  // -------------------------------------------------------------------------
  // Register file (8-bit interface)
  // -------------------------------------------------------------------------
  reg  [2:0] r1_sel;
  reg        r1_hi;
  wire [7:0] r1;
  wire [7:0] r2;
  reg        w_hi;
  reg        w_we;
  reg  [7:0] w_data;

  wire is_mem_phase = (state == E_MEM_LO || state == E_MEM_HI);

  // w_sel: write port register select
  reg [2:0] w_sel_mux;
  always @(*) begin
    if (is_jal || is_int)
      w_sel_mux = LINK_REG;                                    // JAL/INT → R6
    else if (is_rrr)
      w_sel_mux = ir[8:6];                                     // R,R,R: rd at [8:6]
    else if (is_mem_phase && (is_rr_load || is_auto_load))
      w_sel_mux = ir[5:3];                                     // R,R/auto loads: rd at [5:3]
    else if ((is_mem_phase && is_r9_load) || is_r0_dest)
      w_sel_mux = 3'd0;                                        // R,9 loads/SLTI/SLTUI/XORIF → R0
    else
      w_sel_mux = ir[2:0];                                     // Default: reg at [2:0]
  end

  // r2_sel: read port 2 register select
  //   Default ir[5:3] works for R,R,R (rs2) and R,R loads/stores (rd/data).
  //   Override to R0 for R,8-format memory (implicit data/dest = R0).
  wire [2:0] r2_sel = (is_r9_load || is_r9_store) ? 3'd0 : ir[5:3];
  reg        r2_hi_r;   // Registered; alternates 0/1 across states (lo then hi)

  riscyv02_regfile u_regfile (
    .clk    (clk),
    .rst_n  (rst_n),
    .i_bit  (i_bit),
    .w_sel  (w_sel_mux),
    .w_hi   (w_hi),
    .w_data (w_data),
    .w_we   (w_we),
    .r1_sel (r1_sel),
    .r1_hi  (r1_hi),
    .r1     (r1),
    .r2_sel (r2_sel),
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
  wire [3:0] shamt = is_shift_rr ? r2[3:0] : ir[6:3];

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

  // r1_sel: read port 1 register select
  //   Default ir[2:0] works for all formats: reg/rs1/rs is always at [2:0].
  //   Sign-extension readback for loads uses port 2.
  always @(*) begin
    if (is_reti)
      r1_sel = 3'd6;                                   // Banked R6
    else
      r1_sel = ir[2:0];                                // Default: reg at [2:0]
  end

  // r1_hi: read port 1 byte select
  always @(*) begin
    case (state)
      E_EXEC_LO: r1_hi = is_right_shift;
      E_EXEC_HI: r1_hi = !is_right_shift;
      E_MEM_LO:  r1_hi = 1'b0;
      E_MEM_HI:  r1_hi = bus_active_r;
      default:   r1_hi = 1'b0;
    endcase
  end

  // -------------------------------------------------------------------------
  // State-driven signals (computed in state-property block below)
  // -------------------------------------------------------------------------
  reg        insn_completing;
  reg [15:0] next_pc;
  reg        jump;

  // Interrupt control
  wire fsm_ready = state == E_IDLE || insn_completing;
  wire take_nmi = fsm_ready && (nmi_pending || nmi_edge) && !nmi_ack;
  wire take_irq = fsm_ready && !irqb && !i_bit && !take_nmi;
  assign ir_accept      = fsm_ready && ir_valid && !fetch_flush;
  assign waiting = (state == E_IDLE) && is_wai;
  assign stopped = (state == E_IDLE) && is_stp;

  // ==========================================================================
  // State-Property Block
  // ==========================================================================

  assign fetch_pc = pc;

  always @(*) begin
    // Defaults
    bus_active      = 1'b0;
    ab              = 16'bx;
    dout            = 8'bx;
    rwb             = 1'bx;
    alu_a           = r1;
    alu_new_op      = 1'bx;
    alu_b           = 8'bx;
    alu_op          = 3'd0;    // ADD
    w_hi            = 1'bx;
    w_data          = uio_in;
    w_we            = 1'b0;
    insn_completing = 1'b0;
    next_pc         = pc;
    jump            = 1'b0;
    shifter_din     = 15'b0;

    case (state)
      E_IDLE: ;

      E_EXEC_LO: begin
        if (is_reti) begin
          // Read banked R6 low byte (r_sel=6, r_hi=0, i_bit=1)
          // Captured into tmp[7:0] at negedge
        end else if (is_int) begin
          w_data = pc[7:0];
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else if (is_r9_load || is_r9_store) begin
          // Address: base + sext(imm8), byte offset (no shift)
          alu_b      = ir[10:3];            // imm[7:0]
          alu_new_op = 1'b1;
        end else if (is_auipc) begin
          // pc + (sext(imm7) << 9): lo byte is pc + 0
          alu_a      = pc[7:0];
          alu_b      = 8'h00;
          alu_new_op = 1'b1;
          w_data     = alu_result;
          w_hi       = 1'b0;
          w_we       = 1'b1;
        end else if (is_rr_load || is_rr_store) begin
          // Address = rs, no offset
          alu_b      = 8'd0;
          alu_new_op = 1'b1;
        end else if (is_auto_mem) begin
          alu_new_op = 1'b1;
          alu_op     = is_auto_store ? 3'd1 : 3'd0;
          alu_b      = (is_lb_a || is_lbu_a || is_sb_a) ? 8'd1 : 8'd2;
          w_data     = alu_result;
          w_hi       = 1'b0;
          w_we       = 1'b1;
        end else if (is_jr_jalr) begin
          // JR/JALR: rs + sext(imm8) << 1
          alu_b      = {ir[9:3], 1'b0};    // imm[6:0] << 1
          alu_new_op = 1'b1;
          if (is_jalr) begin
            w_data = pc[7:0];
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end
        end else if (is_addi) begin
          alu_b      = ir[10:3];            // imm[7:0]
          alu_new_op = 1'b1;
          w_data     = alu_result;
          w_hi       = 1'b0;
          w_we       = 1'b1;
        end else if (is_li) begin
          w_data = ir[10:3];                // imm[7:0]
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else if (is_alu_rrr) begin
          alu_op     = ir[11:9];            // ADD=0, SUB=1, AND=2, OR=3, XOR=4
          alu_b      = r2;
          alu_new_op = 1'b1;
          w_data     = alu_result;
          w_hi       = 1'b0;
          w_we       = 1'b1;
        end else if (is_slt_rrr) begin
          alu_op     = 3'd1;                // SUB for comparison
          alu_b      = r2;
          alu_new_op = 1'b1;
          w_data     = 8'h00;              // Clear hi byte first
          w_hi       = 1'b1;
          w_we       = 1'b1;
        end else if (is_andi) begin
          alu_op     = 3'd2;
          alu_b      = ir[10:3];            // imm8 (zero-extended: hi byte = 0 in HI)
          alu_new_op = 1'b1;
          w_data     = alu_result;
          w_hi       = 1'b0;
          w_we       = 1'b1;
        end else if (is_ori) begin
          alu_op     = 3'd3;
          alu_b      = ir[10:3];
          alu_new_op = 1'b1;
          w_data     = alu_result;
          w_hi       = 1'b0;
          w_we       = 1'b1;
        end else if (is_xori || is_xorif) begin
          alu_op     = 3'd4;
          alu_b      = ir[10:3];
          alu_new_op = 1'b1;
          w_data     = alu_result;
          w_hi       = 1'b0;
          w_we       = 1'b1;
        end else if (is_slti || is_sltui) begin
          alu_op     = 3'd1;                // SUB
          alu_b      = ir[10:3];            // imm8 low byte
          alu_new_op = 1'b1;
          w_data     = 8'h00;              // Clear hi byte first
          w_hi       = 1'b1;
          w_we       = 1'b1;
        end else if (is_shift) begin
          if (shamt[3]) begin
            // Cross-byte: entire result comes from the other byte.
            w_data = is_right_shift ?
                     (is_arith_shift ? {8{r1[7]}} : 8'h00) : 8'h00;
            w_hi   = is_right_shift ? 1'b1 : 1'b0;
            w_we   = 1'b1;
          end else if (is_right_shift) begin
            // Right shift hi byte: fill from sign/zero
            shifter_din = {is_arith_shift ? {7{r1[7]}} : 7'b0, r1};
            w_data = shifter_result;
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end else begin
            // Left shift lo byte: reverse, right-shift, reverse
            shifter_din = {7'b0, rev8(r1)};
            w_data = rev8(shifter_result);
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end
        end else if (is_lui) begin
          w_data = 8'h00;                   // lo byte = 0 (sext(imm7) << 9)
          w_hi   = 1'b0;
          w_we   = 1'b1;
        end else if (is_branch) begin
          alu_a      = pc[7:0];
          alu_b      = {ir[9:3], 1'b0};     // sext(off8)[6:0] << 1
          alu_new_op = 1'b1;
        end else if (is_jump_imm) begin
          alu_a      = pc[7:0];
          alu_b      = {ir[6:0], 1'b0};     // off10[6:0] << 1
          alu_new_op = 1'b1;
          if (is_jal) begin
            w_data = pc[7:0];
            w_hi   = 1'b0;
            w_we   = 1'b1;
          end
        end else begin
          // System single-cycle (SEI, CLI, WAI, STP)
          if (!is_wai && !is_stp)
            insn_completing = 1'b1;
        end
      end

      E_EXEC_HI: begin
        if (is_r9_load || is_r9_store) begin
          // Address high byte: sign-extend imm bit 7
          alu_b      = {8{ir[10]}};
          alu_new_op = 1'b0;
        end else if (is_auipc) begin
          alu_a           = pc[15:8];
          alu_b           = {ir[9:3], 1'b0};    // (sext(imm7) << 9) hi byte
          alu_new_op      = 1'b0;
          w_data          = alu_result;
          w_hi            = 1'b1;
          w_we            = 1'b1;
          insn_completing = 1'b1;
        end else if (is_rr_load || is_rr_store) begin
          // Address high byte: carry propagation only
          alu_b      = 8'd0;
          alu_new_op = 1'b0;
        end else if (is_auto_mem) begin
          alu_new_op = 1'b0;
          alu_op     = is_auto_store ? 3'd1 : 3'd0;
          alu_b      = 8'd0;
          w_data     = alu_result;
          w_hi       = 1'b1;
          w_we       = 1'b1;
        end else if (is_jr_jalr) begin
          // JR/JALR high byte: sign-extend imm bit 7
          alu_b           = {8{ir[10]}};
          alu_new_op      = 1'b0;
          jump            = 1'b1;
          next_pc         = {alu_result, tmp[7:0]};
          insn_completing = 1'b1;
          if (is_jalr) begin
            w_data = pc[15:8];
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end
        end else begin
          if (is_reti) begin
            jump    = 1'b1;
            next_pc = {r1, tmp[7:1], 1'b0};
          end else if (is_int) begin
            w_data  = pc[15:8];
            w_hi    = 1'b1;
            w_we    = 1'b1;
            jump    = 1'b1;
            next_pc = {13'b0, ir[1:0] + 2'd1, 1'b0};
          end else begin
          // Execute high byte: completes this cycle
          insn_completing = 1'b1;
          if (is_addi) begin
            alu_b      = {8{ir[10]}};           // sign-extend imm bit 7
            alu_new_op = 1'b0;
            w_data     = alu_result;
            w_hi       = 1'b1;
            w_we       = 1'b1;
          end else if (is_li) begin
            w_data = {8{ir[10]}};               // sign-extend imm bit 7
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end else if (is_alu_rrr) begin
            alu_op     = ir[11:9];
            alu_b      = r2;
            alu_new_op = 1'b0;
            w_data     = alu_result;
            w_hi       = 1'b1;
            w_we       = 1'b1;
          end else if (is_slt_rrr) begin
            alu_op     = 3'd1;
            alu_b      = r2;
            alu_new_op = 1'b0;
            w_hi       = 1'b0;
            w_we       = 1'b1;
            if (is_sltu)
              w_data = {7'b0, ~alu_co};
            else
              w_data = {7'b0, (r1[7] ^ r2[7]) ? r1[7] : alu_result[7]};
          end else if (is_andi) begin
            alu_op     = 3'd2;
            alu_b      = 8'h00;                 // zero-extend
            alu_new_op = 1'b0;
            w_data     = alu_result;
            w_hi       = 1'b1;
            w_we       = 1'b1;
          end else if (is_ori) begin
            alu_op     = 3'd3;
            alu_b      = 8'h00;
            alu_new_op = 1'b0;
            w_data     = alu_result;
            w_hi       = 1'b1;
            w_we       = 1'b1;
          end else if (is_xori || is_xorif) begin
            alu_op     = 3'd4;
            alu_b      = 8'h00;
            alu_new_op = 1'b0;
            w_data     = alu_result;
            w_hi       = 1'b1;
            w_we       = 1'b1;
          end else if (is_slti) begin
            alu_op     = 3'd1;
            alu_b      = {8{ir[10]}};           // sign-extend imm8 bit 7
            alu_new_op = 1'b0;
            w_hi       = 1'b0;
            w_we       = 1'b1;
            w_data     = {7'b0, (r1[7] ^ ir[10]) ? r1[7] : alu_result[7]};
          end else if (is_sltui) begin
            alu_op     = 3'd1;
            alu_b      = {8{ir[10]}};           // sign-extend for unsigned comparison
            alu_new_op = 1'b0;
            w_hi       = 1'b0;
            w_we       = 1'b1;
            w_data     = {7'b0, ~alu_co};
          end else if (is_shift) begin
            if (shamt[3]) begin
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
              shifter_din = {tmp[6:0], r1};
              w_data = shifter_result;
              w_hi   = 1'b0;
              w_we   = 1'b1;
            end else begin
              shifter_din = {rev7(tmp[7:1]), rev8(r1)};
              w_data = rev8(shifter_result);
              w_hi   = 1'b1;
              w_we   = 1'b1;
            end
          end else if (is_lui) begin
            w_data = {ir[9:3], 1'b0};           // (sext(imm7) << 9) hi byte
            w_hi   = 1'b1;
            w_we   = 1'b1;
          end else if (is_branch) begin
            alu_a      = pc[15:8];
            alu_b      = {8{ir[10]}};           // sign-extend off8 bit 7
            alu_new_op = 1'b0;
            // BZ/BNZ: only zero/nonzero test (no sign branches)
            if ((!tmp[8] && r1 == 8'h00) ^ is_bnz) begin
              jump    = 1'b1;
              next_pc = {alu_result, tmp[7:0]};
            end
          end else if (is_jump_imm) begin
            alu_a      = pc[15:8];
            alu_b      = {{6{ir[9]}}, ir[8], ir[7]};  // sext(off10[9:7])
            alu_new_op = 1'b0;
            jump       = 1'b1;
            next_pc    = {alu_result, tmp[7:0]};
            if (is_jal) begin
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
        w_we         = !mem_is_store;
        if (mem_is_byte_store)
          insn_completing = 1'b1;
      end

      E_MEM_HI: begin
        insn_completing = 1'b1;
        w_hi            = 1'b1;
        bus_active      = bus_active_r;
        ab              = {tmp[15:8] + {7'b0, ~|tmp[7:0]}, tmp[7:0]};
        if (!bus_active_r) begin
          w_data        = mem_is_lbu ? 8'h00 : {8{r2[7]}};
          w_we          = 1'b1;
        end else
          w_we          = !mem_is_store;
      end

      default: ;
    endcase

    case (state)
      E_MEM_LO, E_MEM_HI: begin
        dout = r2;  // Data via port 2 (low-fanout path to uio_out)
        rwb  = !mem_is_store;
      end
      default: ;
    endcase

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
      i_bit    <= 1'b1;
      nmi_ack  <= 1'b0;
      r2_hi_r      <= 1'b0;
      bus_active_r <= 1'b0;
    end else begin
      // NMI handshake: set has priority (take_nmi fires via nmi_edge
      // before nmi_pending is registered, so nmi_pending may still be 0).
      if (take_nmi) nmi_ack <= 1'b1;
      else if (!nmi_pending) nmi_ack <= 1'b0;

      // -----------------------------------------------------------------
      // State machine
      // -----------------------------------------------------------------
      case (state)
        E_IDLE: ;

        E_EXEC_LO: begin
          if (is_sei) i_bit <= 1'b1;
          if (is_cli) i_bit <= 1'b0;
          if (is_reti) tmp[7:0] <= r1;
          if (is_r9_load || is_r9_store || is_auipc || is_rr_load || is_rr_store
              || is_jr_jalr || is_branch || is_jump_imm || is_auto_mem)
            tmp[7:0] <= is_auto_load ? r1 : alu_result;
          if (is_branch) tmp[8] <= |r1;       // nz_lo for BZ/BNZ
          if (is_shift) tmp[7:0] <= r1;
          if (!is_system_1cyc) begin
            if (!is_shift) r2_hi_r <= 1'b1;
            state <= E_EXEC_HI;
          end else
            state <= E_IDLE;
        end

        E_EXEC_HI: begin
          if (is_r9_load || is_r9_store || is_auipc || is_rr_load || is_rr_store) begin
            tmp[15:8] <= alu_result;
            r2_hi_r   <= 1'b0;
            state     <= is_auipc ? E_IDLE : E_MEM_LO;
          end else if (is_auto_mem) begin
            tmp[15:8] <= is_auto_load ? r1 : alu_result;
            r2_hi_r   <= 1'b0;
            state     <= E_MEM_LO;
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
          tmp[7:0]     <= tmp[7:0] + 8'd1;
          r2_hi_r      <= mem_is_store;  // Stores: hi byte for dout; loads: lo byte for sign ext
          bus_active_r <= !mem_is_byte_load;
          state        <= mem_is_byte_store ? E_IDLE : E_MEM_HI;
        end

        E_MEM_HI: state <= E_IDLE;

        default: state <= 3'bx;
      endcase

      // -----------------------------------------------------------------
      // Interrupt entry: synthesize INT instruction in ir.
      // System prefix + sub[5]=1, vector in ir[1:0].
      // -----------------------------------------------------------------
      if (take_nmi || take_irq) begin
        ir    <= {10'b1111100000, 1'b1, 3'b000, !take_nmi, 1'b0};
        pc[0] <= i_bit;
        i_bit <= 1'b1;
        state <= E_EXEC_LO;
      end

      // -----------------------------------------------------------------
      // Instruction dispatch
      // -----------------------------------------------------------------
      if (ir_accept) begin
        pc <= pc + 16'd2;
        ir <= fetch_ir;
        r2_hi_r  <= 1'b0;
        // BRK/INT detection: system prefix + sub[5]=1
        if (fetch_ir[15:6] == 10'b1111100000 && fetch_ir[5]) begin
          pc[0] <= i_bit;
          i_bit <= 1'b1;
        end
        state <= E_EXEC_LO;
      end
    end
  end

endmodule
