package com.acme.network

import okhttp3.OkHttpClient
import retrofit2.Retrofit

class ApiClient(private val http: OkHttpClient) {
    private val retrofit: Retrofit = Retrofit.Builder().client(http).build()

    fun <T> create(service: Class<T>): T = retrofit.create(service)

    fun reset() {}
}
