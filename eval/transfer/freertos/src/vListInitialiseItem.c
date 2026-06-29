/* Held-out: FreeRTOS-Kernel V11.1.0 list.c vListInitialiseItem (verbatim). Straight-line. */
#include "freertos_shim.h"
void vListInitialiseItem( ListItem_t * const pxItem )
{
    pxItem->pxContainer = NULL;
    listSET_FIRST_LIST_ITEM_INTEGRITY_CHECK_VALUE( pxItem );
    listSET_SECOND_LIST_ITEM_INTEGRITY_CHECK_VALUE( pxItem );
}
