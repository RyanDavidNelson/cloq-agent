/* Tier A controls -- straight-line and a pure counter loop.
   These already work; they are regression guards for the discharge layer. */

/* Straight-line: a fixed sequence of adds, no branches, constant cycle count. */
unsigned int sl_sum3(unsigned int x, unsigned int y, unsigned int z) {
    unsigned int s = x + y;
    s = s + z;
    return s;
}

/* Pure counter loop: decrement-to-zero, body has no memory access and no data
   dependence -- the same structural class as the addloop smoke target. */
unsigned int cl_countdown(unsigned int n) {
    unsigned int c = 0;
    while (n > 0) {
        c = c + 1;
        n = n - 1;
    }
    return c;
}
