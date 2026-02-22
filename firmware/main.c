// RISCY-V02 demoboard firmware
//
// Emulates 64 KiB of SRAM and a simple UART peripheral on the TT demoboard's
// RP2350.  Core 1 runs a tight bus-servicing loop; core 0 handles USB serial
// I/O and the UART peripheral bridge.
//
// Build:
//   cmake -B build -G Ninja && cmake --build build
// Flash:
//   Drag build/riscyv02_firmware.uf2 to the RP2350's USB mass storage device.

#include <stdio.h>
#include <string.h>
#include "pico/stdlib.h"
#include "pico/multicore.h"
#include "hardware/pwm.h"
#include "hardware/clocks.h"
#include "tt_pins.h"

// ---------------------------------------------------------------------------
// Memory map
// ---------------------------------------------------------------------------
#define MEM_SIZE       0x10000  // 64 KiB
#define UART_BASE      0xFF00   // UART peripheral registers
#define UART_TX_DATA   0xFF00   // Write: send byte over USB
#define UART_RX_DATA   0xFF01   // Read: receive byte from USB
#define UART_STATUS    0xFF02   // Read: bit 0 = TX ready, bit 1 = RX available

static uint8_t mem[MEM_SIZE];

// UART peripheral state (shared between cores)
static volatile uint8_t uart_rx_buf;
static volatile bool    uart_rx_ready;

// ---------------------------------------------------------------------------
// TT demoboard control
// ---------------------------------------------------------------------------

static void tt_init_gpio(void) {
    // Control signals
    gpio_init(TT_GP_PROJCLK);
    gpio_init(TT_GP_NPROJECTRST);
    gpio_init(TT_GP_NCRST);
    gpio_init(TT_GP_CINC);
    gpio_init(TT_GP_CENA);

    gpio_set_dir(TT_GP_PROJCLK, GPIO_OUT);
    gpio_set_dir(TT_GP_NPROJECTRST, GPIO_OUT);
    gpio_set_dir(TT_GP_NCRST, GPIO_OUT);
    gpio_set_dir(TT_GP_CINC, GPIO_OUT);
    gpio_set_dir(TT_GP_CENA, GPIO_OUT);

    gpio_put(TT_GP_PROJCLK, 0);
    gpio_put(TT_GP_NPROJECTRST, 1);  // Not in reset
    gpio_put(TT_GP_NCRST, 1);        // Not in reset
    gpio_put(TT_GP_CINC, 0);
    gpio_put(TT_GP_CENA, 0);

    // ui_in[7:0] — outputs from RP2350 to ASIC
    for (int i = 0; i < 4; i++) {
        gpio_init(TT_GP_UI_IN0 + i);
        gpio_set_dir(TT_GP_UI_IN0 + i, GPIO_OUT);
        gpio_init(TT_GP_UI_IN4 + i);
        gpio_set_dir(TT_GP_UI_IN4 + i, GPIO_OUT);
    }

    // uo_out[7:0] — inputs from ASIC to RP2350
    for (int i = 0; i < 4; i++) {
        gpio_init(TT_GP_UO_OUT0 + i);
        gpio_set_dir(TT_GP_UO_OUT0 + i, GPIO_IN);
        gpio_init(TT_GP_UO_OUT4 + i);
        gpio_set_dir(TT_GP_UO_OUT4 + i, GPIO_IN);
    }

    // uio[7:0] — bidirectional, start as inputs
    for (int i = 0; i < 8; i++) {
        gpio_init(TT_GP_UIO_BASE + i);
        gpio_set_dir(TT_GP_UIO_BASE + i, GPIO_IN);
    }
}

// Select project N on the TT mux controller.
static void tt_select_project(uint16_t n) {
    gpio_put(TT_GP_CENA, 0);
    gpio_put(TT_GP_CINC, 0);

    // Reset mux counter
    gpio_put(TT_GP_NCRST, 0);
    sleep_ms(10);
    gpio_put(TT_GP_NCRST, 1);
    sleep_ms(10);

    // Pulse cinc N times
    for (uint16_t i = 0; i < n; i++) {
        gpio_put(TT_GP_CINC, 1);
        sleep_us(100);
        gpio_put(TT_GP_CINC, 0);
        sleep_us(100);
    }

    // Enable selected project
    gpio_put(TT_GP_CENA, 1);
}

// Assert then release project reset.
static void tt_reset_project(void) {
    gpio_put(TT_GP_NPROJECTRST, 0);
    sleep_ms(2);
    gpio_put(TT_GP_NPROJECTRST, 1);
}

// Start the project clock via PWM at the given frequency.
static void tt_start_clock(uint32_t freq_hz) {
    gpio_set_function(TT_GP_PROJCLK, GPIO_FUNC_PWM);
    uint slice = pwm_gpio_to_slice_num(TT_GP_PROJCLK);
    uint32_t sys_clk = clock_get_hz(clk_sys);
    uint32_t divider16 = (sys_clk << 4) / (freq_hz * 2);  // 4.4 fixed point
    uint32_t wrap = 1;  // Toggle every count → 50% duty
    pwm_set_clkdiv_int_frac(slice, divider16 >> 4, divider16 & 0xF);
    pwm_set_wrap(slice, wrap);
    pwm_set_chan_level(slice, pwm_gpio_to_channel(TT_GP_PROJCLK), 1);
    pwm_set_enabled(slice, true);
}

// Stop the project clock and hold it low.
static void tt_stop_clock(void) {
    uint slice = pwm_gpio_to_slice_num(TT_GP_PROJCLK);
    pwm_set_enabled(slice, false);
    gpio_set_function(TT_GP_PROJCLK, GPIO_FUNC_SIO);
    gpio_set_dir(TT_GP_PROJCLK, GPIO_OUT);
    gpio_put(TT_GP_PROJCLK, 0);
}

// ---------------------------------------------------------------------------
// Core 1: bus-servicing loop
//
// The RISCY-V02 bus protocol alternates between two phases each clock edge:
//   mux_sel=0 (clk HIGH→LOW): Address phase
//     uo_out[7:0] = AB[7:0], uio[7:0] = AB[15:8] (all driven by ASIC)
//   mux_sel=1 (clk LOW→HIGH): Data phase
//     uo_out[0] = RWB, uo_out[1] = SYNC
//     uio[7:0] = D[7:0] (ASIC drives on write, RP2350 drives on read)
//
// We don't generate the clock here — PWM on GPIO 0 runs independently.
// Instead we watch the clock pin and react to each phase.
// ---------------------------------------------------------------------------

// TODO: Implement the bus-servicing loop.  For now, this is a placeholder
// that will be filled in once the basic project skeleton is working.
static void core1_bus_service(void) {
    while (true)
        tight_loop_contents();
}

// ---------------------------------------------------------------------------
// Core 0: USB serial REPL and UART bridge
// ---------------------------------------------------------------------------

int main(void) {
    stdio_init_all();

    tt_init_gpio();

    // TODO: Read project index from config or command line.
    // For now, this must be set to the RISCY-V02 project index.
    uint16_t project_index = 0;
    tt_select_project(project_index);

    // Set ui_in: IRQB=1 (inactive), NMIB=1 (inactive), RDY=1 (ready)
    tt_write_ui_in(0x07);

    tt_reset_project();

    // Start bus servicing on core 1
    multicore_launch_core1(core1_bus_service);

    // Start project clock
    // TODO: Make configurable.  1 MHz is conservative and easy to service.
    tt_start_clock(1000000);

    printf("\nRISCY-V02 demoboard firmware\n");
    printf("Project index: %d\n", project_index);
    printf("Clock: 1 MHz\n\n");

    // Main loop: bridge UART peripheral to USB serial
    while (true) {
        // USB → UART RX: buffer one byte for the CPU to read
        int c = getchar_timeout_us(0);
        if (c != PICO_ERROR_TIMEOUT && !uart_rx_ready) {
            uart_rx_buf = (uint8_t)c;
            uart_rx_ready = true;
        }

        tight_loop_contents();
    }
}
