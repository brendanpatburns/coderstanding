package com.shop.model;

import java.util.List;

public class Book extends Item {
    private final List<Author> authors = new java.util.ArrayList<>();
    public int price() { return 10; }

    public static class Author {
        String name;
    }

    public class Page {
        int number;
    }
}
