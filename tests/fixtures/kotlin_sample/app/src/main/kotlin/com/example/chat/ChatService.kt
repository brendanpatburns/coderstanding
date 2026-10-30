package com.example.chat

import com.example.util.Clock as Time
import com.example.util.Logger
import com.example.util.*

open class Base

class ChatService(
    private val repo: Repo,
    val users: Map<String, User?>,
    clock: Time
) : Base() {
    private val log = Logger()
    private val cache: MutableList<Message>? = null
    lateinit var last: Message
    val settings: Settings by lazy { Settings() }

    init {
        repo.save(Message("hello", null))
    }

    fun send(text: String, author: User?): Result<Message> {
        val message = Message(text, author)
        repo.save(message)
        log.info(text)
        return Result.success(message)
    }

    companion object {
        const val LIMIT = 10

        fun create(repo: Repo): ChatService {
            return ChatService(repo, emptyMap(), Time())
        }
    }

    inner class Session {
        fun close() {
            repo.all()
        }
    }
}

sealed class Event {
    object Closed : Event()
    data class Posted(val message: Message) : Event()
}

class Settings
