package com.example.util

import com.example.chat.ChatService
import com.example.chat.Message

class Clock

class Logger {
    fun info(text: String) {
    }
}

object Registry {
    val services = mutableListOf<ChatService>()
}

fun Message.shout(): String {
    return text.uppercase()
}

fun main(args: Array<String>) {
    val service = ChatService.create(com.example.chat.MemoryRepo())
    service.send("hi", null)
    Registry.services.add(service)
}
