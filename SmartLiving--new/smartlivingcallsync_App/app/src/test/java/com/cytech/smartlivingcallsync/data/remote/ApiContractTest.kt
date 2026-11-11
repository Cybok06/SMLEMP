package com.cytech.smartlivingcallsync.data.remote

import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.coroutines.test.runTest
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

class ApiContractTest {
    @Test fun requestUsesExactServerPropertyNamesAndNoCustomerFields() {
        val request = SyncCallsRequest("support-phone-01", listOf(
            SyncCallDto("7821", "0530393625", "0240000001", "outbound", "2026-08-12T18:49:00Z", 332, "SIM1")
        ))
        val encoded = Json.encodeToString(request)
        val root = Json.parseToJsonElement(encoded).jsonObject
        val item = root.getValue("calls").let { it as kotlinx.serialization.json.JsonArray }.first().jsonObject
        assertEquals("support-phone-01", root.getValue("device_id").toString().trim('"'))
        val expected = setOf("external_call_id", "phone_number", "from_number", "call_type", "started_at", "duration_seconds", "sim_account")
        assertEquals(expected, item.keys)
        assertFalse(encoded.contains("customer_name"))
        assertFalse(encoded.contains("customer_id"))
        assertTrue(encoded.contains("\"duration_seconds\":332"))
    }

    @Test fun retrofitPostsExpectedPathHeadersAndBody() = runTest {
        val server = MockWebServer()
        server.enqueue(MockResponse().setResponseCode(200).setHeader("Content-Type", "application/json")
            .setBody("""{"success":true,"created":1,"duplicates":0,"failed":0,"results":[{"index":0,"external_call_id":"7821","status":"created"}]}"""))
        server.start()
        try {
            val json = Json { ignoreUnknownKeys = true }
            val api = Retrofit.Builder().baseUrl(server.url("/" )).client(OkHttpClient())
                .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
                .build().create(SmartLivingApi::class.java)
            val response = api.syncCalls(SyncCallsRequest("support-phone-01", listOf(
                SyncCallDto("7821", "0530393625", "", "outbound", "2026-08-12T18:49:00Z", 332, "SIM1")
            )))
            assertTrue(response.isSuccessful)
            val recorded = server.takeRequest()
            assertEquals("/api/customer-support/mobile/calls/sync", recorded.path)
            assertEquals(null, recorded.getHeader("Authorization"))
            assertEquals("application/json", recorded.getHeader("Accept"))
            assertEquals("application/json", recorded.getHeader("Content-Type"))
            val body = Json.parseToJsonElement(recorded.body.readUtf8()).jsonObject
            assertEquals("support-phone-01", body.getValue("device_id").toString().trim('"'))
            assertEquals(setOf("device_id", "calls"), body.keys)
        } finally { server.shutdown() }
    }
}
