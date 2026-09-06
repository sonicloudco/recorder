package com.soni.soni_sdk_demo;

/**
 * A/B 录音笔开关，由 productFlavor 注入 BuildConfig。
 * false = normal：普通 Opus 流
 * true  = bk：AES-256-GCM 加密流，需先解密再处理
 */
public final class BluetoothConstants {

    public static final boolean DEVICE_AES256GCM = BuildConfig.DEVICE_AES256GCM;

    private BluetoothConstants() {
    }
}
