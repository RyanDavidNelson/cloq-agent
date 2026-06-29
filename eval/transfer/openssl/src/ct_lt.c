/* Held-out transfer unit. Verbatim from OpenSSL openssl-3.4.0 (98acb6b0),
   include/internal/constant_time.h (constant_time_lt: 0xff..f iff a < b). Branchless / straight-
   line, with a real instruction sequence (replaces value_barrier, which the compiler folds to a
   bare `ret`). */
static inline unsigned int constant_time_msb(unsigned int a)
{
    return 0 - (a >> (sizeof(a) * 8 - 1));
}
unsigned int constant_time_lt(unsigned int a, unsigned int b)
{
    return constant_time_msb(a ^ ((a ^ b) | ((a - b) ^ b)));
}
