/* Held-out: FreeRTOS-Kernel V11.1.0 tasks.c xTaskGetTickCount (verbatim body; the
   portTICK_TYPE_ENTER/EXIT_CRITICAL macros are no-ops on a 32-bit port). Reduced: xTickCount is the
   real global. Straight-line: read a global tick counter and return it. */
typedef unsigned long TickType_t;
#define portTICK_TYPE_ENTER_CRITICAL() do {} while (0)
#define portTICK_TYPE_EXIT_CRITICAL()  do {} while (0)
volatile TickType_t xTickCount = 0;

TickType_t xTaskGetTickCount( void )
{
    TickType_t xTicks;
    portTICK_TYPE_ENTER_CRITICAL();
    {
        xTicks = xTickCount;
    }
    portTICK_TYPE_EXIT_CRITICAL();
    return xTicks;
}
