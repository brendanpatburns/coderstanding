package com.shop.app;

import com.shop.service.Cart;
import com.shop.service.Audit;

public class Main {
    public static void main(String[] args) {
        Cart cart = new Cart(new Audit());
    }
}
