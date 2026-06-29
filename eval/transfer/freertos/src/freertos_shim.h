/* Minimal shim for the held-out FreeRTOS-Kernel V11.1.0 (dbf70559) list.c reductions: the REAL
   list.h structs + the trace/integrity/coverage macros as no-ops (their default, trace-off build),
   instead of pulling the whole FreeRTOS.h/portmacro/FreeRTOSConfig.h machinery.
   configUSE_MINI_LIST_ITEM = 0, configUSE_LIST_DATA_INTEGRITY_CHECK_BYTES = 0. */
#ifndef FREERTOS_SHIM_H
#define FREERTOS_SHIM_H
#define NULL ((void *)0)
typedef unsigned long TickType_t;
typedef unsigned long UBaseType_t;
typedef long BaseType_t;
#define configLIST_VOLATILE volatile
#define portMAX_DELAY ((TickType_t)0xffffffffUL)
#define listSET_FIRST_LIST_ITEM_INTEGRITY_CHECK_VALUE(p)  do {} while (0)
#define listSET_SECOND_LIST_ITEM_INTEGRITY_CHECK_VALUE(p) do {} while (0)
#define listTEST_LIST_ITEM_INTEGRITY(p)                   do {} while (0)
#define listTEST_LIST_INTEGRITY(p)                        do {} while (0)
#define mtCOVERAGE_TEST_MARKER()                          do {} while (0)
#define mtCOVERAGE_TEST_DELAY()                           do {} while (0)
#define configASSERT(x)                                   do {} while (0)

struct xLIST;
struct xLIST_ITEM {
    configLIST_VOLATILE TickType_t xItemValue;
    struct xLIST_ITEM * configLIST_VOLATILE pxNext;
    struct xLIST_ITEM * configLIST_VOLATILE pxPrevious;
    void * pvOwner;
    struct xLIST * configLIST_VOLATILE pxContainer;
};
typedef struct xLIST_ITEM ListItem_t;
typedef struct xLIST_ITEM MiniListItem_t;
typedef struct xLIST {
    configLIST_VOLATILE UBaseType_t uxNumberOfItems;
    ListItem_t * configLIST_VOLATILE pxIndex;
    MiniListItem_t xListEnd;
} List_t;
#endif
