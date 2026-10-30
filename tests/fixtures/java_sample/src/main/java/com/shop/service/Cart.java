package com.shop.service;

import static com.shop.service.Util.round;

import com.shop.model.*;
import com.google.common.collect.ImmutableList;
import java.util.Map;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Autowired;

@Service
public class Cart implements Pricing {
    private final Audit audit;
    @Autowired
    private Notifier notifier;
    private Map<String, Item> items = Util.newMap();
    private final Money zero = new Money(0, Kind.BOOK);
    private com.x.KotlinThing helper;

    public Cart(Audit audit) {
        this.audit = audit;
    }

    public long total(Item[] list) {
        audit.record("total");
        notifier.send();
        Runnable r = () -> Util.log();
        return round(list.length);
    }

    private ImmutableList<Book> books() { return null; }
}
