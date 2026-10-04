package com.acme.login.di

import com.acme.login.data.AuthApi
import com.acme.login.data.AuthRepositoryImpl
import com.acme.login.domain.AuthRepository
import com.acme.network.ApiClient
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent

@Module
@InstallIn(SingletonComponent::class)
internal abstract class LoginModule {
    @Binds
    abstract fun bindAuthRepository(impl: AuthRepositoryImpl): AuthRepository

    companion object {
        @Provides
        fun provideAuthApi(client: ApiClient): AuthApi {
            return client.create(AuthApi::class.java)
        }
    }
}
