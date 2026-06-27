/* measure_stub.c — NEORV32 firmware: run one target under cycle measurement per GO pulse.
 *
 * Determinism rules (must match the Cloq timing model):
 *   - interrupts masked across the measured window (only the target counts),
 *   - caches disabled in the SoC config,
 *   - target lives in internal IMEM, operands in internal DMEM.
 *
 * Build once per *target* (link a different target_function); sweep *inputs* from the PS.
 */
#include <neorv32.h>
#include "mailbox.h"

/* The lifted code under test. Replace with the actual target (ct_swap, chacha20_block, ...). */
extern uint32_t target_function(uint32_t input);

#define MAILBOX ((cloq_mailbox_t *)0x90000000u)   /* match Vivado Address Editor */

int main(void) {
    /* Freeze the environment so mcycle reflects only the target. */
    neorv32_cpu_csr_write(CSR_MIE, 0);            /* mask all interrupts            */
    neorv32_cpu_csr_write(CSR_MCOUNTINHIBIT, 0);  /* enable mcycle + minstret       */

    for (;;) {
        MAILBOX->done = 0;
        while (MAILBOX->go == 0) { /* spin until the PS requests a run */ }
        MAILBOX->go = 0;

        uint32_t x = MAILBOX->input;

        uint32_t c0 = neorv32_cpu_csr_read(CSR_MCYCLE);
        uint32_t i0 = neorv32_cpu_csr_read(CSR_MINSTRET);

        uint32_t r  = target_function(x);

        uint32_t c1 = neorv32_cpu_csr_read(CSR_MCYCLE);
        uint32_t i1 = neorv32_cpu_csr_read(CSR_MINSTRET);

        MAILBOX->mcycle   = c1 - c0;
        MAILBOX->minstret = i1 - i0;
        MAILBOX->result   = r;
        MAILBOX->done     = 1;
    }
    return 0;
}
