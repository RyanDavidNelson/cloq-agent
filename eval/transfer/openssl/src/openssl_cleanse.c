/* Held-out transfer unit. OPENSSL_cleanse — the historical EXPLICIT-LOOP form (a write loop).
   NOTE/SWAP: openssl-3.4.0 crypto/mem_clr.c delegates to a `memset_func` function pointer (no
   liftable loop), so the verbatim 3.4.0 body is degenerate; this is the pre-3.0 explicit-loop
   implementation the task names as the "write loop" candidate. A store loop over the buffer. */
typedef __SIZE_TYPE__ size_t;

void OPENSSL_cleanse(void *ptr, size_t len)
{
    volatile unsigned char *p = (volatile unsigned char *)ptr;
    while (len-- > 0)
        *p++ = 0;
}
