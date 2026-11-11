package com.cytech.smartlivingcallsync.data.remote

import com.cytech.smartlivingcallsync.BuildConfig
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import java.util.concurrent.TimeUnit

object ApiFactory {
    fun create(): SmartLivingApi {
        val client = OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(45, TimeUnit.SECONDS)
            .build()
        val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
        return Retrofit.Builder()
            .baseUrl(BuildConfig.SMARTLIVING_BASE_URL)
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(SmartLivingApi::class.java)
    }
}
