package com.cytech.smartlivingcallsync.data.local

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.TypeConverter
import androidx.room.TypeConverters

class DatabaseConverters {
    @TypeConverter fun fromStatus(value: SyncStatus): String = value.name
    @TypeConverter fun toStatus(value: String): SyncStatus = SyncStatus.valueOf(value)
}

@Database(entities = [SyncedCallEntity::class], version = 1, exportSchema = false)
@TypeConverters(DatabaseConverters::class)
abstract class AppDatabase : RoomDatabase() {
    abstract fun callDao(): CallDao
    companion object {
        @Volatile private var instance: AppDatabase? = null
        fun get(context: Context): AppDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(context.applicationContext, AppDatabase::class.java, "smartliving-call-sync.db")
                .build().also { instance = it }
        }
    }
}
