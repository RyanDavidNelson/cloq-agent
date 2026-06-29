/* Held-out: FreeRTOS-Kernel V11.1.0 list.c vListInitialise (verbatim body; trace macros are the
   default no-ops). Straight-line field initialisation. */
#include "freertos_shim.h"
void vListInitialise( List_t * const pxList )
{
    pxList->pxIndex = ( ListItem_t * ) &( pxList->xListEnd );
    listSET_FIRST_LIST_ITEM_INTEGRITY_CHECK_VALUE( &( pxList->xListEnd ) );
    pxList->xListEnd.xItemValue = portMAX_DELAY;
    pxList->xListEnd.pxNext = ( ListItem_t * ) &( pxList->xListEnd );
    pxList->xListEnd.pxPrevious = ( ListItem_t * ) &( pxList->xListEnd );
    pxList->xListEnd.pvOwner = NULL;
    pxList->xListEnd.pxContainer = NULL;
    listSET_SECOND_LIST_ITEM_INTEGRITY_CHECK_VALUE( &( pxList->xListEnd ) );
    pxList->uxNumberOfItems = ( UBaseType_t ) 0U;
}
