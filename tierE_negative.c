/* Tier E negatives -- MUST stay at the ceiling (fail-fast diagnostic). A PROVED
   here is a soundness alarm (a vacuous premise or a template firing where it must
   not), not progress. */

/* Nested loop: trace-like double sum over an m x n grid. Irreducible/nested control
   flow is out of scope for the CFG-derived invariant -- the classifier must wall it
   and fail fast, not spin. */
unsigned int neg_matmul_trace(unsigned int m, unsigned int n) {
    unsigned int t = 0;
    unsigned int i, j;
    for (i = 0; i < m; i++)
        for (j = 0; j < n; j++)
            t = t + (i * n + j);
    return t;
}

/* Cyclic linked-list search with a guard counter. The array-search template must
   NOT fire on a list traversal; termination relies on a guard, not a length, so the
   trip count is not a readable register -- the cyclic uniqueness problem is open. */
struct lnode { struct lnode *next; unsigned int key; };

unsigned int neg_cyclic_find(struct lnode *head, unsigned int key, unsigned int guard) {
    struct lnode *cur = head;
    unsigned int steps = 0;
    while (steps < guard) {
        if (cur->key == key)
            return steps;
        cur = cur->next;
        steps = steps + 1;
    }
    return guard;
}
