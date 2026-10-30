package com.example.chat

/** Stores messages. */
interface Repo {
    fun save(message: Message)
    fun all(): List<Message>
}

data class Message(val text: String, val author: User?)

class User(val name: String)

class MemoryRepo : Repo {
    private val items = mutableListOf<Message>()

    override fun save(message: Message) {
        items.add(message)
    }

    override fun all(): List<Message> = items
}

class FileRepo(private val path: String) : Repo {
    override fun save(message: Message) {
    }

    override fun all(): List<Message> = emptyList()
}
