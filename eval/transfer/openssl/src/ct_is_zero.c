/* Held-out transfer unit. Verbatim from OpenSSL openssl-3.4.0 (98acb6b0),
   include/internal/constant_time.h, reduced to a self-contained TU: the ossl_inline shim
   replaces <openssl/e_os2.h> (which needs a configured tree). Branchless / straight-line. */
static inline unsigned int constant_time_msb(unsigned int a)
{
    return 0 - (a >> (sizeof(a) * 8 - 1));
}
unsigned int constant_time_is_zero(unsigned int a)
{
    return constant_time_msb(~a & (a - 1));
}
