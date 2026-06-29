/* Held-out target (Tier C): a running-pointer word walk. Unlike se_find_eq (which keeps an index
   `i < len`), this bounds the loop by a POINTER comparison `p < end` and dereferences `*p++` — so
   the trip count is (end - p)/stride, not a register the CFG can read directly. GAP 1's recovery
   must treat the pointer-increment as the same induction (base = entry pointer, step = increment)
   and derive the trip count from the pointer range. Word stride (uint32). */
unsigned int ap_ptr_walk(unsigned int *p, unsigned int *end) {
    unsigned int acc = 0;
    while (p < end) {
        acc += *p;
        p++;
    }
    return acc;
}
