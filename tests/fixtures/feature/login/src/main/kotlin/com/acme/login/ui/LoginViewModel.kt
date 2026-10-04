package com.acme.login.ui

import androidx.lifecycle.ViewModel
import com.acme.login.domain.LoginUseCase
import com.acme.login.domain.Session
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Coordina el flujo de inicio de sesión y expone el estado a la UI. */
@HiltViewModel
class LoginViewModel @Inject constructor(
    private val login: LoginUseCase
) : ViewModel() {
    private val _state = MutableStateFlow<LoginUiState>(LoginUiState.Idle)
    val state: StateFlow<LoginUiState> = _state.asStateFlow()

    /** Lanza el login con las credenciales dadas. */
    fun submit(email: String, password: String) {
        _state.value = LoginUiState.Loading
        login(email, password)
            .onSuccess { _state.value = LoginUiState.Success(it) }
            .onFailure { _state.value = LoginUiState.Error(it.message ?: "Error") }
    }
}

sealed interface LoginUiState {
    data object Idle : LoginUiState
    data object Loading : LoginUiState
    data class Success(val session: Session) : LoginUiState
    data class Error(val message: String) : LoginUiState
}
