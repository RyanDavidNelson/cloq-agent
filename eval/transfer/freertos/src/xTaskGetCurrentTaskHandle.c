/* Held-out: FreeRTOS-Kernel V11.1.0 tasks.c xTaskGetCurrentTaskHandle (verbatim body). Reduced:
   TCB_t is stubbed (the function only returns the current-TCB pointer); pxCurrentTCB is the real
   global. Straight-line: load a global pointer and return it. */
typedef void * TaskHandle_t;
typedef struct tskTaskControlBlock TCB_t;
TCB_t * volatile pxCurrentTCB = 0;

TaskHandle_t xTaskGetCurrentTaskHandle( void )
{
    TaskHandle_t xReturn;
    xReturn = pxCurrentTCB;
    return xReturn;
}
