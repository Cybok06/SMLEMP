package com.cytech.smartlivingcallsync.data.remote

import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.Headers
import retrofit2.http.POST

interface SmartLivingApi {
    @Headers("Accept: application/json", "Content-Type: application/json")
    @POST("api/customer-support/mobile/calls/sync")
    suspend fun syncCalls(@Body request: SyncCallsRequest): Response<SyncCallsResponse>
}
