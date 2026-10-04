package com.acme.login.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState

@Composable
fun LoginScreen(
    onLoggedIn: () -> Unit,
    viewModel: LoginViewModel = hiltViewModel(),
) {
    val state = viewModel.state.collectAsState()
    LoginForm(
        state = state.value,
        onSubmit = viewModel::submit
    )
}

@Composable
internal fun LoginForm(state: LoginUiState, onSubmit: (String, String) -> Unit) {
    // UI implementation
}
