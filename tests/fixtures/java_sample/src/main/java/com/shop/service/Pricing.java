package com.shop.service;

import com.shop.model.Item;

public interface Pricing {
    long total(Item[] items);
}
