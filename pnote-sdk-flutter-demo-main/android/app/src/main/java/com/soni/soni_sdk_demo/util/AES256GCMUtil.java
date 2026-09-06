package com.soni.soni_sdk_demo.util;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

public final class AES256GCMUtil {

    private static final String TRANSFORMATION = "AES/GCM/NoPadding";
    private static final int GCM_TAG_LEN_BITS = 128;
    private static final int SESSION_RECORD_PAYLOAD_LEN = 36;
    private static final int PACKET_PAYLOAD_LEN = 420;
    private static final int CIPHERTEXT_LEN = 400;
    private static final int IV_LEN = 12;
    private static final int NONCE_LEN = 12;

    /**
     * 文档中约定的 KEY
     */
    private static final byte[] KEY = hexToBytes(
            "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f");

    private AES256GCMUtil() {
    }

    /**
     * 从 Session Record payload 中提取 session_iv
     *
     * @param sessionRecordPayload 36 字节 session record payload
     * @return session_iv（12 字节）
     */
    public static byte[] parseSessionIv(byte[] sessionRecordPayload) {
        if (sessionRecordPayload.length != SESSION_RECORD_PAYLOAD_LEN) {
            throw new IllegalArgumentException("session record must be 36 bytes");
        }
        byte[] iv = new byte[IV_LEN];
        System.arraycopy(sessionRecordPayload, 8, iv, 0, IV_LEN);
        return iv;
    }

    /**
     * 解密一个 AES-GCM packet
     *
     * @param sessionIv     session_iv（12 字节）
     * @param packetPayload 420 字节 packet payload
     * @return 400 字节 plaintext（5 个 OPUS frame）
     */
    public static byte[] decryptPacket(byte[] sessionIv, byte[] packetPayload) {
        if (sessionIv.length != IV_LEN) {
            throw new IllegalArgumentException("sessionIv must be 12 bytes");
        }
        if (packetPayload.length != PACKET_PAYLOAD_LEN) {
            throw new IllegalArgumentException("packet payload must be 420 bytes");
        }

        ByteBuffer packet = ByteBuffer.wrap(packetPayload)
                .order(ByteOrder.LITTLE_ENDIAN);

        int seq = packet.getInt(); // seq32_le

        byte[] ciphertext = new byte[CIPHERTEXT_LEN];
        packet.get(ciphertext);

        byte[] tag = new byte[16];
        packet.get(tag);

        // nonce = session_iv[0:8] + seq32_le
        ByteBuffer nonceBuf = ByteBuffer.allocate(NONCE_LEN)
                .order(ByteOrder.LITTLE_ENDIAN);
        nonceBuf.put(sessionIv, 0, 8);
        nonceBuf.putInt(seq);
        byte[] nonce = nonceBuf.array();

        // AAD = seq32_le + uint16_le(400)
        ByteBuffer aadBuf = ByteBuffer.allocate(6)
                .order(ByteOrder.LITTLE_ENDIAN);
        aadBuf.putInt(seq);
        aadBuf.putShort((short) CIPHERTEXT_LEN);
        byte[] aad = aadBuf.array();

        try {
            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            SecretKeySpec keySpec = new SecretKeySpec(KEY, "AES");
            GCMParameterSpec spec = new GCMParameterSpec(GCM_TAG_LEN_BITS, nonce);

            cipher.init(Cipher.DECRYPT_MODE, keySpec, spec);
            cipher.updateAAD(aad);

            // ciphertext + tag
            byte[] encrypted = concat(ciphertext, tag);
            return cipher.doFinal(encrypted);
        } catch (Exception e) {
            throw new RuntimeException("AES-GCM decrypt failed, seq=" + seq, e);
        }
    }

    /**
     * 从 400B plaintext 中提取第 index 个 OPUS frame
     *
     * @param plaintext  400 字节 packet plaintext
     * @param frameIndex 0~4
     * @return 80 字节 OPUS frame
     */
    public static byte[] getOpusFrame(byte[] plaintext, int frameIndex) {
        if (plaintext.length != CIPHERTEXT_LEN) {
            throw new IllegalArgumentException("plaintext must be 400 bytes");
        }
        if (frameIndex < 0 || frameIndex > 4) {
            throw new IllegalArgumentException("frameIndex must be 0~4");
        }
        byte[] frame = new byte[80];
        System.arraycopy(plaintext, frameIndex * 80, frame, 0, 80);
        return frame;
    }

    /* ===================== 工具方法 ===================== */
    private static byte[] concat(byte[] a, byte[] b) {
        byte[] r = new byte[a.length + b.length];
        System.arraycopy(a, 0, r, 0, a.length);
        System.arraycopy(b, 0, r, a.length, b.length);
        return r;
    }

    private static byte[] hexToBytes(String hex) {
        byte[] out = new byte[hex.length() / 2];
        for (int i = 0; i < out.length; i++) {
            out[i] = (byte) Integer.parseInt(
                    hex.substring(i * 2, i * 2 + 2), 16);
        }
        return out;
    }
}
