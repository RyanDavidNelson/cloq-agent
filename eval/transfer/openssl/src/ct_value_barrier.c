/* Held-out transfer unit. Verbatim from OpenSSL openssl-3.4.0 (98acb6b0),
   include/internal/constant_time.h, reduced to a self-contained TU: the ossl_inline shim
   replaces <openssl/e_os2.h> (which needs a configured tree). Branchless / straight-line. */
unsigned int value_barrier(unsigned int a)
{
    unsigned int r;
    __asm__("" : "=r"(r) : "0"(a));
    return r;
}
