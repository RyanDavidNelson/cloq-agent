/* Held-out search target (Tier C): linear scan for the first index whose element is >= key.
   A DIFFERENT predicate (>=, not ==) from find_in_array — proves the decidability template
   generalizes across the comparison, not just a structural twin match. WCET ~ len. */
unsigned int se_find_ge(unsigned int *arr, unsigned int key, unsigned int len) {
    unsigned int i;
    for (i = 0; i < len; i++) {
        if (arr[i] >= key)
            return i;
    }
    return len;
}
