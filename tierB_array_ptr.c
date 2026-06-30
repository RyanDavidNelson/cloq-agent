/* Tier B array/pointer loops -- no early exit. Unblocked by Phase 1 (exists-index
   invariant + explicit witness). Bodies do arithmetic (not a bare copy) to keep gcc
   from lowering them to a memcpy/memset libcall the lifter cannot see. */

/* Word-stride accumulate: a[0..len). PTR_ALIGN + LEN_VALID assumed. */
unsigned int ap_sum_u32(unsigned int *a, unsigned int len) {
    unsigned int s = 0;
    unsigned int i;
    for (i = 0; i < len; i++)
        s = s + a[i];
    return s;
}

/* Byte-stride accumulate: stride generalization of ap_sum_u32. */
unsigned int ap_sum_u8(unsigned char *a, unsigned int len) {
    unsigned int s = 0;
    unsigned int i;
    for (i = 0; i < len; i++)
        s = s + a[i];
    return s;
}

/* In-place scale: a single-region store in the loop -- a store is NOT aliasing
   (one region, no second pointer). Uses `mul` (rv32im) -> exercises tmul. */
void ap_scale_inplace(unsigned int *a, unsigned int len, unsigned int k) {
    unsigned int i;
    for (i = 0; i < len; i++)
        a[i] = a[i] * k;
}

/* Two read-only base pointers, no aliasing concern (no stores). */
unsigned int ap_dot2(unsigned int *a, unsigned int *b, unsigned int len) {
    unsigned int s = 0;
    unsigned int i;
    for (i = 0; i < len; i++)
        s = s + a[i] * b[i];
    return s;
}

/* Pointer-increment induction: walk `count` words via a running pointer. */
unsigned int ap_ptr_walk(unsigned int *p, unsigned int count) {
    unsigned int s = 0;
    unsigned int i;
    for (i = 0; i < count; i++) {
        s = s + *p;
        p = p + 1;
    }
    return s;
}

/* Constant-time conditional bitwise-NOT: the per-element work is identical on every
   path (no data-dependent branch), so the cycle count is independent of `mask`
   (the secret). WCET == exact cycle count; spec_lint must see mask absent from it. */
void ct_cond_not(unsigned int *a, unsigned int len, unsigned int mask) {
    unsigned int i;
    for (i = 0; i < len; i++)
        a[i] = a[i] ^ mask;
}
