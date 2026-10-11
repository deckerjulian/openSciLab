package org.openscilab.bridge.core

import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.Closeable
import java.io.IOException
import java.net.ServerSocket
import java.util.concurrent.atomic.AtomicInteger

/**
 * A stand-in for the DHO's SCPI server on 5555, as far as the bridge uses it: `*IDN?`,
 * `:WAVeform:SOURce/MODE/FORMat/STARt/STOP/PREamble?/DATA?` (RAW, BYTE or WORD), plus two test
 * queries. Like the DHO924S (firmware 00.05.02) it rejects STARt behind STOP and STOP beyond the
 * acquisition, the points of its preamble are those of the window, setting the mode opens the
 * window over the acquisition, and a logic channel read as BYTE gives only its value's upper
 * byte, always 0 (its real value is a 16 bit word of 0 or 1; the bridge reads it as WORD).
 */
class FakeDho(val points: Int) : Closeable {
    private val server = ServerSocket(0)
    val port: Int get() = server.localPort
    val windowErrors = AtomicInteger()
    val dataQueries = AtomicInteger()
    val connections = AtomicInteger()
    val received = java.util.Collections.synchronizedList(ArrayList<String>())

    /** Like a DHO without the logic probe: D0–D15 are refused as :WAVeform:SOURce. */
    @Volatile
    var refuseDigital = false

    /** The preamble of the logic channels when it is not that of the analog ones (another rate). */
    @Volatile
    var digitalPreamble: String? = null

    /** The next this many `:WAVeform:DATA?` are answered with an empty block. */
    val emptyReads = AtomicInteger()

    private val clients = ArrayList<java.net.Socket>()

    /** Closes the connections of its clients, as the DHO does when another client comes or goes. */
    fun dropConnections() = synchronized(clients) {
        clients.forEach { it.close() }
        clients.clear()
    }

    private var source = "CHANNEL1"
    private var start = 1L
    private var stop = 1L
    private var format = "BYTE"

    init {
        Thread({ serve() }, "fake-dho").apply { isDaemon = true }.start()
    }

    fun analog(channel: Int, index: Int) = ((index * 7 + channel * 13) xor (index shr 5)) and 0xFF

    fun digitalBit(bit: Int, index: Int) = ((index / 3 + bit * 5) shr (bit % 4)) and 1

    fun digitalWord(index: Int, bits: List<Int>) = bits.fold(0) { word, bit -> word or (digitalBit(bit, index) shl bit) }

    private fun serve() {
        while (!server.isClosed) {
            val socket = try { server.accept() } catch (_: IOException) { return }
            connections.incrementAndGet()
            synchronized(clients) { clients.add(socket) }
            Thread {
                socket.use {
                    val reader = ProgramMessageReader(BufferedInputStream(it.getInputStream()))
                    val out = BufferedOutputStream(it.getOutputStream())
                    try {
                        while (true) {
                            val line = String(reader.next() ?: break, Charsets.ISO_8859_1).trim()
                            received.add(line)
                            answer(line, out)
                            out.flush()
                        }
                    } catch (_: IOException) {
                    } catch (error: RuntimeException) {
                        error.printStackTrace()
                    }
                }
            }.apply { isDaemon = true }.start()
        }
    }

    @Synchronized
    private fun answer(line: String, out: java.io.OutputStream) {
        val header = line.substringBefore(' ').uppercase()
        val argument = line.substringAfter(' ', "").trim()
        fun text(value: String) = out.write("$value\n".toByteArray(Charsets.ISO_8859_1))
        when (header) {
            "*IDN?" -> text("RIGOL TECHNOLOGIES,DHO924S,DHO9TEST001,00.01.02")
            ":WAVEFORM:SOURCE" -> if (!(refuseDigital && argument.uppercase().startsWith("D"))) {
                source = argument.uppercase()
            }
            ":WAVEFORM:SOURCE?" -> text(source.replace("CHANNEL", "CHAN"))
            ":WAVEFORM:MODE" -> { start = 1; stop = points.toLong() }  // (the window over the acquisition)
            ":WAVEFORM:FORMAT" -> format = argument.uppercase()
            ":WAVEFORM:STARt".uppercase() -> {
                val value = argument.toLong()
                if (value > stop) windowErrors.incrementAndGet() else start = value
            }
            ":WAVEFORM:STOP" -> {
                val value = argument.toLong()
                if (value < start || value > points) windowErrors.incrementAndGet() else stop = value
            }
            ":WAVEFORM:PREAMBLE?" -> text(
                // like the real device: a logic channel's preamble is computed as if it were
                // sampled at the analog rate (same default as the one below), its real sample
                // period being LA_PERIOD_FACTOR times that - WaveformReader corrects it, in the
                // way its capture needs.
                (digitalPreamble?.takeIf { source.startsWith("D") }
                    ?: "0,2,$points,1,1.600000E-09,-8.000000E-06,0,4.000000E-02,0,128")
                    .split(",").toMutableList().also { it[2] = (stop - start + 1).toString() }.joinToString(","),
            )
            ":WAVEFORM:DATA?" -> {
                dataQueries.incrementAndGet()
                if (emptyReads.getAndUpdate { maxOf(it - 1, 0) } > 0) {
                    // like the real device just after the capture: STOP, but no waveform yet
                    out.write(ScpiBlock.header(0))
                    out.write('\n'.code)
                    return
                }
                val first = (start - 1).toInt()
                val last = minOf(stop.toInt(), points)
                val count = maxOf(0, last - first)
                val digital = source.startsWith("D")
                val word = format == "WORD"
                val data = ByteArray(count * if (word) 2 else 1)
                for (i in 0 until count) {
                    val index = first + i
                    // like the real device, a logic channel holds its capture in the first half of
                    // the points the preamble names (LA_PERIOD_FACTOR); the rest is old memory
                    val value = if (digital) (if (index >= points / LA_PERIOD_FACTOR) 1 else digitalBit(source.removePrefix("D").toInt(), index))
                               else analog(source.removePrefix("CHANNEL").toInt(), index)
                    if (word) {
                        data[2 * i] = value.toByte()
                        data[2 * i + 1] = 0
                    } else {
                        // BYTE: the upper byte of the instrument's value; for a logic channel (a
                        // 16 bit word of 0 or 1) that is always 0.
                        data[i] = if (digital) 0 else value.toByte()
                    }
                }
                out.write(ScpiBlock.header(data.size.toLong()))
                out.write(data)
                out.write('\n'.code)
            }
            ":TEST:ECHO?" -> text(argument)
            ":TEST:BLOCK?" -> {
                out.write(ScpiBlock.header(3)); out.write(byteArrayOf(10, 0, 35)); out.write('\n'.code)
            }
            else -> if (header.endsWith("?")) text("0")
        }
    }

    override fun close() = server.close()
}
