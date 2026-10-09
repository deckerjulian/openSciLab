// Copyright (C) 2026 Julian Decker
// Part of openSciLab. SPDX-License-Identifier: GPL-3.0-or-later

package org.openscilab.bridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import org.openscilab.bridge.core.BridgeConfig
import org.openscilab.bridge.core.BridgeServer
import org.openscilab.bridge.core.SnapshotCache
import java.io.File

/**
 * Runs the bridge (TCP 5560, UDP beacon 5561) as a foreground service, so Android keeps it
 * alive while the oscilloscope's own application is in front.
 */
class BridgeService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            getSystemService(NotificationManager::class.java).createNotificationChannel(
                NotificationChannel(CHANNEL, getString(R.string.app_name), NotificationManager.IMPORTANCE_LOW)
            )
        }
        startForeground(NOTIFICATION, notification("Starting"))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (server == null) {
            try {
                val created = BridgeServer(
                    BridgeConfig(
                        version = BuildConfig.VERSION_NAME,
                        fallbackModel = Build.MODEL.replace(Regex("\\s+"), "_").ifEmpty { "DHO" },
                    ),
                    SnapshotCache(cacheRoot(this)),
                )
                created.start()
                server = created
                startError = null
                getSystemService(NotificationManager::class.java)
                    .notify(NOTIFICATION, notification("Listening on port ${created.port}"))
            } catch (error: Exception) {
                startError = error.message ?: error.javaClass.simpleName
                getSystemService(NotificationManager::class.java)
                    .notify(NOTIFICATION, notification("Could not start: $startError"))
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        server?.close()
        server = null
        super.onDestroy()
    }

    private fun notification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE
        )
        // (the DHO900 runs Android 7.1: no notification channels there)
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return builder
            .setSmallIcon(R.drawable.ic_bridge)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val CHANNEL = "bridge"
        private const val NOTIFICATION = 1
        private const val PREFERENCES = "bridge"
        private const val ENABLED = "enabled"

        /** The running server, for the status screen. */
        @Volatile
        var server: BridgeServer? = null
            private set

        @Volatile
        var startError: String? = null
            private set

        /** Snapshots go to the app's directory on the shared storage (more room than /data). */
        fun cacheRoot(context: Context): File =
            context.getExternalFilesDir("snapshots") ?: File(context.filesDir, "snapshots")

        fun start(context: Context) {
            preferences(context).edit().putBoolean(ENABLED, true).commit()  // (on disk before a reboot)
            val intent = Intent(context, BridgeService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            preferences(context).edit().putBoolean(ENABLED, false).commit()
            context.stopService(Intent(context, BridgeService::class.java))
        }

        /** Whether the bridge was running when the user last chose; it starts again after a boot. */
        fun enabled(context: Context) = preferences(context).getBoolean(ENABLED, false)

        private fun preferences(context: Context) = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
    }
}
