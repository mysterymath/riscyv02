# Code Comparison: RISCY-V02 vs 65C02

Side-by-side assembly for common routines, showing how the two ISAs compare on real code. All cycle counts assume same-page branches (the common case for tight loops). The 65C02 uses zero-page pointers; RISCY-V02 uses register arguments.

## memcpy

```c
void memcpy(void *dst, const void *src, size_t n);
```

**65C02** — arguments in zero page: src ($00), dst ($02), count ($04)

```
memcpy:
    LDY #0              ;  2 cy   2 B
    LDX count+1         ;  3 cy   2 B    ; full pages
    BEQ partial         ;  2 cy   2 B
page:
    LDA (src),Y         ;  5 cy   2 B
    STA (dst),Y         ;  6 cy   2 B
    INY                 ;  2 cy   1 B
    BNE page            ;  3 cy   2 B
    INC src+1           ;  5 cy   2 B    ; next page
    INC dst+1           ;  5 cy   2 B
    DEX                 ;  2 cy   1 B
    BNE page            ;  3 cy   2 B
partial:
    LDX count           ;  3 cy   2 B    ; remaining bytes
    BEQ done            ;  2 cy   2 B
tail:
    LDA (src),Y         ;  5 cy   2 B
    STA (dst),Y         ;  6 cy   2 B
    INY                 ;  2 cy   1 B
    DEX                 ;  2 cy   1 B
    BNE tail            ;  3 cy   2 B
done:
    RTS                 ;  6 cy   1 B
```

Inner loop (full pages): `LDA (src),Y` + `STA (dst),Y` + `INY` + `BNE` = **16 cy/byte**, 7 B

Page boundary: `INC` + `INC` + `DEX` + `BNE` = 15 cy / 256 bytes (0.06 cy/byte amortized)

Tail loop (partial page): adds `DEX` for count = **18 cy/byte**, 8 B

Total code: **28 bytes**

**RISCY-V02** — arguments in registers: R2 = dst, R3 = src, R4 = count

```
memcpy:
    LI   R1, 1          ;  2 cy   2 B    ; mask
    AND  R1, R4, R1     ;  2 cy   2 B    ; R1 = odd flag
    SRLI R4, 1          ;  2 cy   2 B    ; R4 = word count
    BZ   R4, tail       ;  2 cy   2 B
words:
    LWR  R5, R3         ;  4 cy   2 B
    SWR  R5, R2         ;  4 cy   2 B
    ADDI R3, 2          ;  2 cy   2 B
    ADDI R2, 2          ;  2 cy   2 B
    ADDI R4, -1         ;  2 cy   2 B
    BNZ  R4, words      ;  3 cy   2 B
tail:
    BZ   R1, done       ;  2 cy   2 B
    LBUR R5, R3         ;  3 cy   2 B
    SBR  R5, R2         ;  3 cy   2 B
done:
    JR   R6, 0          ;  3 cy   2 B
```

Word loop: `LWR` + `SWR` + 3×`ADDI` + `BNZ` = 17 cy / 2 bytes = **8.5 cy/byte**, 12 B

Tail: single `LBUR` + `SBR` for the trailing odd byte (if any). No page handling needed.

Total code: **28 bytes**

| | 65C02 | RISCY-V02 |
|---|---|---|
| Inner loop | 16 cy/byte | 8.5 cy/byte |
| Boundary overhead | 15 cy / 256 B | none |
| Tail | 18 cy/byte | 6 cy (1 byte) |
| Code size | 28 B | 28 B |

The 65C02's `(indirect),Y` is powerful — pointer dereference plus index in one instruction. But the 8-bit index register forces page-boundary handling that complicates the code. RISCY-V02's 16-bit pointers eliminate page handling, and 16-bit word loads/stores copy two bytes per bus transaction, nearly halving throughput cost. The structure is analogous: bulk transfer (pages vs words) with a tail for the remainder (partial page vs odd byte). Code size is identical at 28 bytes — the 65C02's compact 1-byte instructions (INY, DEX) compensate for the page-crossing overhead, while RISCY-V02's uniform 2-byte encoding trades density for simplicity.

## strcpy

```c
char *strcpy(char *dst, const char *src);
```

**65C02** — arguments in zero page: src ($00), dst ($02)

```
strcpy:
    LDY #0              ;  2 cy   2 B
loop:
    LDA (src),Y         ;  5 cy   2 B
    STA (dst),Y         ;  6 cy   2 B
    BEQ done            ;  2 cy   2 B
    INY                 ;  2 cy   1 B
    BNE loop            ;  3 cy   2 B
    INC src+1           ;  5 cy   2 B    ; page crossing
    INC dst+1           ;  5 cy   2 B
    BRA loop            ;  3 cy   2 B
done:
    RTS                 ;  6 cy   1 B
```

Inner loop: `LDA` + `STA` + `BEQ` + `INY` + `BNE` = **18 cy/char**, 9 B

Page crossing: `INC` + `INC` + `BRA` = 13 cy / 256 chars (0.05 cy/char amortized)

Total code: **18 bytes**

**RISCY-V02** — arguments in registers: R2 = dst, R3 = src

```
strcpy:
    LBUR R5, R3         ;  3 cy   2 B
    SBR  R5, R2         ;  3 cy   2 B
    ADDI R3, 1          ;  2 cy   2 B
    ADDI R2, 1          ;  2 cy   2 B
    BNZ  R5, strcpy     ;  3 cy   2 B
    JR   R6, 0          ;  3 cy   2 B
```

Inner loop: `LBUR` + `SBR` + 2×`ADDI` + `BNZ` = **13 cy/char**, 10 B. No page handling.

Total code: **12 bytes**

| | 65C02 | RISCY-V02 |
|---|---|---|
| Inner loop | 18 cy/char | 13 cy/char |
| Page overhead | 13 cy / 256 chars | none |
| Code size | 18 B | 12 B |

Both versions store the byte before testing for the null terminator — the 65C02 via `BEQ` after `STA`, RISCY-V02 via `BNZ` after `SBR`. The 65C02 needs an extra `BEQ` branch (2 cycles, not taken) on every character to check for termination, plus page-crossing logic. RISCY-V02 folds the termination check into the loop's back-edge branch. At 12 bytes vs 18, RISCY-V02 is also more compact — the 65C02's page-crossing code (6 bytes) adds density overhead that RISCY-V02 simply doesn't need.

Word-copy variant (RISCY-V02 only, R7 = 0x00FF preloaded):

```
strcpy:
    LWR  R5, R3         ;  4 cy   2 B    ; load 2 chars
    AND  R1, R5, R7     ;  2 cy   2 B    ; R1 = low byte
    BZ   R1, lo         ;  2 cy   2 B
    SUB  R1, R5, R1     ;  2 cy   2 B    ; R1 = high byte << 8
    BZ   R1, hi         ;  2 cy   2 B
    SWR  R5, R2         ;  4 cy   2 B    ; store 2 chars
    ADDI R3, 2          ;  2 cy   2 B
    ADDI R2, 2          ;  2 cy   2 B
    J    strcpy         ;  3 cy   2 B
lo: SBR  R5, R2         ;  3 cy   2 B    ; store null
    JR   R6, 0          ;  3 cy   2 B
hi: SWR  R5, R2         ;  4 cy   2 B    ; store char + null
    JR   R6, 0          ;  3 cy   2 B
```

Word loop: 23 cy / 2 chars = **11.5 cy/char**, 26 B. The null-byte detection (`AND` + `BZ` + `SUB` + `BZ` = 8 cy) eats the word-load savings, so the speedup over the byte version is modest (~12%). Unlike memcpy, where word copies nearly halve throughput, strcpy's per-element null check limits the benefit.

## 16×16 → 16 Multiply

```c
uint16_t mul(uint16_t a, uint16_t b);
```

Both implementations use the same shift-and-add algorithm (GCC's `__mulsi3` pattern): shift the multiplier right one bit per iteration, conditionally add the multiplicand to the result, shift the multiplicand left, and exit early when the multiplier reaches zero.

**65C02** — arguments in zero page: mult ($00), mcand ($02), result ($04)

```
multiply:
    LDA #0              ;  2 cy   2 B
    STA result          ;  3 cy   2 B
    STA result+1        ;  3 cy   2 B
loop:
    LDA mult            ;  3 cy   2 B    ; early exit
    ORA mult+1          ;  3 cy   2 B
    BEQ done            ;  2 cy   2 B
    LSR mult+1          ;  5 cy   2 B    ; shift out bit 0
    ROR mult            ;  5 cy   2 B
    BCC no_add          ;  2.5 cy 2 B
    CLC                 ;  2 cy   1 B    ; result += mcand
    LDA result          ;  3 cy   2 B
    ADC mcand           ;  3 cy   2 B
    STA result          ;  3 cy   2 B
    LDA result+1        ;  3 cy   2 B
    ADC mcand+1         ;  3 cy   2 B
    STA result+1        ;  3 cy   2 B
no_add:
    ASL mcand           ;  5 cy   2 B    ; mcand <<= 1
    ROL mcand+1         ;  5 cy   2 B
    BRA loop            ;  3 cy   2 B
done:
    RTS                 ;  6 cy   1 B
```

Per iteration (no add): **34 cy** — `LDA`+`ORA`+`BEQ`+`LSR`+`ROR`+`BCC`(taken)+`ASL`+`ROL`+`BRA`

Per iteration (add): **54 cy** — adds `CLC` + 3×(`LDA`/`ADC`/`STA`) chain

Average: **44 cy/iter**. Total code: **36 bytes**

**RISCY-V02** — arguments in registers: R2 = multiplier, R3 = multiplicand, result in R4

```
multiply:
    LI   R4, 0          ;  2 cy   2 B
    LI   R1, 1          ;  2 cy   2 B    ; constant mask
loop:
    BZ   R2, done       ;  2 cy   2 B    ; early exit
    AND  R0, R2, R1     ;  2 cy   2 B    ; R0 = bit 0
    SRLI R2, 1          ;  2 cy   2 B    ; multiplier >>= 1
    BZ   R0, no_add     ;  2.5 cy 2 B
    ADD  R4, R4, R3     ;  2 cy   2 B    ; result += mcand
no_add:
    SLLI R3, 1          ;  2 cy   2 B    ; mcand <<= 1
    J    loop           ;  3 cy   2 B
done:
    JR   R6, 0          ;  3 cy   2 B
```

Per iteration (no add): **14 cy** — `BZ`+`AND`+`SRLI`+`BZ`(taken)+`SLLI`+`J`

Per iteration (add): **15 cy** — adds `ADD`

Average: **14.5 cy/iter**. Total code: **20 bytes**

| | 65C02 | RISCY-V02 |
|---|---|---|
| Per iteration (avg) | 44 cy | 14.5 cy |
| 16 iterations (avg) | ~704 cy | ~232 cy |
| Code size | 36 B | 20 B |

The 3× per-iteration speedup comes from three sources: 16-bit addition is one instruction (`ADD`) vs seven (`CLC`+3×`LDA`/`ADC`/`STA`); 16-bit shifts are one instruction (`SLLI`/`SRLI`) vs two (`ASL`+`ROL`); and testing a 16-bit value for zero is one instruction (`BZ`) vs three (`LDA`+`ORA`+`BEQ`). Every 16-bit operation that the 6502 must serialize byte-by-byte collapses to a single instruction on RISCY-V02. Code density follows the same pattern: 20 bytes vs 36, a 44% reduction. The 65C02's 1-byte `CLC` and implied-operand instructions can't compensate for the sheer number of extra instructions needed to work in 8-bit halves.

## 16 ÷ 16 Unsigned Division

```c
uint16_t udiv16(uint16_t dividend, uint16_t divisor);
// Returns quotient; remainder available as a byproduct.
```

Both implementations use binary long division (restoring): shift the dividend left one bit at a time into a running remainder, trial-subtract the divisor, and shift the success/fail bit into the quotient.

**65C02** — arguments in zero page: dividend ($00), divisor ($02), remainder ($04)

```
udiv16:
    LDA #0              ;  2 cy   2 B
    STA rem             ;  3 cy   2 B
    STA rem+1           ;  3 cy   2 B
    LDX #16             ;  2 cy   2 B
loop:
    ASL dividend        ;  5 cy   2 B    ; shift dividend left
    ROL dividend+1      ;  5 cy   2 B    ;   high bit → carry
    ROL rem             ;  5 cy   2 B    ; shift into remainder
    ROL rem+1           ;  5 cy   2 B
    SEC                 ;  2 cy   1 B    ; trial subtract
    LDA rem             ;  3 cy   2 B
    SBC divisor         ;  3 cy   2 B
    TAY                 ;  2 cy   1 B    ; save low result
    LDA rem+1           ;  3 cy   2 B
    SBC divisor+1       ;  3 cy   2 B
    BCC no_sub          ;  2.5 cy 2 B    ; borrow → can't subtract
    STA rem+1           ;  3 cy   2 B    ; commit subtraction
    STY rem             ;  3 cy   2 B
    INC dividend        ;  5 cy   2 B    ; set quotient bit
no_sub:
    DEX                 ;  2 cy   1 B
    BNE loop            ;  3 cy   2 B
    RTS                 ;  6 cy   1 B
```

Per iteration (no sub): **44 cy** — `ASL`+`ROL`×3+`SEC`+`LDA`+`SBC`+`TAY`+`LDA`+`SBC`+`BCC`(taken)+`DEX`+`BNE`

Per iteration (sub): **54 cy** — adds `STA`+`STY`+`INC`

Average: **49 cy/iter**. Total code: **38 bytes**

**RISCY-V02** — R2 = dividend (becomes quotient), R3 = divisor, R4 = remainder

```
udiv16:
    LI   R4, 0          ;  2 cy   2 B    ; remainder = 0
    LI   R5, 16         ;  2 cy   2 B    ; counter
loop:
    SLLT R2             ;  2 cy   2 B    ; dividend <<= 1, T = old bit 15
    RLT  R4             ;  2 cy   2 B    ; remainder <<= 1, shift in T
    CLTU R4, R3         ;  2 cy   2 B    ; T = (rem < div)
    BT   no_sub         ;  2.5 cy 2 B    ; skip if can't subtract
    SUB  R4, R4, R3     ;  2 cy   2 B    ; remainder -= divisor
    ORI  R2, 1          ;  2 cy   2 B    ; set quotient bit
no_sub:
    ADDI R5, -1         ;  2 cy   2 B    ; counter--
    BNZ  R5, loop       ;  3 cy   2 B
    JR   R6, 0          ;  3 cy   2 B
```

Per iteration (no sub): **16 cy** — `SLLT`+`RLT`+`CLTU`+`BT`(taken)+`ADDI`+`BNZ`

Per iteration (sub): **19 cy** — adds `SUB`+`ORI`

Average: **17.5 cy/iter**. Total code: **22 bytes**

| | 65C02 | RISCY-V02 |
|---|---|---|
| Per iteration (avg) | 49 cy | 17.5 cy |
| 16 iterations | ~784 cy | ~280 cy |
| Code size | 38 B | 22 B |

The structure is identical — the same restoring division algorithm. The 2.8× speedup comes from three sources: 16-bit shifts are single instructions, the trial subtraction compresses from 6 instructions to 2 (`CLTU`+`SUB`), and `SLLT`+`RLT` chain the dividend's high bit directly into the remainder without needing `SRR`+`ANDI` to extract T into a register (saving 6 cy/iteration vs the pre-SLLT version). At 22 bytes vs 38, RISCY-V02 is 42% more compact — the 65C02's `SEC`+`TAY` bookkeeping and byte-by-byte shift chains add up fast.

## CRC-8 (SMBUS)

```c
uint8_t crc8(const uint8_t *data, uint8_t len);  // poly=0x07, init=0
```

Both use the standard bitwise algorithm: XOR each byte into the CRC, then shift left 8 times, conditionally XORing with the polynomial when the high bit shifts out.

**65C02** — ptr ($00), len ($02, 8-bit), result in A

```
crc8:
    LDA #0              ;  2 cy   2 B    crc = 0
    LDY #0              ;  2 cy   2 B    index
byte_loop:
    EOR (ptr),Y         ;  5 cy   2 B    crc ^= *data
    LDX #8              ;  2 cy   2 B
bit_loop:
    ASL A               ;  2 cy   1 B    crc <<= 1
    BCC no_xor          ;  2.5 cy 2 B
    EOR #$07            ;  2 cy   2 B    crc ^= poly
no_xor:
    DEX                 ;  2 cy   1 B
    BNE bit_loop        ;  3 cy   2 B
    INY                 ;  2 cy   1 B
    DEC len             ;  5 cy   2 B
    BNE byte_loop       ;  3 cy   2 B
    RTS                 ;  6 cy   1 B
```

Bit loop (no xor): **10 cy** — `ASL`+`BCC`(taken)+`DEX`+`BNE`

Bit loop (xor): **11 cy** — adds `EOR`

Average: **10.5 cy/bit**, 84 cy/byte bit processing. Per byte: **101 cy**. Total code: **22 bytes**

**RISCY-V02** — R2 = data ptr, R3 = len, result in R4; CRC kept in upper byte

```
crc8:
    LI   R4, 0          ;  2 cy   2 B    crc = 0 (upper byte)
    LI   R0, 0x07       ;  2 cy   2 B    polynomial
    SLLI R0, 8          ;  2 cy   2 B    R0 = 0x0700
byte_loop:
    LBUR R5, R2         ;  3 cy   2 B    R5 = *data
    SLLI R5, 8          ;  2 cy   2 B    data in upper byte
    XOR  R4, R4, R5     ;  2 cy   2 B    crc ^= byte
    LI   R5, 8          ;  2 cy   2 B
bit_loop:
    SLLT R4             ;  2 cy   2 B    crc <<= 1, T = old bit 15
    BF   no_xor         ;  2.5 cy 2 B    skip if bit was 0
    XOR  R4, R4, R0     ;  2 cy   2 B    crc ^= poly
no_xor:
    ADDI R5, -1         ;  2 cy   2 B
    BNZ  R5, bit_loop   ;  3 cy   2 B
    ADDI R2, 1          ;  2 cy   2 B    data++
    ADDI R3, -1         ;  2 cy   2 B    len--
    BNZ  R3, byte_loop  ;  3 cy   2 B
    SRLI R4, 8          ;  2 cy   2 B    move to low byte
    JR   R6, 0          ;  3 cy   2 B
```

Bit loop (no xor): **10 cy** — `SLLT`+`BF`(taken)+`ADDI`+`BNZ`

Bit loop (xor): **11 cy** — adds `XOR`

Average: **10.5 cy/bit**, 84 cy/byte bit processing. Per byte: **100 cy**. Total code: **32 bytes**

| | 65C02 | RISCY-V02 |
|---|---|---|
| Bit loop (avg) | 10.5 cy | 10.5 cy |
| Per byte | 101 cy | 100 cy |
| Code size | 22 B | 32 B |

Essentially a tie on speed. The `SLLT` instruction shifts the CRC and captures the overflow bit into T in one instruction — matching the 65C02's `ASL` + carry pattern. The remaining per-byte overhead (setup, pointer/counter updates) is slightly more on RISCY-V02 due to the upper-byte CRC convention, but the bit loop is now identical in cycle count. The 65C02 wins on density (22 B vs 32 B) — its 1-byte `ASL A`, `DEX`, `INY`, and `RTS` pack the inner loop tightly, while RISCY-V02's uniform 2-byte encoding and explicit counter management cost 10 extra bytes. This is where 8-bit code density shines: the algorithm is inherently 8-bit, so the 65C02's implied-operand instructions are at their most effective.

## CRC-16/CCITT

```c
uint16_t crc16(const uint8_t *data, uint8_t len);  // poly=0x1021, init=0xFFFF
```

Same bitwise algorithm, but with a 16-bit accumulator. The data byte is XORed into the high byte of the CRC.

**65C02** — ptr ($00), len ($02, 8-bit), crc ($04)

```
crc16:
    LDA #$FF            ;  2 cy   2 B    crc = 0xFFFF
    STA crc             ;  3 cy   2 B
    STA crc+1           ;  3 cy   2 B
    LDY #0              ;  2 cy   2 B
byte_loop:
    LDA crc+1           ;  3 cy   2 B    crc_hi ^= *data
    EOR (ptr),Y         ;  5 cy   2 B
    STA crc+1           ;  3 cy   2 B
    LDX #8              ;  2 cy   2 B
bit_loop:
    ASL crc             ;  5 cy   2 B    crc <<= 1
    ROL crc+1           ;  5 cy   2 B
    BCC no_xor          ;  2.5 cy 2 B
    LDA crc+1           ;  3 cy   2 B    crc ^= 0x1021
    EOR #$10            ;  2 cy   2 B
    STA crc+1           ;  3 cy   2 B
    LDA crc             ;  3 cy   2 B
    EOR #$21            ;  2 cy   2 B
    STA crc             ;  3 cy   2 B
no_xor:
    DEX                 ;  2 cy   1 B
    BNE bit_loop        ;  3 cy   2 B
    INY                 ;  2 cy   1 B
    DEC len             ;  5 cy   2 B
    BNE byte_loop       ;  3 cy   2 B
    RTS                 ;  6 cy   1 B
```

Bit loop (no xor): **18 cy** — `ASL`+`ROL`+`BCC`(taken)+`DEX`+`BNE`

Bit loop (xor): **33 cy** — adds 2×(`LDA`+`EOR`+`STA`)

Average: **25.5 cy/bit**, 204 cy/byte bit processing. Per byte: **227 cy**. Total code: **43 bytes**

**RISCY-V02** — R2 = data ptr, R3 = len, R4 = crc, R0 = polynomial

```
crc16:
    LI   R4, -1         ;  2 cy   2 B    crc = 0xFFFF
    LUI  R0, 0x10       ;  2 cy   2 B    R0 = 0x1000
    ORI  R0, 0x21       ;  2 cy   2 B    R0 = 0x1021
byte_loop:
    LBUR R5, R2         ;  3 cy   2 B    R5 = *data
    SLLI R5, 8          ;  2 cy   2 B    byte → high position
    XOR  R4, R4, R5     ;  2 cy   2 B    crc ^= byte << 8
    LI   R5, 8          ;  2 cy   2 B
bit_loop:
    SLLT R4             ;  2 cy   2 B    crc <<= 1, T = old bit 15
    BF   no_xor         ;  2.5 cy 2 B    skip if bit was 0
    XOR  R4, R4, R0     ;  2 cy   2 B    crc ^= 0x1021
no_xor:
    ADDI R5, -1         ;  2 cy   2 B
    BNZ  R5, bit_loop   ;  3 cy   2 B
    ADDI R2, 1          ;  2 cy   2 B    data++
    ADDI R3, -1         ;  2 cy   2 B    len--
    BNZ  R3, byte_loop  ;  3 cy   2 B
    JR   R6, 0          ;  3 cy   2 B
```

Bit loop (no xor): **10 cy** — `SLLT`+`BF`(taken)+`ADDI`+`BNZ`

Bit loop (xor): **11 cy** — adds `XOR`

Average: **10.5 cy/bit**, 84 cy/byte bit processing. Per byte: **100 cy**. Total code: **34 bytes**

| | 65C02 | RISCY-V02 |
|---|---|---|
| Bit loop (avg) | 25.5 cy | 10.5 cy |
| Per byte | 227 cy | 100 cy |
| Code size | 43 B | 34 B |

RISCY-V02 wins CRC-16 by >2× on speed and is also more compact (34 B vs 43 B). The `SLLT` instruction shifts and captures the overflow bit into T in a single instruction, matching the 65C02's `ASL`+carry for free. The 6502's bit loop goes from 10.5 to 25.5 cy (2.4× slower) because every shift becomes `ASL`+`ROL` and every XOR becomes `LDA`+`EOR`+`STA` × 2. The polynomial XOR is especially painful: 1 instruction on RISCY-V02 vs 6 on the 6502. The density advantage reverses from CRC-8 because the 65C02's byte-serialization overhead (6 extra instructions for XOR alone) outweighs its 1-byte instruction advantage.

## Raster Bar Interrupt Handler

A classic demo effect: an interrupt fires once per scanline to change the background color, producing horizontal rainbow bands. The handler increments a color byte in memory and writes it to a display register — the simplest possible useful work. Both examples target a C64-style system (VIC-II at $D000, color byte in zero page).

**Interrupt entry latency:**

Both CPUs must finish the current instruction before taking the interrupt. The average wait depends on the instruction mix of the interrupted code:

- **65C02:** Instructions take 2–7 cycles. Length-biased sampling across a typical game loop gives an average wait of **~1.5 cycles**. After the instruction completes, the hardware pushes PC and status to the stack and reads the IRQ vector: **7 cycles**.
- **RISCY-V02:** Instructions take 2–4 cycles (pipeline-visible). Average wait: **~1 cycle**. After completion, EPC/ESR are saved and the PC is redirected in the same cycle (instantaneous dispatch), then the vector is fetched: **2 cycles**.

**65C02** — color byte at $02 (zero page), VIC-II at $D019/$D021

```
                                    ;  7 cy        entry: push PC+P, read vector
irq_handler:
    PHA                 ;  3 cy   1 B    save A
    INC $02             ;  5 cy   2 B    color++ (zero page RMW)
    LDA $02             ;  3 cy   2 B    load updated color
    STA $D021           ;  4 cy   3 B    set background color
    LDA #$01            ;  2 cy   2 B
    STA $D019           ;  4 cy   3 B    ack raster interrupt
    PLA                 ;  4 cy   1 B    restore A
    RTI                 ;  6 cy   1 B
```

| Phase | Cycles |
|---|---|
| Instruction wait (avg) | ~1.5 |
| Hardware entry (push+vector) | 7 |
| Register save (`PHA`) | 3 |
| Handler body | 18 |
| Register restore (`PLA`) | 4 |
| Exit (`RTI`) | 6 |
| **Total** | **~39.5** |

Total code: **15 bytes**

**RISCY-V02** — color byte at $0002 (zero page), VIC-II at $D000

Every register the handler touches must be saved and restored. The handler needs R0 (implicit base for I-type memory ops) and R5 (scratch). The color byte is not within reach of the VIC registers, so R0 must be loaded twice — once for zero page, once for $D000.

Register saves go below the current SP without adjusting it. This is safe because RISCY-V02's IRQ entry sets I=1, masking further IRQs, and NMI handlers cannot return (RETI from NMI is undefined behavior per the architecture — NMI handlers reset, halt, or spin). Since nothing that could resume the handler will touch the stack, the space below SP is exclusively ours for the handler's lifetime.

```
                                    ;  2 cy        entry: instantaneous dispatch + fetch vector
irq_handler:
    SWS  R0, -4         ;  4 cy   2 B    save R0 below SP
    SWS  R5, -2         ;  4 cy   2 B    save R5 below SP
    LI   R0, 0          ;  2 cy   2 B    R0 → zero page
    LBU  R5, 2          ;  3 cy   2 B    R5 = color ($0002)
    ADDI R5, 1          ;  2 cy   2 B    color++
    SB   R5, 2          ;  3 cy   2 B    save color ($0002)
    LUI  R0, 0xD0       ;  2 cy   2 B    R0 = $D000
    SB   R5, $21        ;  3 cy   2 B    $D021: background color
    SB   R5, $19        ;  3 cy   2 B    $D019: ack raster interrupt
    LWS  R5, -2         ;  4 cy   2 B    restore R5
    LWS  R0, -4         ;  4 cy   2 B    restore R0
    RETI                ;  2 cy   2 B
```

| Phase | Cycles |
|---|---|
| Instruction wait (avg) | ~1 |
| Hardware entry (dispatch+fetch) | 2 |
| Register save (`SWS`×2) | 8 |
| Handler body | 18 |
| Register restore (`LWS`×2) | 8 |
| Exit (`RETI`+fetch) | 2 |
| **Total** | **~39** |

Total code: **24 bytes**

| | 65C02 | RISCY-V02 |
|---|---|---|
| Entry (HW) | 7 cy | 2 cy |
| Insn wait (avg) | ~1.5 cy | ~1 cy |
| Save/restore | 7 cy | 16 cy |
| Handler body | 18 cy | 18 cy |
| Exit | 6 cy | 2 cy |
| **Total** | **~39.5 cy** | **~39 cy** |
| Code size | 15 B | 24 B |

Essentially a tie on speed. The 65C02's architectural advantage — each instruction carries its own address (zero page or absolute), so the handler mixes `INC $02` (zero page) with `STA $D021` (absolute) without base register setup — is offset by RISCY-V02's instantaneous interrupt dispatch (2-cycle entry/exit vs 7+6=13 for the 6502). RISCY-V02 must reload R0 when switching memory regions and save/restore two registers (16 cy vs 7 cy), but the 9-cycle entry/exit savings almost exactly compensate. The 65C02 is significantly more compact (15 B vs 24 B) — its 1-byte `PHA`/`PLA`/`RTI` and embedded-address instructions (`INC $02`, `STA $D021`) pack tightly, while RISCY-V02 pays for explicit register save/restore and base register setup.

For handlers with more useful work, RISCY-V02's save/restore is fixed while its body instructions are generally faster, so the crossover comes quickly.

## RC4 Keystream (PRGA)

RC4's pseudo-random generation algorithm — the core inner loop of the stream cipher. Each call generates one byte of keystream from a 256-byte permutation table S and two indices i, j:

```
i = (i + 1) mod 256
j = (j + S[i]) mod 256
swap(S[i], S[j])
output = S[(S[i] + S[j]) mod 256]
```

Four array-indexed operations with computed indices, a swap, and double indirection — a worst case for a load-store architecture that must compute every address through registers.

**65C02** — S at $0200 (page-aligned), i/j in zero page

```
rc4_byte:
    INC i           ; 5 cy  2 B    i = (i+1) mod 256
    LDX i           ; 3 cy  2 B    X = i
    LDA $0200,X     ; 4 cy  3 B    A = S[i]
    PHA             ; 3 cy  1 B    save S[i]
    CLC             ; 2 cy  1 B
    ADC j           ; 3 cy  2 B    A = j + S[i]
    STA j           ; 3 cy  2 B    j updated
    TAY             ; 2 cy  1 B    Y = j
    LDA $0200,Y     ; 4 cy  3 B    A = S[j]
    STA $0200,X     ; 5 cy  3 B    S[i] = S[j]
    PLA             ; 4 cy  1 B    A = old S[i]
    STA $0200,Y     ; 5 cy  3 B    S[j] = old S[i]
    CLC             ; 2 cy  1 B
    ADC $0200,X     ; 4 cy  3 B    A = S[i]+S[j] (new)
    TAY             ; 2 cy  1 B
    LDA $0200,Y     ; 4 cy  3 B    output byte
    RTS             ; 6 cy  1 B
```

**61 cycles, 34 bytes.** S[i] is saved with `PHA` before the j computation (avoiding a re-read), then restored with `PLA` for the swap. The i and j indices must live in zero page because X and Y are needed for array indexing.

**RISCY-V02** — S base in R0, i in R1, j in R2; output in R3; R7 = 0x00FF (preloaded once)

```
; Setup (once): LI R7, -1; SRLI R7, 8  →  R7 = 0x00FF
rc4_byte:
    ADDI R1, 1          ; 2 cy  2 B    i++
    AND  R1, R1, R7     ; 2 cy  2 B    mod 256
    ADD  R3, R0, R1     ; 2 cy  2 B    R3 = &S[i]
    LBUR R4, R3         ; 3 cy  2 B    R4 = S[i]
    ADD  R2, R2, R4     ; 2 cy  2 B    j += S[i]
    AND  R2, R2, R7     ; 2 cy  2 B    mod 256
    ADD  R3, R0, R2     ; 2 cy  2 B    R3 = &S[j]
    LBUR R5, R3         ; 3 cy  2 B    R5 = S[j]
    SBR  R4, R3         ; 3 cy  2 B    S[j] = old S[i]
    ADD  R3, R0, R1     ; 2 cy  2 B    R3 = &S[i]
    SBR  R5, R3         ; 3 cy  2 B    S[i] = old S[j]
    ADD  R3, R4, R5     ; 2 cy  2 B    R3 = S[i]+S[j]
    AND  R3, R3, R7     ; 2 cy  2 B    mod 256
    ADD  R3, R0, R3     ; 2 cy  2 B    R3 = &S[sum]
    LBUR R3, R3         ; 3 cy  2 B    output byte
    JR   R6, 0          ; 3 cy  2 B
```

**38 cycles, 32 bytes.** Three `AND` instructions (6 cy) with a preloaded mask register are needed for mod-256 masking that the 6502 gets for free from 8-bit registers (the mask setup is amortized over many calls). Five `ADD` instructions (10 cy) compute array addresses that the 6502 folds into its indexed addressing modes. Despite this 16-cycle tax, RISCY-V02 wins by a wide margin.

| | 65C02 | RISCY-V02 |
|---|---|---|
| Cycles | 61 | 38 |
| Code size | 34 B | 32 B |
| Speedup | 1.0× | 1.6× |

RISCY-V02 wins decisively on speed (1.6×) and is slightly more compact (32 B vs 34 B). The mod-256 masking requires a preloaded mask register (R7 = 0x00FF) since ANDI is sign-extended, but the per-call cost is identical. Two factors overwhelm the mod-256 and address-computation tax:

1. **Registers eliminate state traffic.** The 6502 stores i and j in zero page — every call does INC+LDX+ADC+STA (14 cy) just to read, update, and write back two index variables. RISCY-V02 keeps i, j, and the S base in registers: state overhead is a single `ADDI` (2 cy).

2. **Multiple live values avoid spills and re-reads.** The swap requires S[i] and S[j] simultaneously, but the 6502's single accumulator forces a stack spill (`PHA`/`PLA`, 7 cy). RISCY-V02 holds both values in R4 and R5, computes the final sum as `ADD R3, R4, R5`, and never touches memory for temporaries.

## 32-bit Arithmetic

32-bit operations reveal the cost of each architecture's word width. The 6502's 8-bit ALU requires four byte-at-a-time steps for each 32-bit operation; RISCY-V02's 16-bit ALU cuts this to two. This section covers every R-type (register-register-register) ALU operation: ADD, SUB, AND, OR, XOR, SLL, SRL, SRA.

**Register conventions:**

- **65C02:** 32-bit values in four consecutive zero-page bytes (little-endian): a ($00–$03), b ($04–$07), r ($08–$0B). Shift count in X.
- **RISCY-V02:** 32-bit values in register pairs {high, low}: A = {R1, R0}, B = {R3, R2}, result = {R5, R4}. Shifts are in-place on {R1, R0} with count in R2.

### 32-bit ADD

```c
uint32_t add32(uint32_t a, uint32_t b);
```

**65C02**

```
    CLC             ;  2 cy   1 B
    LDA a           ;  3 cy   2 B
    ADC b           ;  3 cy   2 B
    STA r           ;  3 cy   2 B
    LDA a+1         ;  3 cy   2 B
    ADC b+1         ;  3 cy   2 B
    STA r+1         ;  3 cy   2 B
    LDA a+2         ;  3 cy   2 B
    ADC b+2         ;  3 cy   2 B
    STA r+2         ;  3 cy   2 B
    LDA a+3         ;  3 cy   2 B
    ADC b+3         ;  3 cy   2 B
    STA r+3         ;  3 cy   2 B
```

13 instructions, **25 bytes, 38 cycles.** The carry flag chains automatically through all four ADC operations.

**RISCY-V02** — A = {R1, R0}, B = {R3, R2}, result = {R5, R4}

```
    ADD  R4, R0, R2     ;  2 cy   2 B    Rl = Al + Bl
    CLTU R4, R0         ;  2 cy   2 B    T = carry (result < input)
    ADD  R5, R1, R3     ;  2 cy   2 B    Rh = Ah + Bh
    BF   done           ;  3 cy   2 B    skip if no carry (T=0)
    ADDI R5, 1          ;  2 cy   2 B    Rh += carry
done:
```

5 instructions, **10 bytes, 9–10 cycles.** `CLTU` detects unsigned overflow (result < input ⟹ carry), then a conditional `ADDI` propagates it. No carry flag needed — the T-bit comparison substitutes cleanly. For constant-time code (crypto), replace the branch with `SRR R0; ANDI R0, 1; ADD R5, R5, R0` (6 insns, 12 B, 12 cy).

| | 65C02 | RISCY-V02 |
|---|---|---|
| Code size | 25 B | 10 B |
| Cycles | 38 | 9–10 |
| Speedup | 1.0× | 3.8–4.2× |

### 32-bit SUB

```c
uint32_t sub32(uint32_t a, uint32_t b);
```

**65C02**

```
    SEC             ;  2 cy   1 B
    LDA a           ;  3 cy   2 B
    SBC b           ;  3 cy   2 B
    STA r           ;  3 cy   2 B
    LDA a+1         ;  3 cy   2 B
    SBC b+1         ;  3 cy   2 B
    STA r+1         ;  3 cy   2 B
    LDA a+2         ;  3 cy   2 B
    SBC b+2         ;  3 cy   2 B
    STA r+2         ;  3 cy   2 B
    LDA a+3         ;  3 cy   2 B
    SBC b+3         ;  3 cy   2 B
    STA r+3         ;  3 cy   2 B
```

13 instructions, **25 bytes, 38 cycles.** Mirror of ADD: SEC initializes the inverted borrow, and SBC chains carry through all four bytes.

**RISCY-V02** — A = {R1, R0}, B = {R3, R2}, result = {R5, R4}

```
    CLTU R0, R2         ;  2 cy   2 B    T = borrow (Al < Bl)
    SUB  R4, R0, R2     ;  2 cy   2 B    Rl = Al - Bl
    SUB  R5, R1, R3     ;  2 cy   2 B    Rh = Ah - Bh
    BF   done           ;  3 cy   2 B    skip if no borrow (T=0)
    ADDI R5, -1         ;  2 cy   2 B    Rh -= borrow
done:
```

5 instructions, **10 bytes, 9–10 cycles.** `CLTU` must precede `SUB` so the comparison uses the original Al (in case R4 aliases R0). SUB does not modify T, so the borrow survives to the branch. Constant-time variant: `SRR R0; ANDI R0, 1; SUB R5, R5, R0` (6 insns, 12 B, 12 cy).

| | 65C02 | RISCY-V02 |
|---|---|---|
| Code size | 25 B | 10 B |
| Cycles | 38 | 9–10 |
| Speedup | 1.0× | 3.8–4.2× |

### 32-bit AND / OR / XOR

```c
uint32_t and32(uint32_t a, uint32_t b);
uint32_t or32(uint32_t a, uint32_t b);
uint32_t xor32(uint32_t a, uint32_t b);
```

All three bitwise operations are identical in structure — no carry, no interaction between bytes/words.

**65C02** (shown for AND; substitute ORA/EOR for OR/XOR)

```
    LDA a           ;  3 cy   2 B
    AND b           ;  3 cy   2 B
    STA r           ;  3 cy   2 B
    LDA a+1         ;  3 cy   2 B
    AND b+1         ;  3 cy   2 B
    STA r+1         ;  3 cy   2 B
    LDA a+2         ;  3 cy   2 B
    AND b+2         ;  3 cy   2 B
    STA r+2         ;  3 cy   2 B
    LDA a+3         ;  3 cy   2 B
    AND b+3         ;  3 cy   2 B
    STA r+3         ;  3 cy   2 B
```

12 instructions, **24 bytes, 36 cycles.**

**RISCY-V02** (shown for AND; substitute OR/XOR)

```
    AND  R4, R0, R2     ;  2 cy   2 B    Rl = Al & Bl
    AND  R5, R1, R3     ;  2 cy   2 B    Rh = Ah & Bh
```

2 instructions, **4 bytes, 4 cycles.**

| | 65C02 | RISCY-V02 |
|---|---|---|
| Code size | 24 B | 4 B |
| Cycles | 36 | 4 |
| Speedup | 1.0× | 9.0× |

### 32-bit SLL (Shift Left Logical)

```c
uint32_t sll32(uint32_t a, unsigned shamt);  // shamt 0–31
```

The 6502 has no barrel shifter — it must loop one bit per iteration, chaining ASL+ROL across four bytes. RISCY-V02's barrel shifter (SLL/SRL shift 0–15 bits in one instruction) enables an O(1) approach: split on whether N >= 16, then shift both halves and merge the cross-word bits.

**65C02** — val ($00–$03, modified in-place), shift count in X

```
    LDX shift       ;  3 cy   2 B
    BEQ done        ;  2 cy   2 B
loop:
    ASL val         ;  5 cy   2 B
    ROL val+1       ;  5 cy   2 B
    ROL val+2       ;  5 cy   2 B
    ROL val+3       ;  5 cy   2 B
    DEX             ;  2 cy   1 B
    BNE loop        ;  3 cy   2 B
done:
```

8 instructions, **15 bytes.** Per iteration: **25 cycles.** An 8-bit shift costs 205 cycles.

**RISCY-V02** — {R1, R0} shifted in-place, count in R2 (consumed), R3 scratch

```
    BZ   R2, done       ;  2 cy   2 B
    LI   R3, 16         ;  2 cy   2 B
    CLTU R2, R3         ;  2 cy   2 B    T = (N < 16)
    BT   small          ;  3 cy   2 B
    ; N >= 16: Rh = Rl << (N-16), Rl = 0
    SUB  R2, R2, R3     ;  2 cy   2 B
    SLL  R1, R0, R2     ;  2 cy   2 B
    LI   R0, 0          ;  2 cy   2 B
    J    done           ;  3 cy   2 B
small:
    ; N = 1..15: shift both halves, merge cross-word bits
    SUB  R3, R3, R2     ;  2 cy   2 B    R3 = 16-N
    SRL  R3, R0, R3     ;  2 cy   2 B    R3 = Rl >> (16-N)
    SLL  R1, R1, R2     ;  2 cy   2 B    Rh <<= N
    OR   R1, R1, R3     ;  2 cy   2 B    Rh |= cross bits
    SLL  R0, R0, R2     ;  2 cy   2 B    Rl <<= N
done:
```

13 instructions, **26 bytes, 17–19 cycles** (constant, regardless of shift amount). For compact code at the cost of O(N) time, a loop alternative using SLLT+RLT is 5 insns / 10 B / 9N cy.

| | 65C02 | RISCY-V02 |
|---|---|---|
| Code size | 15 B | 26 B |
| 1-bit shift | 30 cy | 19 cy |
| 8-bit shift | 205 cy | 19 cy |
| 16-bit shift | 405 cy | 17 cy |
| Speedup (N=8) | 1.0× | 10.8× |

The 6502 is more compact (1-byte DEX, ASL, ROL) but O(N). RISCY-V02's barrel shifter makes the shift itself free — the overhead is all in the cross-word merge logic. For typical shift amounts (4–12), the barrel version is 5–10× faster.

### 32-bit SRL (Shift Right Logical)

```c
uint32_t srl32(uint32_t a, unsigned shamt);  // shamt 0–31
```

Mirror of SLL. The 6502 chains LSR+ROR from the MSB down; RISCY-V02 uses the barrel shifter with the halves reversed.

**65C02** — val ($00–$03, modified in-place), shift count in X

```
    LDX shift       ;  3 cy   2 B
    BEQ done        ;  2 cy   2 B
loop:
    LSR val+3       ;  5 cy   2 B
    ROR val+2       ;  5 cy   2 B
    ROR val+1       ;  5 cy   2 B
    ROR val         ;  5 cy   2 B
    DEX             ;  2 cy   1 B
    BNE loop        ;  3 cy   2 B
done:
```

8 instructions, **15 bytes.** Per iteration: **25 cycles.**

**RISCY-V02** — {R1, R0} shifted in-place, count in R2 (consumed), R3 scratch

```
    BZ   R2, done       ;  2 cy   2 B
    LI   R3, 16         ;  2 cy   2 B
    CLTU R2, R3         ;  2 cy   2 B    T = (N < 16)
    BT   small          ;  3 cy   2 B
    ; N >= 16: Rl = Rh >> (N-16), Rh = 0
    SUB  R2, R2, R3     ;  2 cy   2 B
    SRL  R0, R1, R2     ;  2 cy   2 B
    LI   R1, 0          ;  2 cy   2 B
    J    done           ;  3 cy   2 B
small:
    ; N = 1..15: shift both halves, merge cross-word bits
    SUB  R3, R3, R2     ;  2 cy   2 B    R3 = 16-N
    SLL  R3, R1, R3     ;  2 cy   2 B    R3 = Rh << (16-N)
    SRL  R0, R0, R2     ;  2 cy   2 B    Rl >>= N
    OR   R0, R0, R3     ;  2 cy   2 B    Rl |= cross bits
    SRL  R1, R1, R2     ;  2 cy   2 B    Rh >>= N
done:
```

13 instructions, **26 bytes, 17–19 cycles** (constant). Loop alternative: SRLT+RRT, 5 insns / 10 B / 9N cy.

| | 65C02 | RISCY-V02 |
|---|---|---|
| Code size | 15 B | 26 B |
| 1-bit shift | 30 cy | 19 cy |
| 8-bit shift | 205 cy | 19 cy |
| 16-bit shift | 405 cy | 17 cy |
| Speedup (N=8) | 1.0× | 10.8× |

### 32-bit SRA (Shift Right Arithmetic)

```c
int32_t sra32(int32_t a, unsigned shamt);  // shamt 0–31
```

Arithmetic right shift preserves the sign bit. The 6502 uses a clever trick: `LDA; ASL A` copies the sign bit into carry, then `ROR` propagates it from the MSB down — but must loop. RISCY-V02's barrel shifter handles it in O(1): the large-shift case uses SRA for the low half and `SRAI R1, 15` to sign-fill the high half.

**65C02** — val ($00–$03, modified in-place), shift count in X

```
    LDX shift       ;  3 cy   2 B
    BEQ done        ;  2 cy   2 B
loop:
    LDA val+3       ;  3 cy   2 B    load MSB
    ASL A           ;  2 cy   1 B    sign bit → carry
    ROR val+3       ;  5 cy   2 B    sign-preserving shift
    ROR val+2       ;  5 cy   2 B
    ROR val+1       ;  5 cy   2 B
    ROR val         ;  5 cy   2 B
    DEX             ;  2 cy   1 B
    BNE loop        ;  3 cy   2 B
done:
```

10 instructions, **18 bytes.** Per iteration: **30 cycles.** The LDA+ASL A trick (5 cy, 3 B) is the price of not having a dedicated ASR instruction.

**RISCY-V02** — {R1, R0} shifted in-place, count in R2 (consumed), R3 scratch

```
    BZ   R2, done       ;  2 cy   2 B
    LI   R3, 16         ;  2 cy   2 B
    CLTU R2, R3         ;  2 cy   2 B    T = (N < 16)
    BT   small          ;  3 cy   2 B
    ; N >= 16: Rl = Rh >>s (N-16), Rh = sign-fill
    SUB  R2, R2, R3     ;  2 cy   2 B
    SRA  R0, R1, R2     ;  2 cy   2 B    Rl = Rh >>s (N-16)
    SRAI R1, 15         ;  2 cy   2 B    Rh = 0x0000 or 0xFFFF
    J    done           ;  3 cy   2 B
small:
    ; N = 1..15: shift both halves, merge cross-word bits
    SUB  R3, R3, R2     ;  2 cy   2 B    R3 = 16-N
    SLL  R3, R1, R3     ;  2 cy   2 B    R3 = Rh << (16-N)
    SRL  R0, R0, R2     ;  2 cy   2 B    Rl >>= N (logical)
    OR   R0, R0, R3     ;  2 cy   2 B    Rl |= cross bits
    SRA  R1, R1, R2     ;  2 cy   2 B    Rh >>= N (arithmetic)
done:
```

13 instructions, **26 bytes, 17–19 cycles** (constant). `SRAI R1, 15` sign-fills the high word cleanly: it shifts in 15 copies of the sign bit, producing 0x0000 or 0xFFFF.

| | 65C02 | RISCY-V02 |
|---|---|---|
| Code size | 18 B | 26 B |
| 1-bit shift | 35 cy | 19 cy |
| 8-bit shift | 245 cy | 19 cy |
| 16-bit shift | 485 cy | 17 cy |
| Speedup (N=8) | 1.0× | 12.9× |

### 32-bit Summary

| Operation | 65C02 | | RISCY-V02 | | Speedup |
|---|---|---|---|---|---|
| | Bytes | Cycles | Bytes | Cycles | |
| ADD | 25 | 38 | 10 | 9–10 | 3.8–4.2× |
| SUB | 25 | 38 | 10 | 9–10 | 3.8–4.2× |
| AND | 24 | 36 | 4 | 4 | 9.0× |
| OR | 24 | 36 | 4 | 4 | 9.0× |
| XOR | 24 | 36 | 4 | 4 | 9.0× |
| SLL (N=8) | 15 | 205 | 26 | 19 | 10.8× |
| SRL (N=8) | 15 | 205 | 26 | 19 | 10.8× |
| SRA (N=8) | 18 | 245 | 26 | 19 | 12.9× |

For ADD/SUB, the 16-bit ALU collapses 4 byte additions to 2 word additions plus a lightweight carry/borrow chain (CLTU+BF). For bitwise ops, the advantage is stark: 2 instructions vs 12. For shifts, the barrel shifter makes the operation O(1) — the 6502 must loop N times with no escape, while RISCY-V02 handles any shift amount in a fixed 17–19 cycles. This is the clearest architectural win: the barrel shifter transforms shifts from the 6502's weakest operation into a constant-time operation that's 10–13× faster.

The 6502 wins on shift code size (15–18 B vs 26 B) — its compact 1-byte implied instructions pack the loop tightly. RISCY-V02 trades 11 bytes of code for a 10× speedup. For code-size-sensitive contexts, a compact loop alternative using SLLT/RLT (10 B, 9N cy) is available.
