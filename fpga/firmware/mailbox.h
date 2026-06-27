/* mailbox.h — AXI register map shared between the A53 PS (host) and NEORV32 (measured core).
 *
 * The PS writes INPUT + pulses GO; the core runs the target bracketed by mcycle/minstret CSR
 * reads and writes back MCYCLE/MINSTRET + DONE. One run per GO pulse. Offsets are byte offsets
 * from the NEORV32 AXI base address (see Vivado Address Editor).
 */
#ifndef CLOQ_MAILBOX_H
#define CLOQ_MAILBOX_H

#include <stdint.h>

#define MBX_INPUT     0x00u   /* PS -> core : argument / secret under test            */
#define MBX_GO        0x04u   /* PS -> core : write 1 to start a measured run          */
#define MBX_DONE      0x08u   /* core -> PS : 1 when MCYCLE/MINSTRET are valid          */
#define MBX_MCYCLE    0x0Cu   /* core -> PS : measured cycle count of the target        */
#define MBX_MINSTRET  0x10u   /* core -> PS : measured retired-instruction count        */
#define MBX_RESULT    0x14u   /* core -> PS : functional result (sanity / liveness)     */

/* Mapped to NEORV32's external bus (XBUS) region in the firmware linker script. */
typedef volatile struct {
    uint32_t input;
    uint32_t go;
    uint32_t done;
    uint32_t mcycle;
    uint32_t minstret;
    uint32_t result;
} cloq_mailbox_t;

#endif /* CLOQ_MAILBOX_H */
