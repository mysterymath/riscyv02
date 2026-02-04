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

  localparam E_IDLE    = 3'd0;
  localparam E_ADDR_LO = 3'd1;  // Computing address low byte
  localparam E_ADDR_HI = 3'd2;  // Computing address high byte
  localparam E_MEM_LO  = 3'd3;  // Memory access low byte
  localparam E_MEM_HI  = 3'd4;  // Memory access high byte (can dispatch)

  reg [2:0]  state;
  reg [15:0] MAR;

  // -------------------------------------------------------------------------
  // Interrupt and PC state
  // -------------------------------------------------------------------------
  reg [15:0] pc;       // Current PC (address of instruction in execute)
  reg [15:0] next_pc;  // Computed next PC (sequential or branch target)
  reg [15:0] epc;      // Exception PC (bit 0 used for I on save)
  reg        i_bit;    // Interrupt disable flag (0=enabled, 1=disabled)

  // -------------------------------------------------------------------------
  // Decoded instruction state (active)
  // -------------------------------------------------------------------------
  reg        is_store_r;      // 1 = SW, 0 = LW or JR
  reg        is_jr_r;         // 1 = JR
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
  // Ready states can accept a new dispatch.
  wire ready = (state == E_IDLE) || (state == E_MEM_HI);
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
  wire is_recognized = is_lw || is_sw || is_jr;

  // -------------------------------------------------------------------------
  // Interrupt detection
  // -------------------------------------------------------------------------
  wire irq_pending = !irqb && !i_bit;
  wire irq_entry = ready && ir_valid && irq_pending;

  wire [2:0] ir_base_sel    = is_jr ? fetch_ir[2:0] : fetch_ir[11:9];
  wire [5:0] ir_off6        = fetch_ir[8:3];
  wire [2:0] ir_rd_rs2_sel  = fetch_ir[2:0];

  // Dispatch: accepting a recognized instruction this cycle (not on IRQ entry)
  wire dispatch = ready && ir_valid && is_recognized && !irq_entry;

  // ir_accept: we consumed an instruction (recognized or NOP), not on IRQ entry
  // On IRQ entry, the instruction is not consumed - it will be re-fetched after RETI
  assign ir_accept = ready && ir_valid && !irq_entry;

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
  wire in_mem_state = (state == E_MEM_LO) || (state == E_MEM_HI);
  assign r_sel = in_mem_state ? rd_rs2_sel_r : base_sel_r;

  // r_hi: select high byte in E_ADDR_HI and E_MEM_HI
  assign r_hi = (state == E_ADDR_HI) || (state == E_MEM_HI);

  // Write port: fires in E_MEM_LO and E_MEM_HI for loads
  assign w_we   = in_mem_state && !is_store_r;
  assign w_sel  = rd_rs2_sel_r;
  assign w_hi   = (state == E_MEM_HI);
  assign w_data = uio_in;

  // -------------------------------------------------------------------------
  // Redirect interface (JR, RETI, IRQ entry)
  // -------------------------------------------------------------------------
  wire jr_redirect   = (state == E_ADDR_HI) && is_jr_r;
  wire reti_redirect = ready && ir_valid && is_reti && !irq_entry;

  assign redirect    = jr_redirect || reti_redirect || irq_entry;
  assign redirect_pc = irq_entry   ? 16'h0004 :
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
      base_sel_r       <= 3'b000;
      off6_r           <= 6'b000000;
      rd_rs2_sel_r     <= 3'b000;
      // Interrupt and PC state
      pc               <= 16'h0000;
      next_pc          <= 16'h0000;
      epc              <= 16'h0000;
      i_bit            <= 1'b1;  // Interrupts disabled after reset
    end else begin
      // -----------------------------------------------------------------------
      // IRQ entry: save EPC and jump to vector (highest priority)
      // -----------------------------------------------------------------------
      if (irq_entry) begin
        epc   <= next_pc | {15'b0, i_bit};  // Save return address with I bit
        i_bit <= 1'b1;                       // Disable further interrupts
        next_pc <= 16'h0004;                 // Set up for vector (ir_accept will commit)
        state <= E_IDLE;                     // Return to idle (important if in E_MEM_HI)
      end else begin
        // ---------------------------------------------------------------------
        // Normal instruction processing
        // ---------------------------------------------------------------------

        // RETI: restore I bit and redirect to saved PC
        if (reti_redirect) begin
          i_bit   <= epc[0];
          next_pc <= {epc[15:1], 1'b0};  // Set up for return (ir_accept will commit)
        end

        // SEI: set interrupt disable
        if (ready && ir_valid && is_sei) begin
          i_bit <= 1'b1;
        end

        // CLI: clear interrupt disable
        if (ready && ir_valid && is_cli) begin
          i_bit <= 1'b0;
        end

        // Dispatch: latch decoded fields when accepting a recognized instruction.
        // dispatch implies ready, so this only fires in E_IDLE/E_MEM_HI.
        if (dispatch) begin
          is_store_r     <= is_sw;
          is_jr_r        <= is_jr;
          base_sel_r     <= ir_base_sel;
          off6_r         <= ir_off6;
          rd_rs2_sel_r   <= ir_rd_rs2_sel;
        end

        // PC update on instruction acceptance (sequential)
        if (ir_accept && !reti_redirect) begin
          pc      <= next_pc;
          next_pc <= next_pc + 16'd2;
        end

        case (state)
          E_IDLE:
            if (dispatch) state <= E_ADDR_LO;
            // NOP, SEI, CLI, or no instruction: stay in E_IDLE

          E_ADDR_LO: begin
            MAR[7:0] <= alu_result;
            state    <= E_ADDR_HI;
          end

          E_ADDR_HI: begin
            MAR[15:8] <= alu_result;
            if (is_jr_r) begin
              // JR: redirect fires this cycle, return to idle
              // Update next_pc to branch target (for potential IRQ entry)
              next_pc <= {alu_result, MAR[7:0]};
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

          E_MEM_HI:
            // LW: rd_hi written via w_we during this cycle
            // SW: rs2_hi output via dout during this cycle
            state <= dispatch ? E_ADDR_LO : E_IDLE;

          default: state <= E_IDLE;
        endcase
      end
    end
  end

endmodule
