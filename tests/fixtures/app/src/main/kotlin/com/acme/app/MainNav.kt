package com.acme.app

import androidx.compose.runtime.Composable
import com.acme.login.ui.LoginScreen

@Composable
fun MainNav(onDone: () -> Unit) {
    LoginScreen(onLoggedIn = onDone)
}
