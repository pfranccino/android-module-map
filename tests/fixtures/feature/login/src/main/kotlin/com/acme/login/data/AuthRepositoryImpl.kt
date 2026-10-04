package com.acme.login.data

import com.acme.login.domain.AuthRepository
import com.acme.login.domain.Session
import com.acme.network.ApiClient
import retrofit2.http.Body
import retrofit2.http.POST

interface AuthApi {
    @POST("auth/login")
    suspend fun login(@Body body: LoginRequest): LoginResponse
}

data class LoginRequest(val email: String, val password: String)
data class LoginResponse(val token: String, val userId: Long)

internal class AuthRepositoryImpl @javax.inject.Inject constructor(
    private val api: AuthApi,
    private val client: ApiClient,
    private val store: SessionStore
) : AuthRepository {
    override suspend fun login(email: String, password: String): Result<Session> {
        val response = api.login(LoginRequest(email, password))
        val session = response.toSession()
        store.save(session)
        return Result.success(session)
    }

    override suspend fun logout() {
        store.clear()
        client.reset()
    }
}

private fun LoginResponse.toSession() = Session(token = token, userId = userId)

class SessionStore @javax.inject.Inject constructor() {
    private var current: Session? = null
    fun save(session: Session) { current = session }
    fun clear() { current = null }
}
