package com.cytech.smartlivingcallsync.data.local

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class CallDaoInstrumentedTest {
    private lateinit var database: AppDatabase
    private lateinit var dao: CallDao

    @Before fun setUp() {
        database = Room.inMemoryDatabaseBuilder(ApplicationProvider.getApplicationContext<Context>(), AppDatabase::class.java)
            .allowMainThreadQueries().build()
        dao = database.callDao()
    }
    @After fun close() = database.close()

    @Test fun duplicateRoomInsertionDoesNotCreateAnotherRow() = runBlocking {
        val row = SyncedCallEntity("device:7821", "device", "7821", "0530393625", "", "outbound", "2026-08-12T18:49:00Z", 332, "SIM1")
        val results = dao.insertAll(listOf(row, row))
        assertEquals(1, results.count { it != -1L })
        assertEquals(1, dao.nextBatch("device", 100).size)
    }

    @Test fun processRestartRecoveryMovesSyncingBackToRetryable() = runBlocking {
        val row = SyncedCallEntity("device:1", "device", "1", "0551234567", "", "missed", "2026-08-12T18:49:00Z", 0, "SIM2")
        dao.insertAll(listOf(row)); dao.markSyncing(listOf(row.syncKey)); dao.recoverInterrupted()
        assertEquals(SyncStatus.FAILED_RETRYABLE, dao.nextBatch("device", 1).single().syncStatus)
    }

    @Test fun pendingRowsSurviveDatabaseReopen() = runBlocking {
        database.close()
        val context = ApplicationProvider.getApplicationContext<Context>()
        val name = "room-update-survival-test.db"
        context.deleteDatabase(name)
        val row = SyncedCallEntity("device:99", "device", "99", "0530393625", "0240000001", "inbound", "2026-08-13T00:20:00Z", 120, "SIM1")
        val beforeUpdate = Room.databaseBuilder(context, AppDatabase::class.java, name).allowMainThreadQueries().build()
        beforeUpdate.callDao().insertAll(listOf(row))
        beforeUpdate.close()
        val afterUpdate = Room.databaseBuilder(context, AppDatabase::class.java, name).allowMainThreadQueries().build()
        assertEquals(row, afterUpdate.callDao().nextBatch("device", 1).single())
        afterUpdate.close()
        context.deleteDatabase(name)
        database = Room.inMemoryDatabaseBuilder(context, AppDatabase::class.java).allowMainThreadQueries().build()
        dao = database.callDao()
    }
}
