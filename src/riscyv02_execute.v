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
//
// 8-bit register file interface: reads and writes are byte-at-a-time,
// selected by r_hi/w_hi. This eliminates store_data and mem_lo registers;
// bytes are read/written directly during the appropriate cycles.
// =========================================================================
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

  localparam E_IDLE    = 3'd0;  // Waiting for instruction
  localparam E_EXEC    = 3'd1;  // Execute instruction effects
  localparam E_ADDR_LO = 3'd2;  // Computing address low byte
  localparam E_ADDR_HI = 3'd3;  // Computing address high byte
  localparam E_MEM_LO  = 3'd4;  // Memory access low byte
  localparam E_MEM_HI  = 3'd5;  // Memory access high byte (can accept next)

  reg [2:0]  state;
  reg [15:0] MAR;

  // -------------------------------------------------------------------------
  // Interrupt and PC state
  // -------------------------------------------------------------------------
  reg [15:0] pc;       // Program counter (next instruction address)
  reg [15:0] epc;      // Exception PC (bit 0 used for I on save)
  reg        i_bit;    // Interrupt disable flag (0=enabled, 1=disabled)

  // -------------------------------------------------------------------------
  // Decoded instruction state (latched at retire)
  // -------------------------------------------------------------------------
  reg        is_store_r;      // 1 = SW, 0 = LW or JR
  reg        is_jr_r;         // 1 = JR
  reg        is_sei_r;        // 1 = SEI
  reg        is_cli_r;        // 1 = CLI
  reg        is_reti_r;       // 1 = RETI
  reg        is_multicycle_r; // 1 = LW/SW/JR (needs address computation)
  reg [2:0]  base_sel_r;      // Base register selector
  reg [5:0]  off6_r;          // 6-bit offset
  reg [2:0]  rd_rs2_sel_r;    // Destination (LW) or source (SW)

  // -------------------------------------------------------------------------
  // Register file (internal to execute) — 8-bit interface
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
  // Control signals
  // -------------------------------------------------------------------------
  // FSM can accept new work in idle or final cycle of previous instruction
  wire fsm_ready = (state == E_IDLE) || (state == E_MEM_HI);
  assign bus_active = (state == E_MEM_LO) || (state == E_MEM_HI);

  // -------------------------------------------------------------------------
  // Instruction decode (from fetch_ir, stable when ir_valid)
  // -------------------------------------------------------------------------
  wire is_lw = (fetch_ir[15:12] == 4'b1000);
  wire is_sw = (fetch_ir[15:12] == 4'b1010);
  wire is_jr = (fetch_ir[15:9] == 7'b1011100);
  wire is_reti = (fetch_ir == 16'b1111111010000001);
  wire is_sei  = (fetch_ir == 16'b1111111010000010);
  wire is_cli  = (fetch_ir == 16'b1111111010000011);
  wire is_multicycle = is_lw || is_sw || is_jr;

  wire [2:0] ir_base_sel    = is_jr ? fetch_ir[2:0] : fetch_ir[11:9];
  wire [5:0] ir_off6        = fetch_ir[8:3];
  wire [2:0] ir_rd_rs2_sel  = fetch_ir[2:0];

  // -------------------------------------------------------------------------
  // Instruction boundary control flow
  //
  // Hierarchy:
  //   at_boundary: FSM ready and instruction available (something CAN happen)
  //     ├─ take_irq: IRQ hijacks this boundary (instruction NOT consumed)
  //     └─ retire: normal instruction retirement (fetch advances, enter E_EXEC)
  //
  // All instructions enter the FSM via E_EXEC. Instruction-specific effects
  // happen in E_EXEC (for 2-cycle) or later states (for multi-cycle).
  // -------------------------------------------------------------------------
  wire at_boundary = fsm_ready && ir_valid;
  wire irq_pending = !irqb && !i_bit;
  wire take_irq    = at_boundary && irq_pending;
  wire retire      = at_boundary && !take_irq;

  // Output to fetch: instruction consumed (fetch advances to next)
  assign ir_accept = retire;

  // -------------------------------------------------------------------------
  // ALU
  // -------------------------------------------------------------------------
  wire [7:0] alu_a, alu_b;
  wire       alu_start;
  wire [7:0] alu_result;

  riscyv02_alu u_alu (
    .clk    (clk),
    .rst_n  (rst_n),
    .a      (alu_a),
    .b      (alu_b),
    .start  (alu_start),
    .result (alu_result)
  );

  // ALU inputs: compute address as register + sign-extended offset * 2
  // Start ALU on E_ADDR_LO; carry propagates to E_ADDR_HI.
  // E_ADDR_LO: r_hi=0, so r=base_lo
  // E_ADDR_HI: r_hi=1, so r=base_hi
  assign alu_start = (state == E_ADDR_LO);
  assign alu_a = r;
  assign alu_b = (state == E_ADDR_LO) ? {off6_r[5], off6_r, 1'b0} : {8{off6_r[5]}};

  // -------------------------------------------------------------------------
  // Register file interface
  // -------------------------------------------------------------------------
  // Read port mux:
  //   E_ADDR_LO: base_sel_r, hi=0 (base_lo for ALU)
  //   E_ADDR_HI: base_sel_r, hi=1 (base_hi for ALU)
  //   E_MEM_LO (SW): rd_rs2_sel_r, hi=0 (rs2_lo for dout)
  //   E_MEM_HI (SW): rd_rs2_sel_r, hi=1 (rs2_hi for dout)
  assign r_sel = bus_active ? rd_rs2_sel_r : base_sel_r;

  // r_hi: select high byte in E_ADDR_HI and E_MEM_HI
  assign r_hi = (state == E_ADDR_HI) || (state == E_MEM_HI);

  // Write port: fires in E_MEM_LO and E_MEM_HI for loads
  assign w_we   = bus_active && !is_store_r;
  assign w_sel  = rd_rs2_sel_r;
  assign w_hi   = (state == E_MEM_HI);
  assign w_data = uio_in;

  // -------------------------------------------------------------------------
  // Redirect interface (JR, RETI, IRQ entry)
  // -------------------------------------------------------------------------
  wire jr_redirect   = (state == E_ADDR_HI) && is_jr_r;
  wire reti_redirect = (state == E_EXEC) && is_reti_r;

  assign redirect    = jr_redirect || reti_redirect || take_irq;
  assign redirect_pc = take_irq     ? 16'h0004 :
                       reti_redirect ? {epc[15:1], 1'b0} :
                       {alu_result, MAR[7:0]};  // JR

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
        dout = r;  // rs2_lo direct from regfile
        rwb  = !is_store_r;
      end
      E_MEM_HI: begin
        ab   = {MAR[15:1], 1'b1};
        dout = r;  // rs2_hi direct from regfile
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
      // Decoded instruction state
      is_store_r       <= 1'b0;
      is_jr_r          <= 1'b0;
      is_sei_r         <= 1'b0;
      is_cli_r         <= 1'b0;
      is_reti_r        <= 1'b0;
      is_multicycle_r  <= 1'b0;
      base_sel_r       <= 3'b000;
      off6_r           <= 6'b000000;
      rd_rs2_sel_r     <= 3'b000;
      // Interrupt and PC state
      pc               <= 16'h0000;
      epc              <= 16'h0000;
      i_bit            <= 1'b1;  // Interrupts disabled after reset
    end else begin
      // -----------------------------------------------------------------------
      // IRQ entry: save EPC and jump to vector (highest priority)
      // -----------------------------------------------------------------------
      if (take_irq) begin
        epc   <= pc | {15'b0, i_bit};  // Save return address with I bit
        i_bit <= 1'b1;                       // Disable further interrupts
        pc <= 16'h0004;                 // Set up for vector (retire will commit)
        state <= E_IDLE;                     // Return to idle (important if in E_MEM_HI)
      end else begin
        // ---------------------------------------------------------------------
        // Instruction retirement: latch instruction, advance PC, enter E_EXEC
        // ---------------------------------------------------------------------
        if (retire) begin
          // Latch instruction type and operands
          is_store_r      <= is_sw;
          is_jr_r         <= is_jr;
          is_sei_r        <= is_sei;
          is_cli_r        <= is_cli;
          is_reti_r       <= is_reti;
          is_multicycle_r <= is_multicycle;
          base_sel_r      <= ir_base_sel;
          off6_r          <= ir_off6;
          rd_rs2_sel_r    <= ir_rd_rs2_sel;
          // Advance PC sequentially
          pc <= pc + 16'd2;
        end

        // ---------------------------------------------------------------------
        // FSM state transitions and effects
        // ---------------------------------------------------------------------
        case (state)
          E_IDLE:
            if (retire) state <= is_multicycle ? E_ADDR_LO : E_EXEC;

          E_EXEC: begin
            // Apply instruction effects
            if (is_sei_r) i_bit <= 1'b1;
            if (is_cli_r) i_bit <= 1'b0;
            if (is_reti_r) begin
              i_bit   <= epc[0];
              pc <= {epc[15:1], 1'b0};  // Override for redirect
            end
            // Transition based on instruction type
            state <= is_multicycle_r ? E_ADDR_LO : E_IDLE;
          end

          E_ADDR_LO: begin
            MAR[7:0] <= alu_result;
            state    <= E_ADDR_HI;
          end

          E_ADDR_HI: begin
            MAR[15:8] <= alu_result;
            if (is_jr_r) begin
              // JR: redirect fires this cycle, return to idle
              pc <= {alu_result, MAR[7:0]};
              state   <= E_IDLE;
            end else begin
              // LW or SW: proceed to memory access
              state <= E_MEM_LO;
            end
          end

          E_MEM_LO: begin
            // LW: rd_lo written via w_we during this cycle
            // SW: rs2_lo output via dout during this cycle
            state <= E_MEM_HI;
          end

          E_MEM_HI: begin
            // LW: rd_hi written via w_we during this cycle
            // SW: rs2_hi output via dout during this cycle
            // Can pipeline: accept next instruction directly
            if (retire)
              state <= is_multicycle ? E_ADDR_LO : E_EXEC;
            else
              state <= E_IDLE;
          end

          default: state <= E_IDLE;
        endcase
      end
    end
  end

endmodule
