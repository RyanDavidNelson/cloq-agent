/* Tier C search loops -- data-dependent early exit. Unblocked by Phase 2 (the
   array-search decidability case-split, emitted as a template from the recovered
   element shape). WCET of a search is a found / not-found disjunction. */

/* First index whose element is >= threshold. A DIFFERENT predicate (>=) from the
   vendored find_in_array (==): a Qed here proves the dec-template generalizes
   across the comparison, not just a structural twin match. */
unsigned int se_find_ge(unsigned int *a, unsigned int len, unsigned int threshold) {
    unsigned int i;
    for (i = 0; i < len; i++) {
        if (a[i] >= threshold)
            return i;
    }
    return len;
}

/* First index whose BYTE is zero. Byte width + predicate (== 0). Compile with
   -fno-tree-loop-distribute-patterns if the build lowers this to a memchr call. */
unsigned int se_first_zero_u8(unsigned char *p, unsigned int len) {
    unsigned int i;
    for (i = 0; i < len; i++) {
        if (p[i] == 0)
            return i;
    }
    return len;
}

/* First index whose element EQUALS key. The recall-vs-synthesis control: it sits
   closest to find_in_array, so the gap between the intact run and the
   `--ablate-gold-proof find_in_array` run is the headline generalization metric. */
unsigned int se_find_eq(unsigned int *a, unsigned int len, unsigned int key) {
    unsigned int i;
    for (i = 0; i < len; i++) {
        if (a[i] == key)
            return i;
    }
    return len;
}
