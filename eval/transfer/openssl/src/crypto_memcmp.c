/* Held-out transfer unit. Verbatim from OpenSSL openssl-3.4.0 (98acb6b0), crypto/cpuid.c
   (CRYPTO_memcmp). Self-contained: size_t via the compiler builtin. One OR-accumulate loop over
   the two volatile byte buffers — a single-exit array/pointer loop. */
typedef __SIZE_TYPE__ size_t;

int CRYPTO_memcmp(const void *in_a, const void *in_b, size_t len)
{
    size_t i;
    const volatile unsigned char *a = in_a;
    const volatile unsigned char *b = in_b;
    unsigned char x = 0;

    for (i = 0; i < len; i++)
        x |= a[i] ^ b[i];

    return x;
}
