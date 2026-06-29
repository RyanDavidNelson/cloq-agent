/* Held-out: FreeRTOS-Kernel V11.1.0 list.c uxListRemove (verbatim). Aliased pointer splice + a
   branch; the known memory-aliasing case. */
#include "freertos_shim.h"
UBaseType_t uxListRemove( ListItem_t * const pxItemToRemove )
{
    List_t * const pxList = pxItemToRemove->pxContainer;
    pxItemToRemove->pxNext->pxPrevious = pxItemToRemove->pxPrevious;
    pxItemToRemove->pxPrevious->pxNext = pxItemToRemove->pxNext;
    mtCOVERAGE_TEST_DELAY();
    if( pxList->pxIndex == pxItemToRemove )
        pxList->pxIndex = pxItemToRemove->pxPrevious;
    else
        mtCOVERAGE_TEST_MARKER();
    pxItemToRemove->pxContainer = NULL;
    ( pxList->uxNumberOfItems ) = ( UBaseType_t ) ( pxList->uxNumberOfItems - 1U );
    return pxList->uxNumberOfItems;
}
