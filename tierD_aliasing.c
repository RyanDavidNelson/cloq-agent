/* Tier D memory-aliasing branches -- Phase 3. The proof must assume `noverlaps`
   over the touched regions and discharge a getmem_noverlap obligation; the
   premise-satisfiability gate must fire (a wrong region model => vacuous). */

/* Swap two words through two distinct pointers. Correct cycle count requires the
   two stores not to alias (noverlaps p q); the read-after-write of *p must survive
   the store to *q. */
void al_swap_a(unsigned int *p, unsigned int *q) {
    unsigned int t = *p;
    *p = *q;
    *q = t;
}

/* Unlink a node from a doubly linked list: n->prev->next = n->next;
   n->next->prev = n->prev. Three dynamic region addresses (n, n->prev, n->next)
   that must be pairwise non-overlapping for the stores to commute. */
struct node { struct node *next; struct node *prev; unsigned int val; };

void al_unlink(struct node *n) {
    struct node *p = n->prev;
    struct node *q = n->next;
    p->next = q;
    q->prev = p;
}
