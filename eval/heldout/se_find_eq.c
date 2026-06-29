/* Held-out search target (Tier C): linear scan for the first index whose element EQUALS key.
   Same predicate shape as the vendored find_in_array (==), but a distinct, self-contained program
   the corpus has never seen — so a Qed here measures generalization, not recall. WCET ~ len. */
unsigned int se_find_eq(unsigned int *arr, unsigned int key, unsigned int len) {
    unsigned int i;
    for (i = 0; i < len; i++) {
        if (arr[i] == key)
            return i;
    }
    return len;
}
