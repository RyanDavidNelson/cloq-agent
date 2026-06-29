/* Held-out transfer unit. Verbatim from OpenSSL openssl-3.4.0 (98acb6b0), crypto/bn/bn_lib.c
   (BN_consttime_swap), reduced: minimal bignum_st + BN_ULONG/flag shims replace the openssl bn
   internals; bn_wcheck_size becomes a no-op. The conditional swap loops over a->d[i]/b->d[i]
   (array loads + stores through two aliased pointers) — the ct_swap class. */
typedef unsigned long BN_ULONG;
#define BN_BITS2 32
#define BN_FLG_CONSTTIME 0x04
#define BN_FLG_FIXED_TOP 0x10
#define bn_wcheck_size(a, n) do { } while (0)

typedef struct bignum_st {
    BN_ULONG *d;
    int top;
    int dmax;
    int neg;
    int flags;
} BIGNUM;

void BN_consttime_swap(BN_ULONG condition, BIGNUM *a, BIGNUM *b, int nwords)
{
    BN_ULONG t;
    int i;

    bn_wcheck_size(a, nwords);
    bn_wcheck_size(b, nwords);

    condition = ((~condition & ((condition - 1))) >> (BN_BITS2 - 1)) - 1;

    t = (a->top ^ b->top) & condition;
    a->top ^= t;
    b->top ^= t;

    t = (a->neg ^ b->neg) & condition;
    a->neg ^= t;
    b->neg ^= t;

#define BN_CONSTTIME_SWAP_FLAGS (BN_FLG_CONSTTIME | BN_FLG_FIXED_TOP)
    t = ((a->flags ^ b->flags) & BN_CONSTTIME_SWAP_FLAGS) & condition;
    a->flags ^= t;
    b->flags ^= t;

    for (i = 0; i < nwords; i++) {
        t = (a->d[i] ^ b->d[i]) & condition;
        a->d[i] ^= t;
        b->d[i] ^= t;
    }
}
