package com.shop.service;

import java.util.HashMap;
import java.util.Map;

public final class Util {
    static { init(); }
    private static void init() { }
    public static Map<String, ?> newMap() { return new HashMap<>(); }
    public static long round(long v) { return v; }
    public static void log() { }
}
