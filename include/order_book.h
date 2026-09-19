#ifndef ORDER_BOOK_H
#define ORDER_BOOK_H

#include <stdint.h>
#include <stddef.h>

#define ABYSS_BID 0
#define ABYSS_ASK 1

typedef int64_t price_t;
typedef int64_t volume_t;
typedef uint64_t order_id_t;

typedef struct {
    order_id_t id;
    price_t price;
    volume_t quantity;
    volume_t filled;
    uint64_t timestamp_ns;
} Order;

typedef struct order_node {
    Order order;
    struct order_node *next;
    struct order_node *prev;
} OrderNode;

typedef struct {
    price_t price;
    volume_t total_volume;
    OrderNode *head;
    OrderNode *tail;
} PriceLevel;

typedef struct {
    order_id_t key;
    OrderNode *value;
} OrderEntry;

typedef struct {
    OrderEntry *entries;
    size_t capacity;
    size_t size;
} OrderMap;

typedef struct {
    PriceLevel *bids;
    PriceLevel *asks;
    size_t bid_count;
    size_t ask_count;
    size_t max_levels;
    
    OrderMap *order_map;
    
    price_t best_bid;
    price_t best_ask;
    volume_t total_bid_volume;
    volume_t total_ask_volume;
    
    /* Arena allocator */
    char *arena;
    size_t arena_size;
    size_t arena_offset;
} OrderBook;

OrderBook* ob_create(size_t max_price_levels, size_t max_orders);
void ob_destroy(OrderBook *ob);
void ob_clear(OrderBook *ob);

int ob_add_order(OrderBook *ob, order_id_t id, price_t price, volume_t qty, uint64_t ts);
int ob_cancel_order(OrderBook *ob, order_id_t id);
int ob_modify_order(OrderBook *ob, order_id_t id, volume_t new_qty);
int ob_execute_trade(OrderBook *ob, price_t price, volume_t qty, uint64_t ts);
int ob_get_depth(OrderBook *ob, int side, size_t n, price_t *price_out, volume_t *vol_out);
int parse_binance_depth(OrderBook *ob, const char *json_str);
int parse_binance_depth_file(OrderBook *ob, const char *filename);

price_t ob_get_best_bid(OrderBook *ob);
price_t ob_get_best_ask(OrderBook *ob);
volume_t ob_get_bid_volume_at(OrderBook *ob, price_t price);
volume_t ob_get_ask_volume_at(OrderBook *ob, price_t price);

#endif
