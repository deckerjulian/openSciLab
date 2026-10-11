// Copyright (C) 2026 Julian Decker
// Part of openSciLab. SPDX-License-Identifier: GPL-3.0-or-later

package org.openscilab.bridge.core

import java.io.ByteArrayOutputStream
import kotlin.test.Test
import kotlin.test.assertEquals

class SkewTest {
    /** [values] as 16 bit samples, written in chunks of [chunk] samples, moved by [skew]. */
    private fun skewed(values: List<Int>, skew: Long, chunk: Int): List<Int> {
        val out = ByteArrayOutputStream()
        val writer = SnapshotWriter.SkewedOutput(out, skew, values.size.toLong())
        for (part in values.chunked(chunk)) {
            val bytes = ByteArray(part.size * 2)
            part.forEachIndexed { i, v -> bytes[2 * i] = v.toByte(); bytes[2 * i + 1] = (v ushr 8).toByte() }
            writer.write(bytes, bytes.size)
        }
        writer.finish()
        val data = out.toByteArray()
        return (0 until data.size / 2).map { (data[2 * it].toInt() and 0xFF) or ((data[2 * it + 1].toInt() and 0xFF) shl 8) }
    }

    @Test
    fun samplesMoveEarlierOrLaterWithTheEndsHeld() {
        val values = listOf(0, 0, 1, 1, 1, 0, 0, 0x100)
        for (chunk in listOf(1, 3, 8)) {
            assertEquals(values, skewed(values, 0, chunk))
            assertEquals(listOf(1, 1, 1, 0, 0, 0x100, 0x100, 0x100), skewed(values, 2, chunk))
            assertEquals(listOf(0, 0, 0, 0, 1, 1, 1, 0), skewed(values, -2, chunk))
        }
    }

    @Test
    fun ratesOfTheTable() {
        assertEquals(3, laSkew(6.25e6))
        assertEquals(0, laSkew(12.5e6))
        assertEquals(-1, laSkew(312.5e6 * 1.001))
    }
}
