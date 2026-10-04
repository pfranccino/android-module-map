package com.acme.login.domain

data class Session(val token: String, val userId: Long)

interface AuthRepository {
    suspend fun login(email: String, password: String): Result<Session>
    suspend fun logout()
}

class LoginUseCase @javax.inject.Inject constructor(
    private val repository: AuthRepository
) {
    suspend operator fun invoke(email: String, password: String): Result<Session> {
        return repository.login(email, password)
    }
}
