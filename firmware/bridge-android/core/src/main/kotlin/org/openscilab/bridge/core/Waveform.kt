// Copyright (C) 2026 Julian Decker
// Part of openSciLab. SPDX-License-Identifier: GPL-3.0-or-later

package org.openscilab.bridge.core

import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.FileOutputStream
import java.io.IOException
import java.io.OutputStream
import java.math.BigDecimal
import java.math.MathContext
import kotlin.math.abs
import kotlin.math.roundToLong

/**
 * The logic channels' real sample period is this many times the `xIncrement` their own
 * `:WAVeform:PREamble?` reports (measured on a DHO924S, alone and with CH1, at 10 rates with a
 * signal of known frequency: real period 2.00 times the reported one, e.g. 3.2 ns at reported
 * `xIncrement` 1.6 ns): the instrument computes their preamble as if they were sampled at the
 * analog rate. For the same reason only the first `points / LA_PERIOD_FACTOR` words it gives out
 * are the capture's; the rest is old memory (stretches of one level that are not in the signal,
 * which read as holes), and only those are read. Read alone (no analog channel to line them up
 * with), [SnapshotWriter] corrects `xIncrement` by the factor (which also corrects the trigger
 * sample the same preamble gives, measured through a known edge's position). Read with the
 * analog channels, whose rate is not halved, their preamble already matches the analog one's
 * (same `xIncrement`, same trigger sample), since both describe a count of `:ACQuire:MDEPth`
 * memory slots: there, every logic sample is repeated this many times instead, which lines it up
 * with the analog channels' finer time axis without changing (or needing to understand) the
 * preamble.
 */
const val LA_PERIOD_FACTOR = 2

/**
 * Read out together with an analog channel, the logic channels come this many analog samples late
 * (positive) or early, by the analog rate (Sa/s): measured on a DHO924S (NK v0.5.2), CH1 and D0 on
 * one square wave, three captures per rate - the same table as the driver's `scpi.LA_SKEW`. The
 * instrument's screen aligns them in the FPGA's display path, which the export of the memory does
 * not go through. From capture to capture it varies by one sample; rates not measured: 0.
 */
val LA_SKEW = mapOf(
    1_250_000.0 to 3, 3_125_000.0 to 4, 6_250_000.0 to 3, 15_625_000.0 to 1, 31_250_000.0 to -2,
    62_500_000.0 to 0, 156_250_000.0 to 0, 312_500_000.0 to -1, 625_000_000.0 to -1,
)

fun laSkew(rate: Double): Int = LA_SKEW.entries.firstOrNull { abs(rate - it.key) <= 0.01 * it.key }?.value ?: 0

/**
 * `:WAVeform:PREamble?`: format, type, points, count, xincrement, xorigin, xreference,
 * yincrement, yorigin, yreference. The y values are kept as the instrument wrote them.
 */
data class Preamble(
    val points: Long,
    val xIncrement: Double,
    val xOrigin: Double,
    val xReference: Double,
    val yIncrement: String,
    val yOrigin: String,
    val yReference: String,
) {
    /** Samples per second. */
    val rate: Double get() = if (xIncrement > 0) 1.0 / xIncrement else 0.0

    /** The sample at time 0 (the trigger): time = (i - xreference) * xincrement + xorigin. */
    val trigger: Long get() = if (xIncrement > 0) (xReference - xOrigin / xIncrement).roundToLong() else 0

    companion object {
        fun parse(text: String): Preamble {
            val fields = text.trim().split(',').map { it.trim() }
            if (fields.size < 10) throw IOException("unexpected preamble: $text")
            fun number(index: Int) = fields[index].toDoubleOrNull() ?: throw IOException("unexpected preamble: $text")
            return Preamble(
                points = number(2).toLong(),
                xIncrement = number(4),
                xOrigin = number(5),
                xReference = number(6),
                yIncrement = fields[7],
                yOrigin = fields[8],
                yReference = fields[9],
            )
        }
    }
}

/**
 * Reads waveform memory from the instrument block by block (`:WAVeform:STARt/STOP/DATA?`, RAW
 * mode). [chunk] is the most points one `:WAVeform:DATA?` returns.
 *
 * An analog channel is read as BYTE, the top 8 of the 12 bits of the DHO's ADC (the cache, the
 * overviews `<BB` and the tile codec work with 8 bit analog samples, as the driver does). A
 * logic channel (`D0`–`D15`) is read as WORD, one 16 bit word per sample (0 or 1): as BYTE the
 * instrument gives only the word's upper byte, always 0. Reading them needs a patch of the
 * library of the modified firmware NK v0.5.2; without it the instrument refuses the source.
 */
class WaveformReader(private val instrument: Instrument, private val chunk: Int) {
    private var start: Long? = null
    private var stop: Long? = null

    /** The format last set on the instrument ("BYTE" or "WORD"), or null before the first one. */
    private var currentFormat: String? = null

    /** Selects a source (`CHANnel1`, `D0` …), sets the format it needs and returns its preamble,
     * unchanged (a logic channel's does not describe its own real sample period: the caller
     * corrects it, in the way its capture needs - see [LA_PERIOD_FACTOR]). */
    fun select(source: String): Preamble {
        instrument.write(":WAVeform:SOURce $source")
        if (source.startsWith("D")) {
            // A refused source leaves the one before (CHANnel1 …): its data would be taken for D<n>.
            // The DHO refuses D0–D15 without the logic probe, and with the modified firmware
            // NK v0.5.2 also with it unless its library is patched.
            val selected = instrument.query(":WAVeform:SOURce?").trim()
            if (!selected.equals(source, ignoreCase = true)) {
                throw BridgeError("the instrument does not give out $source (it kept $selected as :WAVeform:SOURce)")
            }
        }
        prepare()
        return preamble()
    }

    /**
     * RAW mode, the format of the source currently selected on the instrument, the window (STARt
     * to STOP) over the whole acquisition. The points of the preamble are those of the window,
     * and a window an earlier read left beyond this acquisition cannot be set (neither STARt nor
     * STOP): switching the mode opens it. Used directly (not through [select]) when the source
     * was already chosen, as [Bench] does.
     */
    fun prepare() {
        instrument.write(":WAVeform:MODE NORMal")
        instrument.write(":WAVeform:MODE RAW")
        setFormat(formatFor(instrument.query(":WAVeform:SOURce?").trim()))
        start = null
        stop = null
    }

    /** Switches to [source] and sets the format it needs, without resetting the window (unlike
     * [select]): for repeated switches between sources of one already opened acquisition, as
     * reading the digital channels bit by bit does. */
    fun switchSource(source: String) {
        instrument.write(":WAVeform:SOURce $source")
        setFormat(formatFor(source))
    }

    private fun formatFor(source: String) = if (source.startsWith("D")) "WORD" else "BYTE"

    private fun setFormat(format: String) {
        if (format == currentFormat) return
        instrument.write(":WAVeform:FORMat $format")
        currentFormat = format
    }

    /** Bytes one sample takes in the format currently selected. */
    private fun bytesPerPoint(): Long = if (currentFormat == "WORD") 2L else 1L

    fun preamble(): Preamble = Preamble.parse(instrument.query(":WAVeform:PREamble?"))

    /** Streams the samples [first, first + count) (0-based) of the current source into [out]. */
    fun read(first: Long, count: Long, out: OutputStream) {
        var position = first
        val end = first + count
        while (position < end) {
            val length = minOf(chunk.toLong(), end - position)
            readWindow(position, length, out)
            position += length
        }
    }

    /** One `:WAVeform:DATA?` of up to [chunk] points, in the format currently selected. */
    fun readWindow(first: Long, length: Long, out: OutputStream) {
        var received = 0L
        // The instrument says STOP a moment before the waveform is there to read: it answers
        // with an empty block ("Wave data is empty" in its log) until then.
        for (attempt in 0 until EMPTY_READ_ATTEMPTS) {
            setWindow(first + 1, first + length)
            received = instrument.queryBlock(":WAVeform:DATA?", out)
            if (received != 0L || length == 0L) break
            Thread.sleep(EMPTY_READ_RETRY_MS)
        }
        val expected = length * bytesPerPoint()
        if (received != expected) {
            throw IOException("asked for $length points from ${first + 1}, got ${received / bytesPerPoint()}")
        }
    }

    /**
     * Sets STARt and STOP (1-based, inclusive) in an order the instrument accepts: STARt may not
     * lie behind STOP.
     */
    private fun setWindow(newStart: Long, newStop: Long) {
        val oldStop = stop
        if (oldStop != null && newStart > oldStop) {
            instrument.write(":WAVeform:STOP $newStop")
            instrument.write(":WAVeform:STARt $newStart")
        } else if (oldStop != null || newStart == 1L) {
            instrument.write(":WAVeform:STARt $newStart")
            instrument.write(":WAVeform:STOP $newStop")
        } else {
            instrument.write(":WAVeform:STARt 1")
            instrument.write(":WAVeform:STOP $newStop")
            instrument.write(":WAVeform:STARt $newStart")
        }
        start = newStart
        stop = newStop
    }
}

/** Tries (and milliseconds between them) to read a window the instrument answers with an empty block. */
const val EMPTY_READ_ATTEMPTS = 40
const val EMPTY_READ_RETRY_MS = 100L

/** Reads the stopped acquisition into the cache: `:BRIDge:SNAPshot`. */
class SnapshotWriter(
    private val instrument: Instrument,
    private val cache: SnapshotCache,
    private val chunk: Int,
    private val clock: () -> Long = { System.currentTimeMillis() / 1000 },
) {
    fun take(channels: ChannelSet): Int = instrument.exclusive {
        val (id, dir) = cache.begin()
        try {
            val reader = WaveformReader(instrument, chunk)
            // Every source's own preamble: only the channels asked for (the others may be off).
            // Alone (no analog channel to line them up with), a logic channel's real sample
            // period is corrected (LA_PERIOD_FACTOR); with one, its preamble already matches the
            // analog one's and every sample is repeated instead, when the data are read, below.
            val alone = channels.analog.isEmpty()
            val digital = channels.digital.associate {
                val raw = reader.select(digitalSource(it))
                // only the first part of the points it names are the capture's (LA_PERIOD_FACTOR)
                val held = raw.points / LA_PERIOD_FACTOR
                digitalSource(it) to if (alone) {
                    raw.copy(points = held, xIncrement = raw.xIncrement * LA_PERIOD_FACTOR)
                } else {
                    raw.copy(points = held * LA_PERIOD_FACTOR)
                }
            }
            val analog = channels.analog.associateWith { reader.select(analogSource(it)) }
            val sources = digital + analog.mapKeys { "CH${it.key}" }
            val first = sources.values.first()
            checkSameTiming(sources)
            val points = sources.values.minOf { it.points }

            val meta = LinkedHashMap<String, String>()
            meta["points"] = points.toString()
            meta["rate"] = formatNumber(first.rate)
            meta["trigger"] = first.trigger.toString()
            meta["channels"] = channels.toString()
            for ((channel, preamble) in analog) {
                meta["CH$channel.yinc"] = preamble.yIncrement
                meta["CH$channel.yorig"] = preamble.yOrigin
                meta["CH$channel.yref"] = preamble.yReference
            }
            meta[Snapshot.TIME] = clock().toString()

            for (channel in channels.analog) {
                reader.switchSource(analogSource(channel))
                BufferedOutputStream(FileOutputStream(Snapshot.rawFile(dir, "CH$channel")), 1 shl 16).use {
                    reader.read(0, points, it)
                }
            }
            if (channels.digital.isNotEmpty()) {
                BufferedOutputStream(FileOutputStream(Snapshot.rawFile(dir, "D")), 1 shl 16).use {
                    readDigital(
                        reader, channels.digital, points, it, if (alone) 1 else LA_PERIOD_FACTOR,
                        if (alone) 0 else laSkew(first.rate),
                    )
                }
            }
            for (name in channels.names) {
                Pyramid.build(RawSamples(Snapshot.rawFile(dir, name), Snapshot.kindOf(name)), dir, name)
            }
            Snapshot.writeMeta(dir, meta)
            cache.commit(id, dir)
            id
        } catch (error: Exception) {
            cache.abandon(dir)
            throw error
        }
    }

    /**
     * The instrument gives every digital channel on its own, as WORD (one 16 bit word per
     * sample, 0 or 1); they are combined into one little-endian 16 bit word per sample, chunk by
     * chunk. [repeat] (1, alone, or [LA_PERIOD_FACTOR] with the analog channels) is how many
     * times each one is written, lining the logic channels up with the analog ones' finer time
     * axis; [outputPoints] is the count after repeating (at most [repeat] fewer raw samples are
     * read than it, for an [outputPoints] not a multiple of [repeat]). [skew] moves them that many
     * samples earlier (later if negative), the ends held at their level ([LA_SKEW]).
     */
    private fun readDigital(
        reader: WaveformReader, bits: List<Int>, outputPoints: Long, out: OutputStream, repeat: Int, skew: Int = 0,
    ) {
        val shifted = SkewedOutput(out, skew.toLong().coerceIn(-outputPoints, outputPoints), outputPoints)
        val rawPoints = (outputPoints + repeat - 1) / repeat
        var position = 0L
        var written = 0L
        val words = IntArray(chunk)
        val buffer = ByteArrayOutputStream(chunk * 2)
        val bytes = ByteArray(chunk * 2 * repeat)
        while (position < rawPoints) {
            val length = minOf(chunk.toLong(), rawPoints - position).toInt()
            words.fill(0, 0, length)
            for (bit in bits) {
                reader.switchSource(digitalSource(bit))
                buffer.reset()
                reader.readWindow(position, length.toLong(), buffer)
                val data = buffer.toByteArray()
                for (i in 0 until length) {
                    val value = (data[2 * i].toInt() and 0xFF) or ((data[2 * i + 1].toInt() and 0xFF) shl 8)
                    if (value != 0) words[i] = words[i] or (1 shl bit)
                }
            }
            for (i in 0 until length) {
                val lo = words[i].toByte()
                val hi = (words[i] ushr 8).toByte()
                for (r in 0 until repeat) {
                    bytes[2 * (i * repeat + r)] = lo
                    bytes[2 * (i * repeat + r) + 1] = hi
                }
            }
            val produced = minOf(length.toLong() * repeat, outputPoints - written)
            shifted.write(bytes, (produced * 2).toInt())
            written += produced
            position += length
        }
        shifted.finish()
    }

    /**
     * Writes 16 bit samples moved [skew] samples earlier (later if negative) into [out], [total] of
     * them: the first [skew] are dropped and the last one is repeated at the end, or the first one
     * is repeated [skew] times at the start and the end dropped.
     */
    internal class SkewedOutput(private val out: OutputStream, private val skew: Long, private val total: Long) {
        private var seen = 0L
        private var written = 0L
        private val first = ByteArray(2)
        private val last = ByteArray(2)

        fun write(bytes: ByteArray, length: Int) {
            var start = 0
            if (length >= 2) {
                if (seen == 0L) {
                    bytes.copyInto(first, 0, 0, 2)
                    for (i in 0 until -skew) put(first, 0, 2)
                }
                bytes.copyInto(last, 0, length - 2, length)
            }
            val samples = length / 2
            if (skew > 0 && seen < skew) start = (minOf(skew - seen, samples.toLong()) * 2).toInt()
            seen += samples
            put(bytes, start, length - start)
        }

        fun finish() {
            while (written < total) put(last, 0, 2)
        }

        private fun put(bytes: ByteArray, offset: Int, length: Int) {
            val room = ((total - written) * 2).coerceAtMost(length.toLong()).toInt()
            if (room <= 0) return
            out.write(bytes, offset, room)
            written += room / 2
        }
    }

    companion object {
        fun analogSource(channel: Int) = "CHANnel$channel"
        fun digitalSource(bit: Int) = "D$bit"

        /**
         * A snapshot has one rate and one trigger sample (META). Sources sampled otherwise (the
         * logic channels beside the analog ones) would be out of step in it: refused.
         */
        fun checkSameTiming(sources: Map<String, Preamble>) {
            val (firstName, first) = sources.entries.first()
            for ((name, preamble) in sources) {
                val rates = abs(preamble.rate - first.rate) <= 1e-6 * maxOf(preamble.rate, first.rate)
                if (rates && abs(preamble.trigger - first.trigger) <= 1) continue
                throw BridgeError(
                    "the channels were sampled at different rates or triggers " +
                        "($firstName: ${formatNumber(first.rate)} Sa/s, trigger at ${first.trigger}; " +
                        "$name: ${formatNumber(preamble.rate)} Sa/s, trigger at ${preamble.trigger}); snapshot them separately",
                )
            }
        }
    }
}

/** `:BRIDge:BENCH? <bytes>`: reads at least [bytes] of waveform data and times it. */
class Bench(private val instrument: Instrument, private val chunk: Int) {
    fun run(bytes: Long): Long = instrument.exclusive {
        val reader = WaveformReader(instrument, chunk)
        reader.prepare()
        val points = reader.preamble().points
        if (points <= 0) throw IOException("the instrument has no waveform data")
        val counter = CountingStream()
        var position = 0L
        val begin = System.nanoTime()
        while (counter.count < bytes) {
            val length = minOf(chunk.toLong(), points - position)
            reader.readWindow(position, length, counter)
            position = (position + length) % points
        }
        val elapsed = maxOf(System.nanoTime() - begin, 1)
        (counter.count.toDouble() * 1e9 / elapsed).roundToLong()
    }
}

/** A number for META: an integer when it is one, otherwise ten significant digits. */
fun formatNumber(value: Double): String {
    if (value.isNaN() || value.isInfinite()) return "0"
    val rounded = BigDecimal(value).round(MathContext(10))
    return rounded.stripTrailingZeros().let { if (it.scale() < 0) it.setScale(0) else it }.toPlainString()
}
