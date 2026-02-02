/*
 * Copyright (c) 2024 mysterymath
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// =========================================================================
// Execute unit: execute FSM + adder + decode (registers owned by top)
//
// PC updates (exec_pc_we / exec_pc_next) confirm the architectural PC
// after each instruction completes.
// =========================================================================
module riscyv02_execute (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  uio_in,
    input  wire        ir_valid,
    input  wire [15:0] new_ir,
    input  wire [15:0] ir_addr,
    input  wire [15:0] reg_a,
    output reg         exec_pc_we,
    output reg  [15:0] exec_pc_next,
    output wire        exec_busy,
    output reg  [15:0] exec_ab,
    output reg  [7:0]  exec_do,
    output reg         exec_rwb,
    // Register file interface (combinational)
    output wire [2:0]  reg_a_sel,
    output wire [2:0]  rd_sel,
    output wire [15:0] rd_data,
    output wire        rd_we
);

  localparam E_IDLE     = 3'd0;
  localparam E_LOAD_LO  = 3'd1;
  localparam E_LOAD_HI  = 3'd2;
  localparam E_STORE_LO = 3'd3;
  localparam E_STORE_HI = 3'd4;

  reg [2:0]  e_state;
  reg [15:0] IR;
  reg [15:0] MAR;
  reg [7:0]  mem_lo;
  reg [15:0] exec_ir_addr;

  assign exec_busy = (e_state != E_IDLE);

  // Datapath: adder and sign-extended offset
  wire [15:0] sext_off_x2 = {{9{new_ir[8]}}, new_ir[8:3], 1'b0};

  // Instruction decode
  wire is_lw = (new_ir[15:12] == 4'b1000);
  wire is_sw = (new_ir[15:12] == 4'b1010);
  wire is_jr = (new_ir[15:9]  == 7'b1011100);

  // Register read port mux:
  //   E_IDLE dispatch: rs1 (LW/SW) or rs (JR) from new_ir for the adder
  //   Store states: rs2 from IR for store data
  assign reg_a_sel = (e_state == E_STORE_LO || e_state == E_STORE_HI)
                     ? IR[2:0]
                     : ((is_lw || is_sw) ? new_ir[11:9] : new_ir[2:0]);
  wire [15:0] adder_result = reg_a + sext_off_x2;

  // ir_addr + 2
  wire [15:0] ir_addr_plus2 = {ir_addr[15:1] + 15'd1, 1'b0};

  // exec_ir_addr + 2
  wire [15:0] exec_ir_addr_plus2 = {exec_ir_addr[15:1] + 15'd1, 1'b0};

  // Combinational register write: fires in E_LOAD_HI
  assign rd_we   = (e_state == E_LOAD_HI);
  assign rd_sel  = IR[2:0];
  assign rd_data = {uio_in, mem_lo};

  // Execute bus address/data/control (combinational)
  always @(*) begin
    exec_ab  = MAR;
    exec_do  = 8'h00;
    exec_rwb = 1'b1;
    case (e_state)
      E_LOAD_LO: begin
        exec_ab  = MAR;
        exec_rwb = 1'b1;
      end
      E_LOAD_HI: begin
        exec_ab  = {MAR[15:1], 1'b1};
        exec_rwb = 1'b1;
      end
      E_STORE_LO: begin
        exec_ab  = MAR;
        exec_do  = reg_a[7:0];
        exec_rwb = 1'b0;
      end
      E_STORE_HI: begin
        exec_ab  = {MAR[15:1], 1'b1};
        exec_do  = reg_a[15:8];
        exec_rwb = 1'b0;
      end
      default: begin
        exec_ab  = 16'h0000;
        exec_rwb = 1'b1;
      end
    endcase
  end

  // Execute FSM (negedge clk) — only updates private state
  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) begin
      e_state      <= E_IDLE;
      IR           <= 16'h0000;
      MAR          <= 16'h0000;
      mem_lo       <= 8'h00;
      exec_pc_we   <= 1'b0;
      exec_pc_next <= 16'h0000;
      exec_ir_addr <= 16'h0000;
    end else begin
      exec_pc_we <= 1'b0;

      case (e_state)
        E_IDLE: begin
          if (ir_valid) begin
            if (is_jr) begin
              exec_pc_we   <= 1'b1;
              exec_pc_next <= adder_result;
            end else if (is_lw) begin
              MAR          <= adder_result;
              IR           <= new_ir;
              exec_ir_addr <= ir_addr;
              e_state      <= E_LOAD_LO;
            end else if (is_sw) begin
              MAR          <= adder_result;
              IR           <= new_ir;
              exec_ir_addr <= ir_addr;
              e_state      <= E_STORE_LO;
            end else begin
              // NOP
              exec_pc_we   <= 1'b1;
              exec_pc_next <= ir_addr_plus2;
            end
          end
        end

        E_LOAD_LO: begin
          mem_lo  <= uio_in;
          e_state <= E_LOAD_HI;
        end

        E_LOAD_HI: begin
          exec_pc_we   <= 1'b1;
          exec_pc_next <= exec_ir_addr_plus2;
          e_state      <= E_IDLE;
        end

        E_STORE_LO: begin
          e_state <= E_STORE_HI;
        end

        E_STORE_HI: begin
          exec_pc_we   <= 1'b1;
          exec_pc_next <= exec_ir_addr_plus2;
          e_state      <= E_IDLE;
        end

        default: e_state <= E_IDLE;
      endcase
    end
  end

endmodule
