package org.openscilab.bridge.core

import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.Socket
import java.nio.file.Files
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

/** The whole bridge on a plain JVM: client -> BridgeServer -> FakeDho. */
class BridgeServerTest {
    private val points = 10_007
    private val dho = FakeDho(points)
    private val root = Files.createTempDirectory("bridge").toFile()
    private val server = BridgeServer(
        BridgeConfig(
            version = "0.1.0", port = 0, beaconPort = 0, instrumentPort = dho.port, chunk = 1000,
            instrumentTimeoutMs = 5000,
        ),
        SnapshotCache(root),
    ).also { it.start() }
    private val socket = Socket("127.0.0.1", server.port).apply { soTimeout = 20_000 }
    private val input: InputStream = BufferedInputStream(socket.getInputStream())

    @AfterTest
    fun cleanUp() {
        socket.close()
        server.close()
        dho.close()
        root.deleteRecursively()
    }

    private fun send(message: String) {
        socket.getOutputStream().write("$message\n".toByteArray(Charsets.ISO_8859_1))
        socket.getOutputStream().flush()
    }

    /** One answer line; unlike ScpiResponse.readLine, an empty line is an answer here. */
    private fun query(message: String): String {
        send(message)
        val out = ByteArrayOutputStream()
        while (true) {
            val byte = input.read()
            if (byte < 0 || byte == '\n'.code) return out.toString("ISO-8859-1")
            out.write(byte)
        }
    }

    /** A block and the `\n` after it. */
    private fun block(message: String): ByteArray {
        send(message)
        return ScpiBlock.read(input).also { assertEquals('\n'.code, input.read()) }
    }

    @Test
    fun versionAndPassThrough() {
        assertEquals("OPENSCILAB_BRIDGE,0.1.0,1", query(":BRIDge:VERSion?"))
        assertEquals("RIGOL TECHNOLOGIES,DHO924S,DHO9TEST001,00.01.02", query("*IDN?"))
        send(":TIMebase:MAIN:SCALe 1e-3")
        assertEquals("hello", query(":TEST:ECHO? hello"))
        assertContentEquals(byteArrayOf(10, 0, 35), block(":TEST:BLOCK?"))
        assertEquals("next", query(":TEST:ECHO? next"))
        assertTrue(":TIMebase:MAIN:SCALe 1e-3" in dho.received)
        assertTrue(query(":BRIDge:NOPE?").startsWith("ERR "))
        server.updateIdentity()
        assertEquals("OPENSCILAB_BRIDGE 0.1.0 ${server.port} DHO924S DHO9TEST001", server.beaconMessage())
    }

    @Test
    fun snapshotOverviewsAndTiles() {
        val id = query(":BRIDge:SNAPshot D0-D15,CH1,CH3")
        assertEquals("1", id)
        assertEquals(0, dho.windowErrors.get())
        assertEquals(1, dho.connections.get())

        val list = query(":BRIDge:CACHe:LIST?").split(',')
        assertEquals("1", list[0])
        assertEquals("$points", list[2])

        val meta = query(":BRIDge:META? 1").split(';').associate { it.substringBefore('=') to it.substringAfter('=') }
        assertEquals("$points", meta["points"])
        assertEquals("625000000", meta["rate"])
        assertEquals("5000", meta["trigger"])
        assertEquals("D0-D15,CH1,CH3", meta["channels"])
        assertEquals("4.000000E-02", meta["CH1.yinc"])
        assertEquals("0", meta["CH3.yorig"])
        assertEquals("128", meta["CH3.yref"])

        val bits = (0..15).toList()
        val words = IntArray(points) { dho.digitalWord(it, bits) }
        val ch3 = IntArray(points) { dho.analog(3, it) }
        for (level in listOf(0, 3, 6, 9, 13, 14, 30)) {
            assertContentEquals(TestData.overview(SampleKind.DIGITAL, words, level), block(":BRIDge:OVERview? 1,D,$level"), "D $level")
            assertContentEquals(TestData.overview(SampleKind.ANALOG, ch3, level), block(":BRIDge:OVERview? 1,CH3,$level"), "CH3 $level")
        }

        assertContentEquals(words, TileCodec.decodeDigital(block(":BRIDge:TILE? 1,D,0,$points")))
        assertContentEquals(words.copyOfRange(2500, 4100), TileCodec.decodeDigital(block(":BRIDge:TILE? 1,D,2500,1600")))
        assertContentEquals(ch3.copyOfRange(9000, points), TileCodec.decodeAnalog(block(":BRIDge:TILE? 1,CH3,9000,5000")))
        assertEquals(0, block(":BRIDge:TILE? 1,CH1,$points,10").size)
        assertTrue(query(":BRIDge:TILE? 1,CH2,0,10").startsWith("ERR "))
        assertTrue(query(":BRIDge:TILE? 9,CH1,0,10").startsWith("ERR "))

        // The connection stays in sync after the blocks.
        assertEquals("hello", query(":TEST:ECHO? hello"))

        assertEquals("2", query(":BRIDge:SNAPshot CH1"))
        assertEquals(listOf("2", "1"), query(":BRIDge:CACHe:LIST?").split(';').map { it.substringBefore(',') })
        assertEquals("OK", query(":BRIDge:CACHe:DELete 1"))
        assertTrue(query(":BRIDge:CACHe:DELete 1").startsWith("ERR "))
        assertEquals("OK", query(":BRIDge:CACHe:LIMit 1000000"))
        assertEquals("OK", query(":BRIDge:CACHe:DELete ALL"))
        assertEquals("", query(":BRIDge:CACHe:LIST?"))
    }

    @Test
    fun snapshotOfSomeLogicChannels() {
        // The host asks only for the channels of its capture; the others are off on the instrument.
        assertEquals("1", query(":BRIDge:SNAPshot D0,D3,D5"))
        val meta = query(":BRIDge:META? 1").split(';').associate { it.substringBefore('=') to it.substringAfter('=') }
        assertEquals("D0,D3,D5", meta["channels"])
        assertEquals("5000", meta["trigger"])
        val words = IntArray(points) { dho.digitalWord(it, listOf(0, 3, 5)) }
        assertContentEquals(words, TileCodec.decodeDigital(block(":BRIDge:TILE? 1,D,0,$points")))
        val sources = dho.received.filter { it.startsWith(":WAVeform:SOURce") }.toSet()
        assertEquals(setOf(":WAVeform:SOURce D0", ":WAVeform:SOURce D3", ":WAVeform:SOURce D5"), sources)
    }

    @Test
    fun logicAndAnalogChannelsAtDifferentRatesAreRefused() {
        // twice the sample interval, the trigger at the same sample
        dho.digitalPreamble = "0,2,$points,1,3.200000E-09,-1.600000E-05,0,1,0,0"
        val answer = query(":BRIDge:SNAPshot D0-D3,CH1")
        assertTrue(answer.startsWith("ERR the channels were sampled at different rates"), answer)
        assertTrue("D0: 312500000 Sa/s" in answer && "CH1: 625000000 Sa/s" in answer, answer)
        assertEquals("", query(":BRIDge:CACHe:LIST?"))
        assertTrue(query(":BRIDge:SNAPshot D0-D3").toIntOrNull() != null)  // one kind alone is fine
        assertEquals("hello", query(":TEST:ECHO? hello"))
    }

    @Test
    fun aDamagedOverviewIsAnErrorBeforeItsBlock() {
        assertEquals("1", query(":BRIDge:SNAPshot CH1"))
        assertTrue(Pyramid.levelFile(java.io.File(root, "1"), "CH1", 9).delete())
        assertTrue(query(":BRIDge:OVERview? 1,CH1,9").startsWith("ERR "))
        assertEquals("hello", query(":TEST:ECHO? hello"))  // the connection is still in step
    }

    @Test
    fun aBlockThatBreaksOffIsNoErrLine() {
        val out = ByteArrayOutputStream()
        assertFailsWith<BlockAborted> { writeBlock(out, 10) { it.write(ByteArray(4)); throw IOException("disk gone") } }
        assertEquals(8, out.size())  // the header and what came: no ERR inside the block
        assertFailsWith<BlockAborted> { writeBlock(ByteArrayOutputStream(), 10) { it.write(ByteArray(9)) } }
        val good = ByteArrayOutputStream()
        writeBlock(good, 3) { it.write(byteArrayOf(1, 2, 3)) }
        assertContentEquals("#13".toByteArray() + byteArrayOf(1, 2, 3, '\n'.code.toByte()), good.toByteArray())

        // The handler passes it on, so the session ends the connection.
        assertEquals("1", query(":BRIDge:SNAPshot CH1"))
        val failing = object : OutputStream() {
            var written = 0
            override fun write(b: Int) {
                if (++written > 6) throw IOException("the client went away")
            }
        }
        assertFailsWith<BlockAborted> { server.handler.handle(BridgeCommand.Tile(1, "CH1", 0, 100), failing) }
    }

    @Test
    fun bench() {
        val before = dho.dataQueries.get()
        val rate = query(":BRIDge:BENCH? 25000").toLong()
        assertTrue(rate > 0)
        // 10 windows of 1000 points and one of 7, then from the start again
        assertEquals(27, dho.dataQueries.get() - before)
        assertEquals(0, dho.windowErrors.get())
    }

    @Test
    fun instrumentAway() {
        // The bridge first, then the instrument away: Linux hands the port just closed to the next
        // socket bound to port 0, so a bridge started later could listen on the instrument's port
        // and pass the commands to itself.
        val away = BridgeServer(BridgeConfig(version = "0.1.0", port = 0, beaconPort = 0, instrumentPort = dho.port, instrumentTimeoutMs = 500), SnapshotCache(root))
        away.start()
        dho.close()
        Socket("127.0.0.1", away.port).use { client ->
            client.soTimeout = 5000
            client.getOutputStream().write("*IDN?\n:BRIDge:VERSion?\n".toByteArray())
            val reader = BufferedInputStream(client.getInputStream())
            assertTrue(ScpiResponse.readLine(reader).startsWith("ERR instrument"))
            assertEquals("OPENSCILAB_BRIDGE,0.1.0,1", ScpiResponse.readLine(reader))
        }
        away.close()
    }
}
